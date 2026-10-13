"""Check the Fox verifier's assertion-dependent entry points with real subprocesses."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('test_fox_sgf_filter.py').resolve()


class OptimizationTests(unittest.TestCase):
    def run_verifier(self, *, flags=(), setting=None, entry='cli', help_only=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'proof'
            model = root / 'model.bin'
            model.write_bytes(b'fixture')
            # Python acts as the engine and receives this filename as its first argument.
            # Wrong empty output must never earn PASS, even with assertions disabled.
            (root / 'writetrainingdata').write_text(
                "from pathlib import Path\nimport sys\n"
                "Path('engine-started').write_text('started')\n"
                "output = Path(sys.argv[sys.argv.index('-output-dir') + 1])\n"
                "output.mkdir(parents=True)\n"
                "(output / 'log.log').write_text('Counts by done\\nUsed: 0\\n==========\\nTotal rows: 0\\n')\n",
                encoding='utf-8',
            )
            env = dict(os.environ)
            env.pop('PYTHONOPTIMIZE', None)
            if setting is not None:
                env['PYTHONOPTIMIZE'] = setting
            if entry == 'import':
                arguments = ['-c', f'import sys; sys.path.insert(0, {str(SCRIPT.parent)!r}); import test_fox_sgf_filter']
            elif help_only:
                arguments = [str(SCRIPT), '--help']
            else:
                arguments = [str(SCRIPT), '--engine', sys.executable, '--model', str(model),
                             '--output', str(output)]
            result = subprocess.run([sys.executable, '-S', '-B', *flags, *arguments], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=10)
            return (result, output.exists(), (root / 'engine-started').exists(),
                    (output / 'result.json').exists())

    def test_optimized_cli_and_import_fail_before_side_effects(self):
        for flags, setting in ((['-O'], None), (['-OO'], None), ([], '1'), ([], '2')):
            for entry in ('cli', 'import'):
                with self.subTest(flags=flags, setting=setting, entry=entry):
                    result, output_created, engine_started, receipt_written = self.run_verifier(
                        flags=flags, setting=setting, entry=entry)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('checks require assertions', result.stderr)
                    self.assertFalse(output_created)
                    self.assertFalse(engine_started)
                    self.assertFalse(receipt_written)

    def test_normal_cli_help_and_import_remain_available(self):
        for setting in (None, '0'):
            for entry in ('cli', 'import'):
                with self.subTest(setting=setting, entry=entry):
                    result, output_created, engine_started, receipt_written = self.run_verifier(
                        setting=setting, entry=entry, help_only=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(output_created)
                    self.assertFalse(engine_started)
                    self.assertFalse(receipt_written)

    def test_normal_mode_rejects_incorrect_empty_engine_output(self):
        result, output_created, engine_started, receipt_written = self.run_verifier()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('completion counts', result.stderr)
        self.assertTrue(output_created)
        self.assertTrue(engine_started)
        self.assertFalse(receipt_written)


if __name__ == '__main__':
    unittest.main()
