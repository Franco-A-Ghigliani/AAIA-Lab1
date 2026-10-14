"""Required runtime argument, file-boundary and permission checks."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from student_agent.runtime import Runtime


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source / 'a.py').write_text('value = 1\n')
        (self.source / 'untracked.txt').write_text('private')
        self.runtime = Runtime(self.source, mode="edit")
        self.addCleanup(self.runtime.close)

    def call(self, tool, **args):
        return self.runtime.execute({'tool': tool, 'args': args})

    def test_argument_validation(self):
        for action in [None, {}, {'tool': [], 'args': {}}, {'tool': 'push', 'args': {}},
                       {'tool': 'read_file', 'args': {'path': 42}},
                       {'tool': 'read_file', 'args': {'path': 'a', 'extra': 'b'}},
                       {'tool': 'bash', 'args': {'command': 'x' * 12001}}]:
            with self.subTest(action=action):
                self.assertEqual(self.runtime.execute(action)['status'], 'error')

    def test_paths(self):
        for path in ['../x', '/tmp/x', 'a/../../x', '.git/config', '', '.env',
                     'a/.git/config', 'a\\..\\x', 'C:foo', 'a\0b']:
            with self.subTest(path=path):
                self.assertEqual(self.call('write_file', path=path, content='x')['status'], 'denied')

    def test_symlinks(self):
        try:
            (self.runtime.root / 'link').symlink_to(self.source / 'untracked.txt')
        except OSError as error:
            self.skipTest(f'symlinks unavailable: {error}')
        self.assertEqual(self.call('read_file', path='link')['status'], 'denied')
        self.assertEqual(self.call('list_files')['output'], ['a.py', 'untracked.txt'])

    def test_file_workflow(self):
        self.assertEqual(self.call('write_file', path='notes/a', content='hello')['status'], 'ok')
        self.assertEqual(self.call('search_files', query='hello')['output'][0]['path'], 'notes/a')
        self.assertEqual(self.call('edit_file', path='a.py', old='missing', new='2')['status'], 'error')
        self.assertEqual(self.call('read_file', path='a.py')['output'], 'value = 1\n')

    def test_bash_permissions(self):
        with mock.patch.object(self.runtime.sandbox, 'run', return_value={'exit_code': 3}) as run:
            self.assertEqual(self.call('bash', command='exit 3')['status'], 'denied')
            self.runtime.approve = mock.Mock(return_value='yes')
            self.assertEqual(self.call('bash', command='exit 3')['status'], 'denied')
            run.assert_not_called()
            self.runtime.approve.return_value = True
            self.assertEqual(self.call('bash', command='exit 3')['output']['exit_code'], 3)
            run.assert_called_once_with('exit 3', 20, 12000)
            self.runtime.tools = []
            self.runtime.approve.reset_mock()
            self.assertEqual(self.call('bash', command='exit 3')['status'], 'denied')
            self.runtime.approve.assert_not_called()

    def test_failed_cleanup_blocks_all_actions(self):
        self.runtime.sandbox.broken = True
        try:
            for tool, args in [('read_file', {'path': 'a.py'}),
                               ('write_file', {'path': 'x', 'content': 'x'}),
                               ('bash', {'command': 'true'})]:
                self.assertEqual(self.call(tool, **args)['status'], 'denied')
        finally:
            self.runtime.sandbox.broken = False

    def test_url_allowlist(self):
        for url in ['http://docs.python.org/', 'https://example.com/',
                    'https://u:p@docs.python.org/', 'https://docs.python.org:8443/',
                    'https://docs.python.org/?q=x', 'https://[']:
            self.assertEqual(self.call('fetch_url', url=url)['status'], 'denied')


    def test_search_ignores_special_files(self):
        import os
        if not hasattr(os, 'mkfifo'):
            self.skipTest('FIFO creation requires POSIX')
        os.mkfifo(self.runtime.root / 'pipe')
        self.assertEqual(self.call('search_files', query='value')['output'][0]['path'], 'a.py')
