"""Exercise scope selection against real Git history, including deletion and rename."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import ci_scope


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('README.md', 'base\n')
        self.write('cpp/main.cpp', 'int main() {}\n')
        self.base = self.commit()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode().strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture')
        return self.git('rev-parse', 'HEAD')

    def selected(self, head, event_name='pull_request', base=None):
        base = base or self.base
        event = {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}},
                 'before': base, 'after': head}
        return ci_scope.needs_native(event_name, event, self.root)[0]

    def test_docs_only_pr_and_push_skip_native(self):
        self.write('README.md', 'new entry\n')
        self.write('docs/KG_CI.md', 'cache rules\n')
        head = self.commit()
        for event in ('pull_request', 'push'):
            self.assertFalse(self.selected(head, event))

    def test_native_edit_and_deletion_require_builds(self):
        self.write('cpp/main.cpp', 'int main() { return 1; }\n')
        self.assertTrue(self.selected(self.commit()))
        (self.root / 'cpp/main.cpp').unlink()
        self.assertTrue(self.selected(self.commit()))

    def test_rename_from_code_to_docs_still_requires_builds(self):
        self.git('mv', 'cpp/main.cpp', 'README2.md')
        self.assertTrue(self.selected(self.commit()))

    def test_unknown_ci_and_config_files_are_not_docs(self):
        for name in ('.github/workflows/build.yml', '.github/actions/ccache/action.yml',
                     'docs/example.cfg', 'scripts/ci_scope.py', 'unknown.txt'):
            with self.subTest(name=name):
                self.assertFalse(ci_scope.documentation_only([name]))

    def test_empty_invalid_and_missing_history_keep_builds(self):
        self.assertTrue(self.selected(self.base))
        self.assertTrue(self.selected(self.base, base='0' * 40))
        self.assertTrue(self.selected(self.base, base='f' * 40))
        self.assertTrue(self.selected(self.base, base='--help'))
        self.assertTrue(ci_scope.needs_native('push', {}, self.root)[0])
        self.assertTrue(ci_scope.needs_native('push', [], self.root)[0])
        self.assertTrue(ci_scope.needs_native('workflow_dispatch', {}, self.root)[0])

    def test_large_diff_does_not_truncate_after_300_paths(self):
        for i in range(310):
            self.write(f'docs/{i:03d}.md', 'doc\n')
        self.write('cpp/main.cpp', 'new code\n')
        self.assertTrue(self.selected(self.commit()))

    def test_nul_delimited_unusual_doc_name(self):
        self.write('docs/name\nwith newline.md', 'doc\n')
        self.assertFalse(self.selected(self.commit()))

    def test_real_cli_writes_selection_for_a_documentation_change(self):
        self.write('README.md', 'changed\n')
        head = self.commit()
        event_file = self.root / 'event.json'
        event_file.write_text(json.dumps({'before': self.base, 'after': head}))
        output = self.root / 'output.txt'
        env = {**os.environ, 'GITHUB_EVENT_NAME': 'push', 'GITHUB_EVENT_PATH': str(event_file),
               'GITHUB_OUTPUT': str(output)}
        subprocess.run([os.sys.executable, str(Path(ci_scope.__file__).resolve())],
                       cwd=self.root, env=env, check=True, stdout=subprocess.PIPE)
        self.assertEqual(output.read_text(), 'native=false\n')

    def test_pr_merge_base_avoids_unrelated_base_branch_changes(self):
        self.git('checkout', '-qb', 'feature')
        self.write('README.md', 'feature docs\n')
        head = self.commit()
        self.git('checkout', '--detach', self.base)
        self.write('cpp/another.cpp', 'unrelated base code\n')
        advanced_base = self.commit()
        self.assertFalse(self.selected(head, base=advanced_base))


if __name__ == '__main__':
    unittest.main()
