"""Required-check regressions using GitHub's actual needs/result/output shape."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import ci_gate


def completed(selected=True):
    jobs = ('engine', 'onnx', 'cuda', 'board')
    return {
        'plan': {'result': 'success', 'outputs': dict.fromkeys(jobs, str(selected).lower())},
        'general': {'result': 'success', 'outputs': {}},
        **{job: {'result': 'success' if selected else 'skipped', 'outputs': {}} for job in jobs},
    }


class GateTests(unittest.TestCase):
    def test_full_success_and_documentation_only_skips_pass(self):
        ci_gate.validate(completed())
        ci_gate.validate(completed(False))

    def test_each_required_job_failure_cancellation_or_skip_fails(self):
        for job in completed():
            for result in ('failure', 'cancelled', 'skipped', None):
                with self.subTest(job=job, result=result):
                    needs = completed()
                    needs[job]['result'] = result
                    with self.assertRaises(ValueError):
                        ci_gate.validate(needs)

    def test_only_explicitly_unselected_jobs_may_be_skipped(self):
        for job in ('engine', 'onnx', 'cuda', 'board'):
            with self.subTest(job=job):
                needs = completed()
                needs['plan']['outputs'][job] = 'false'
                needs[job]['result'] = 'skipped'
                ci_gate.validate(needs)
                for result in ('failure', 'cancelled'):
                    needs[job]['result'] = result
                    with self.assertRaises(ValueError):
                        ci_gate.validate(needs)

    def test_missing_or_invalid_scope_outputs_fail_even_when_jobs_succeed(self):
        for job in ('engine', 'onnx', 'cuda', 'board'):
            for value in (None, '', True, 'yes'):
                with self.subTest(job=job, value=value):
                    needs = completed()
                    needs['plan']['outputs'][job] = value
                    with self.assertRaises(ValueError):
                        ci_gate.validate(needs)
        for value in (None, [], ''):
            needs = completed()
            needs['plan']['outputs'] = value
            with self.assertRaises(ValueError):
                ci_gate.validate(needs)

    def test_missing_unexpected_and_malformed_results_fail(self):
        for job in completed():
            needs = completed()
            del needs[job]
            with self.assertRaises(ValueError):
                ci_gate.validate(needs)
        invalid = completed()
        invalid['untracked-job'] = {'result': 'failure'}
        for needs in (invalid, [], None, {'plan': 'success'}):
            with self.assertRaises(ValueError):
                ci_gate.validate(needs)

    def test_cli_exit_code_enforces_checks_even_with_optimized_python(self):
        valid = completed(False)
        invalid = copy.deepcopy(valid)
        invalid['plan']['result'] = 'failure'
        for flags in ([], ['-O']):
            for needs, code in ((valid, 0), (invalid, 1)):
                with self.subTest(flags=flags, code=code):
                    result = subprocess.run(
                        [sys.executable, '-B', *flags, str(Path(ci_gate.__file__).resolve())],
                        env={**os.environ, 'CI_NEEDS': json.dumps(needs)},
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual('All selected CI checks passed' in result.stdout, code == 0)


if __name__ == '__main__':
    unittest.main()
