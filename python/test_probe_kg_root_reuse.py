#!/usr/bin/env python3
"""Tests for probe failure handling; real engine checks stay in the native CI matrix."""
from contextlib import contextmanager
import os
import tempfile
from pathlib import Path
import subprocess
import sys
import threading
import time
import tracemalloc
import unittest
from unittest.mock import Mock, patch

import probe_kg_root_reuse as probe_module


class ProbeLifecycleTests(unittest.TestCase):
    def test_existing_proof_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            previous = output / 'result.json'
            previous.write_text('{"status":"PASS","version":"previous"}', encoding='utf-8')
            with patch.object(probe_module, 'Probe') as create_probe:
                with self.assertRaises(FileExistsError):
                    probe_module.run(Path('unused-engine'), Path('unused-model'), output)
                create_probe.assert_not_called()
            self.assertEqual(previous.read_text(encoding='utf-8'),
                             '{"status":"PASS","version":"previous"}')

    def test_failed_search_preserves_logs_but_never_publishes_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = root / 'engine'
            engine.write_bytes(b'identity only; process is stubbed in this lifecycle unit test')
            model = root / 'model'
            model.write_bytes(b'model')
            instance = Mock()
            instance.command.side_effect = AssertionError('injected search failure')
            instance.close.return_value = True
            def create_probe(*args, transcript, stderr_log):
                transcript.write('failed command\n')
                stderr_log.write('diagnostic\n')
                return instance

            output = root / 'proof'
            with patch.object(probe_module, 'Probe', side_effect=create_probe):
                with self.assertRaisesRegex(AssertionError, 'injected search failure'):
                    probe_module.run(engine, model, output)
            instance.close.assert_called_once_with()
            self.assertFalse((output / 'result.json').exists())
            self.assertEqual((output / 'gtp.log').read_text(), 'failed command\n')
            self.assertEqual((output / 'stderr.log').read_text(), 'diagnostic\n')
            self.assertTrue((output / 'gtp.cfg').exists())

    def make_closing_probe(self):
        probe = probe_module.Probe.__new__(probe_module.Probe)
        probe.process = Mock()
        probe.process.poll.return_value = None
        probe.process.returncode = 0
        probe.command = Mock()
        probe.reader = Mock()
        probe.err_reader = Mock()
        probe.reader.is_alive.return_value = False
        probe.err_reader.is_alive.return_value = False
        probe.stop_queuing = threading.Event()
        probe.transcript = Mock()
        probe.stderr_log = Mock()
        probe.stdout_error = probe.stderr_error = None
        return probe

    def test_acknowledged_zero_exit_is_clean(self):
        probe = self.make_closing_probe()
        self.assertTrue(probe.close())
        probe.command.assert_called_once_with('quit', timeout=5)
        probe.process.terminate.assert_not_called()
        probe.process.stdout.close.assert_called_once_with()

    def test_failed_quit_or_nonzero_exit_cannot_pass(self):
        for failure in ('quit', 'exit', 'already-exited', 'reader'):
            with self.subTest(failure=failure):
                probe = self.make_closing_probe()
                if failure == 'quit':
                    probe.command.side_effect = AssertionError('bad quit acknowledgement')
                elif failure == 'exit':
                    probe.process.returncode = 1
                elif failure == 'already-exited':
                    probe.process.poll.return_value = 0
                else:
                    probe.reader.is_alive.return_value = True
                self.assertFalse(probe.close())

    def test_forced_kill_cannot_pass(self):
        probe = self.make_closing_probe()
        probe.process.wait.side_effect = [subprocess.TimeoutExpired('engine', 5), 0]
        self.assertFalse(probe.close())
        probe.process.kill.assert_called_once_with()


class FinalReportTests(unittest.TestCase):
    def run_search(self, final, **restrictions):
        probe = probe_module.Probe.__new__(probe_module.Probe)
        probe.player = 'B'
        probe.ownership = False
        probe.last = None
        before = {'visits': 100, 'moves': {'D4': 99}}

        def command(text):
            if text == 'name':
                probe.last = final
            return ''

        def line(deadline):
            probe.last = before
            return 'info'

        probe.command = command
        probe.line = line
        return probe.search('test-mask', 100, **restrictions)

    def test_barrier_report_is_checked_for_allowed_and_forbidden_moves(self):
        invalid = {'visits': 101, 'moves': {'F6': 100}}
        for restrictions in ({'allowed': {'D4'}}, {'forbidden': {'F6'}}):
            with self.subTest(restrictions=restrictions):
                with self.assertRaises(AssertionError):
                    self.run_search(invalid, **restrictions)

    def test_barrier_report_cannot_lose_visits(self):
        with self.assertRaises(AssertionError):
            self.run_search({'visits': 99, 'moves': {'D4': 98}})

    def test_valid_final_report_is_returned(self):
        result = self.run_search({'visits': 105, 'moves': {'D4': 104}}, allowed={'D4'})
        self.assertEqual(result['visits'], 105)
        self.assertEqual(result['firstVisits'], 100)


