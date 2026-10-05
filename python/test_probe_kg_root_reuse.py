#!/usr/bin/env python3
"""Tests for probe failure handling; real engine checks stay in the native CI matrix."""
import tempfile
from pathlib import Path
import subprocess
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
            instance.transcript = ['failed command']
            instance.stderr = ['diagnostic']
            output = root / 'proof'
            with patch.object(probe_module, 'Probe', return_value=instance):
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


if __name__ == '__main__':
    unittest.main()
