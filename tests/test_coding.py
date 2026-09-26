import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from bot import Bot, APIError
from coding import Workspace


class CodingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def test_edit_and_ambiguous_replacement(self):
        self.workspace.execute('write_file', {'path': 'src/a.py', 'content': 'one\none\n'})
        result = self.workspace.execute('replace_text', {'path': 'src/a.py', 'old': 'one', 'new': 'two'})
        self.assertIn('Tool failed', result)
        self.assertEqual((self.root / 'src/a.py').read_text(), 'one\none\n')
        self.workspace.execute('replace_text', {'path': 'src/a.py', 'old': 'one\none', 'new': 'two'})
        self.assertEqual(self.workspace.execute('read_file', {'path': 'src/a.py'}), 'two\n')

    def test_boundary_and_private_files(self):
        (self.root / 'link').symlink_to(self.root.parent, target_is_directory=True)
        for path in ['../escape', '/tmp/escape', '.env', '.git/config', 'link/escape']:
            with self.subTest(path=path):
                self.assertIn('Tool failed', self.workspace.execute('write_file', {'path': path, 'content': 'bad'}))
        (self.root / 'big').write_bytes(b'x' * 32001)
        self.assertIn('Tool failed', self.workspace.execute('read_file', {'path': 'big'}))
        self.assertIn('Tool failed', self.workspace.execute('run_command', {'command': 'echo unsafe'}))
        self.assertIn('Tool failed', self.workspace.execute('read_file', []))

    def test_opt_in_command(self):
        workspace = Workspace(self.root, commands=True)
        self.assertEqual(workspace.execute('run_command', {'command': 'printf hello'}), 'Exit code: 0\nhello')
        self.assertIn('Exit code: 7', workspace.execute('run_command', {'command': 'exit 7'}))

    def bridge(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': '123:secret',
                                    'TELEGRAM_ALLOWED_USER_IDS': '42', 'OLLAMA_MODEL': 'test',
                                    'CODING_WORKSPACE': str(self.root), 'HISTORY_TURNS': '1'}, clear=True):
            bridge = Bot()
        bridge.telegram = MagicMock()
        return bridge

    def update(self, user=42):
        return {'message': {'from': {'id': user}, 'chat': {'type': 'private', 'id': user},
                            'text': '/code create hello.py'}}

    def test_tool_round_trip_and_complete_history(self):
        bridge = self.bridge()
        call = {'message': {'role': 'assistant', 'content': '', 'tool_calls': [
            {'function': {'name': 'write_file', 'arguments': {'path': 'hello.py', 'content': 'print(42)'}}}]}}
        with patch('bot.request_json', side_effect=[call, {'message': {'content': 'Created hello.py'}}]) as request:
            bridge.handle(self.update(user=99))
            request.assert_not_called()
            bridge.handle(self.update())
            payload = request.call_args.args[1]
            self.assertEqual(payload['messages'][-1]['role'], 'tool')
            self.assertEqual(payload['messages'][-1]['tool_name'], 'write_file')
        self.assertEqual((self.root / 'hello.py').read_text(), 'print(42)')
        self.assertEqual([m['role'] for m in bridge.coding_history[42][0]],
                         ['user', 'assistant', 'tool', 'assistant'])
        with patch('bot.request_json', return_value={'message': {'content': 'Done'}}):
            bridge.handle(self.update())
        self.assertEqual(len(bridge.coding_history[42]), 1)
        self.assertEqual(bridge.history, {})

    def test_failure_and_step_limit(self):
        bridge = self.bridge()
        with patch('bot.request_json', side_effect=APIError('Ollama')):
            bridge.handle(self.update())
        self.assertIn('Coding stopped', bridge.telegram.call_args.kwargs['text'])
        call = {'message': {'content': '', 'tool_calls': [
            {'function': {'name': 'list_files', 'arguments': {'path': '.'}}}]}}
        with patch('bot.request_json', return_value=call) as request:
            bridge.handle(self.update())
            self.assertEqual(request.call_count, 16)
        self.assertIn('step limit', bridge.telegram.call_args.kwargs['text'])