class OptimizationTests(unittest.TestCase):
    def test_optimized_cli_and_import_fail_before_side_effects(self):
        script = Path(probe_module.__file__).resolve()
        # Both the CLI and imported API must reject assertions-disabled execution.
        for flags, setting in ((['-O'], None), (['-OO'], None), ([], '1'), ([], '2')):
            for entry in ('cli', 'import'):
                with self.subTest(flags=flags, setting=setting, entry=entry), tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / 'proof'
                    env = dict(os.environ)
                    env.pop('PYTHONOPTIMIZE', None)
                    if setting is not None:
                        env['PYTHONOPTIMIZE'] = setting
                    arguments = ([str(script), '--engine', 'unused', '--model', 'unused', '--output', str(output)]
                                 if entry == 'cli' else
                                 ['-c', f'import runpy; runpy.run_path({str(script)!r})'])
                    result = subprocess.run([sys.executable, '-S', *flags, '-B', *arguments], env=env,
                                            capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('checks require assertions', result.stderr)
                    self.assertNotIn('KG-next root tree reuse: PASS', result.stdout)
                    self.assertFalse(output.exists())


class StreamingTests(unittest.TestCase):
    @contextmanager
    def producer(self, body):
        """Use real pipes/threads, replacing only the engine with a small Python producer."""
        popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with ((root / 'gtp.log').open('w', encoding='utf-8') as transcript,
                  (root / 'stderr.log').open('w', encoding='utf-8') as stderr_log):
                def launch(_args, **kwargs):
                    return popen([sys.executable, '-S', '-u', '-c', body], **kwargs)

                with patch.object(probe_module.subprocess, 'Popen', side_effect=launch):
                    probe = probe_module.Probe(Path('producer'), Path('model'), Path('config'), 'B', False,
                                               transcript=transcript, stderr_log=stderr_log)
                try:
                    yield probe, root
                finally:
                    # Test failures must not leave the producer or a backpressured reader alive.
                    probe.stop_queuing.set()
                    if probe.process.poll() is None:
                        probe.process.kill()
                    probe.process.wait(timeout=5)
                    probe.reader.join(timeout=5)
                    probe.err_reader.join(timeout=5)
                    for stream in (probe.process.stdin, probe.process.stdout, probe.process.stderr):
                        stream.close()
                    self.assertFalse(probe.reader.is_alive())
                    self.assertFalse(probe.err_reader.is_alive())

    def test_large_transcript_and_stderr_are_complete_with_bounded_memory(self):
        count, width = 4096, 4096
        body = f'''
import sys
for request in sys.stdin:
    serial, command = request.strip().split(' ', 1)
    if command == 'quit':
        print('=' + serial + '\\n', flush=True)
        break
    for i in range({count}):
        print('out' + str(i) + ':' + 'x' * {width})
        print('err' + str(i) + ':' + 'y' * {width}, file=sys.stderr)
    print('=' + serial + ' ready\\n', flush=True)
'''
        tracemalloc.start()
        try:
            with self.producer(body) as (probe, root):
                self.assertEqual(probe.command('name', timeout=30), 'ready')
                self.assertTrue(probe.close())
                _, peak = tracemalloc.get_traced_memory()
                # More than 32 MiB are logged. The probe must not retain either stream.
                self.assertLess(peak, 8 * 1024 * 1024)
                self.assertEqual(probe.lines.maxsize, probe_module.MAX_QUEUED_LINES)
                self.assertLessEqual(len(probe.stderr), probe_module.STDERR_TAIL_LINES)
                self.assertTrue(all(len(line) <= probe_module.STDERR_TAIL_CHARS for line in probe.stderr))
                for name, prefix in (('gtp.log', '< out'), ('stderr.log', 'err')):
                    with (root / name).open(encoding='utf-8') as stream:
                        lines = (line for line in stream if line.startswith(prefix))
                        for index in range(count):
                            line = next(lines)
                            self.assertTrue(line.startswith(f'{prefix}{index}:'))
                            self.assertEqual(len(line.split(':', 1)[1].rstrip('\n')), width)
                        self.assertIsNone(next(lines, None))
        finally:
            tracemalloc.stop()

    def test_quit_drains_output_beyond_queue_capacity_without_killing(self):
        body = '''
import sys
for i in range(64):
    print('queued:' + str(i), flush=True)
for request in sys.stdin:
    serial, command = request.strip().split(' ', 1)
    print('=' + serial + '\\n', flush=True)
    if command == 'quit':
        for i in range(2048):
            print('shutdown:' + str(i) + ':' + 'x' * 4096)
        print('final diagnostic', file=sys.stderr)
        break
'''
        with self.producer(body) as (probe, root):
            deadline = time.monotonic() + 5
            while probe.lines.qsize() < probe_module.MAX_QUEUED_LINES and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(probe.lines.qsize(), probe_module.MAX_QUEUED_LINES)
            self.assertTrue(probe.close())
            self.assertEqual(probe.process.returncode, 0)
            self.assertLessEqual(probe.lines.qsize(), probe_module.MAX_QUEUED_LINES)
            with (root / 'gtp.log').open() as stream:
                self.assertEqual(sum(line.startswith('< shutdown:') for line in stream), 2048)
            self.assertIn('final diagnostic', (root / 'stderr.log').read_text())

    def test_invalid_utf8_and_oversize_lines_cannot_be_silent_reader_exits(self):
        for channel in ('stdout', 'stderr'):
            for payload in ("b'\\xff\\n'", f"b'x' * {probe_module.MAX_LINE_CHARS + 1} + b'\\n'"):
                with self.subTest(channel=channel, payload=payload):
                    body = f'''
import sys
sys.stdin.readline()
sys.{channel}.buffer.write({payload})
sys.{channel}.buffer.flush()
sys.stdin.read()
'''
                    with self.producer(body) as (probe, _):
                        with self.assertRaisesRegex(AssertionError, channel + ' capture failed'):
                            probe.command('name', timeout=5)
                        with self.assertRaisesRegex(AssertionError, channel + ' capture failed'):
                            probe.close()

    def test_log_write_errors_propagate_and_cleanup_readers(self):
        for channel in ('stdout', 'stderr'):
            with self.subTest(channel=channel):
                body = f'''
import sys
sys.stdin.readline()
print('diagnostic', file=sys.{channel}, flush=True)
sys.stdin.read()
'''
                with self.producer(body) as (probe, _):
                    sink = probe.transcript if channel == 'stdout' else probe.stderr_log
                    write = sink.write

                    def fail_received(line):
                        if line.startswith('> '):
                            return write(line)
                        raise OSError('injected disk write failure')

                    with patch.object(sink, 'write', side_effect=fail_received):
                        with self.assertRaisesRegex(AssertionError, 'injected disk write failure'):
                            probe.command('name', timeout=5)
                        with self.assertRaisesRegex(AssertionError, 'injected disk write failure'):
                            probe.close()

    def test_buffered_log_flush_failure_is_not_a_clean_exit(self):
        body = "import sys\ns = sys.stdin.readline().split()[0]\nprint('=' + s + '\\n', flush=True)\n"
        for channel in ('stdout', 'stderr'):
            with self.subTest(channel=channel), self.producer(body) as (probe, _):
                sink = probe.transcript if channel == 'stdout' else probe.stderr_log
                with patch.object(sink, 'flush', side_effect=OSError('injected flush failure')):
                    with self.assertRaisesRegex(OSError, 'injected flush failure'):
                        probe.close()

    def test_unterminated_multiline_response_has_a_memory_limit(self):
        body = '''
import sys
s = sys.stdin.readline().split()[0]
print('=' + s, flush=True)
for i in range(2048):
    print('x' * 4096)
sys.stdin.read()
'''
        with self.producer(body) as (probe, _):
            with self.assertRaisesRegex(AssertionError, 'response exceeds capture limit'):
                probe.command('name', timeout=5)

    def test_streamed_and_stop_barrier_violations_still_fail(self):
        for stage in ('streamed', 'barrier'):
            with self.subTest(stage=stage):
                bad = 'info move F6 visits 99 rootInfo visits 100'
                good = 'info move D4 visits 99 rootInfo visits 100'
                body = f'''
import sys
for request in sys.stdin:
    serial, command = request.strip().split(' ', 1)
    if command.startswith('kata-analyze'):
        print('=' + serial + '\\n', flush=True)
        print({bad if stage == 'streamed' else good!r}, flush=True)
    else:
        if command == 'name':
            print({bad!r}, flush=True)
        print('=' + serial + '\\n', flush=True)
        if command == 'quit':
            break
'''
                with self.producer(body) as (probe, root):
                    with self.assertRaises(AssertionError):
                        probe.search('', 100, allowed={'D4'})
                    self.assertTrue(probe.close())
                    self.assertIn(bad, (root / 'gtp.log').read_text())


if __name__ == '__main__':
    unittest.main()
