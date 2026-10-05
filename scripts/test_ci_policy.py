"""Small structural contracts for cost controls that must not bypass validation.

PyYAML is pinned in the lightweight plan job. BaseLoader avoids YAML 1.1 treating
GitHub's `on` key as a boolean. This is not a replacement for actionlint or CI.
"""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return yaml.load((ROOT / path).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


class PolicyTests(unittest.TestCase):
    def test_only_superseded_pr_runs_share_a_cancellation_group(self):
        for name in ('build', 'kg-root-reuse', 'windows-cuda-bundles', 'onnx-backend'):
            with self.subTest(workflow=name):
                concurrency = load(f'.github/workflows/{name}.yml')['concurrency']
                group = concurrency['group']
                self.assertIn('github.workflow', group)
                self.assertIn('github.event_name', group)
                self.assertIn('github.event.pull_request.number || github.run_id', group)
                self.assertEqual(concurrency['cancel-in-progress'], "${{ github.event_name == 'pull_request' }}")

    def test_general_workflow_stays_visible_and_gates_only_native_jobs(self):
        workflow = load('.github/workflows/build.yml')
        for event in ('push', 'pull_request'):
            self.assertNotIn('paths', workflow['on'][event])
            self.assertNotIn('paths-ignore', workflow['on'][event])
        jobs = workflow['jobs']
        self.assertEqual(len([name for name in jobs if name.startswith('build-')]), 6)
        plan = jobs['plan']
        self.assertEqual(plan['steps'][0]['with']['fetch-depth'], '0')
        self.assertTrue(any(step.get('run') == 'python3 -B scripts/ci_scope.py' for step in plan['steps']))
        for name, job in jobs.items():
            if name.startswith('build-'):
                self.assertEqual(job['needs'], 'plan')
                self.assertEqual(job['if'], "${{ !cancelled() && (needs.plan.result != 'success' || needs.plan.outputs.native != 'false') }}")
                test_steps = [step for step in job['steps'] if step.get('name') == 'Run tests']
                self.assertEqual(len(test_steps), 1)
                self.assertNotIn('if', test_steps[0])

    def test_compiler_cache_does_not_restore_cmake_state_or_skip_tests(self):
        cache = load('.github/actions/ccache/action.yml')
        restore = next(step for step in cache['runs']['steps'] if step.get('uses', '').startswith('actions/cache@'))
        self.assertEqual(restore['with']['path'], '${{ runner.temp }}/katago-ccache')
        for token in ('runner.os', 'runner.arch', 'inputs.backend', 'toolchain.outputs.identity'):
            self.assertIn(token, restore['with']['key'])
            self.assertIn(token, restore['with']['restore-keys'])
        self.assertRegex(restore['uses'], r'@\w{40}$')
        self.assertIn('CCACHE_COMPILERCHECK=content', cache['runs']['steps'][0]['run'])
        self.assertIn('CCACHE_MAXSIZE=256M', cache['runs']['steps'][0]['run'])
        build = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertNotIn('cpp/CMakeCache.txt', build)
        self.assertNotIn('cpp/CMakeFiles', build)
        self.assertNotIn('cpp/.ninja_deps', build)
        root = load('.github/workflows/kg-root-reuse.yml')
        self.assertIn('.github/actions/ccache/**', root['on']['pull_request']['paths'])
        checks = [s for s in root['jobs']['native-cpu']['steps'] if s.get('name') == 'Check native invariants']
        self.assertIn('runoutputtests', checks[0]['run'])
        self.assertNotIn('if', checks[0])

    def test_cuda_cache_is_exact_profile_lock_and_builder_is_unconditional(self):
        workflow = load('.github/workflows/windows-cuda-bundles.yml')
        job = workflow['jobs']['bundle']
        matrix = job['strategy']['matrix']['include']
        self.assertEqual({row['lockfile'] for row in matrix},
                         {'scripts/windows_cuda12.lock.json', 'scripts/windows_cuda13.lock.json'})
        for row in matrix:
            self.assertTrue((ROOT / row['lockfile']).is_file())
        step = next(s for s in job['steps'] if s.get('name') == 'Cache hash-locked source downloads')
        self.assertEqual(step['with']['path'], '${{ runner.temp }}/cuda-downloads')
        self.assertNotIn('restore-keys', step['with'])
        self.assertIn('matrix.target', step['with']['key'])
        self.assertIn('hashFiles(matrix.lockfile)', step['with']['key'])
        builder = next(s for s in job['steps'] if s.get('name', '').startswith('Build, inspect'))
        self.assertNotIn('if', builder)
        self.assertIn('--source-sha $env:SOURCE_SHA', builder['run'])
        self.assertIn('--cache "$env:RUNNER_TEMP/cuda-downloads"', builder['run'])

    def test_vcpkg_uses_binary_archives_not_installed_tree(self):
        job = load('.github/workflows/build.yml')['jobs']['build-windows']
        steps = job['steps']
        cache = next(s for s in steps if s.get('name') == 'Cache vcpkg binary archives')
        self.assertEqual(cache['with']['path'], '${{ runner.temp }}/vcpkg-binary-cache')
        self.assertNotIn('restore-keys', cache['with'])
        install = next(s for s in steps if s.get('name') == 'Install vcpkg dependencies')
        self.assertNotIn('if', install)
        self.assertIn('vcpkg install', install['run'])

    def test_expensive_onnx_builds_require_explicit_opt_in(self):
        workflow = load('.github/workflows/onnx-backend.yml')
        self.assertEqual(workflow['on']['workflow_dispatch']['inputs']['include_slow']['default'], 'false')
        self.assertNotIn('if', workflow['jobs']['build-fast'])
        for name in ('build-slow', 'build-tensorrt'):
            self.assertIn('inputs.include_slow', workflow['jobs'][name]['if'])
            self.assertIn("github.event_name == 'workflow_dispatch'", workflow['jobs'][name]['if'])

    def test_fork_entrypoint_links_exist(self):
        readme = (ROOT / 'README.md').read_text()
        intro = readme.split('<!-- END KG-NEXT FORK ENTRY -->')[0]
        self.assertIn('PENDING_HARDWARE', intro)
        self.assertIn('docs/KG_CI.md', intro)
        for target in re.findall(r'\]\(([^)]+)\)', intro):
            if not target.startswith(('https://', '#')):
                self.assertTrue((ROOT / target.split('#')[0]).is_file(), target)


if __name__ == '__main__':
    unittest.main()
