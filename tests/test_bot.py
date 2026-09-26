import json
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from io import BytesIO

import bot


class BotTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'TELEGRAM_BOT_TOKEN': '123:secret', 'TELEGRAM_ALLOWED_USER_IDS': '42,43',
            'OLLAMA_MODEL': 'test-model', 'HISTORY_TURNS': '2',
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.bridge = bot.Bot()
        self.bridge.telegram = MagicMock()
        self.api = patch('bot.request_json', return_value={'message': {'content': 'Hello!'}})
        self.request = self.api.start()
        self.addCleanup(self.api.stop)

    def update(self, text='Hi', user=42, kind='private'):
        return {'update_id': 1, 'message': {'from': {'id': user},
                'chat': {'id': user, 'type': kind}, 'text': text}}

    def test_access_control_and_id_discovery(self):
        self.bridge.handle(self.update(user=99))
        self.bridge.handle(self.update(kind='group'))
        self.request.assert_not_called()
        self.bridge.telegram.assert_not_called()
        self.bridge.handle(self.update('/id', user=99))
        self.bridge.telegram.assert_called_with('sendMessage', chat_id=99, text='Your Telegram user ID: 99')
        self.request.assert_not_called()
        self.bridge.allowed.clear()
        self.bridge.handle(self.update())
        self.request.assert_not_called()

    def test_ollama_contract_and_conversation_isolation(self):
        self.bridge.handle(self.update('First'))
        url, payload, timeout, service = self.request.call_args.args
        self.assertEqual(url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(payload['model'], 'test-model')
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['messages'][-1], {'role': 'user', 'content': 'First'})
        self.bridge.handle(self.update('Second'))
        self.assertEqual(len(self.request.call_args.args[1]['messages']), 4)
        self.bridge.handle(self.update('Another chat', user=43))
        self.assertEqual(len(self.request.call_args.args[1]['messages']), 2)
        self.bridge.handle(self.update('Third'))
        self.assertEqual(len(self.bridge.history[42]), 4)
        self.assertEqual(self.bridge.history[42][0]['content'], 'Second')
        self.bridge.handle(self.update('/reset'))
        self.assertNotIn(42, self.bridge.history)
        self.assertIn(43, self.bridge.history)

    def test_failed_inference_preserves_history(self):
        self.bridge.handle(self.update())
        previous = list(self.bridge.history[42])
        self.request.side_effect = bot.APIError('Ollama')
        self.bridge.handle(self.update('Fail'))
        self.assertEqual(self.bridge.history[42], previous)
        self.assertIn('Ollama could not reply', self.bridge.telegram.call_args.kwargs['text'])

    def test_empty_model_response(self):
        self.request.return_value = {'message': {'content': ''}}
        self.bridge.handle(self.update())
        self.assertEqual(self.bridge.history, {})
        self.assertIn('Ollama could not reply', self.bridge.telegram.call_args.kwargs['text'])

    def test_delivery_failure_does_not_commit_history(self):
        self.bridge.telegram.side_effect = bot.APIError('Telegram')
        with self.assertRaises(bot.APIError):
            self.bridge.handle(self.update())
        self.assertEqual(self.bridge.history, {})

    def test_commands_and_attachments_do_not_invoke_model(self):
        for text in ['/start', '/help', '/model', '/unknown', '']:
            self.bridge.handle(self.update(text))
        self.request.assert_not_called()

    def test_unicode_chunking(self):
        text = 'a😀\n' * 5000
        parts = list(bot.chunks(text))
        self.assertEqual(''.join(parts), text)
        self.assertTrue(all(len(p.encode('utf-16-le')) // 2 <= 4000 for p in parts))

    def test_rate_limit_retried(self):
        self.request.side_effect = [bot.APIError('Telegram', 429, 2), {'ok': True, 'result': True}]
        with patch('bot.time.sleep') as sleep:
            self.assertTrue(bot.Bot().telegram('sendMessage', chat_id=42, text='Hello'))
        sleep.assert_called_once_with(2)

    def test_formatted_reply_and_original_history(self):
        self.request.return_value = {'message': {'content': '**42**'}}
        self.bridge.handle(self.update())
        self.bridge.telegram.assert_called_with('sendMessage', chat_id=42,
                                                text='<b>42</b>', parse_mode='HTML')
        self.assertEqual(self.bridge.history[42][-1]['content'], '**42**')

    def test_format_rejection_falls_back_to_plain_text(self):
        self.bridge.telegram.side_effect = [bot.APIError('Telegram', 400), True]
        self.bridge.send(42, '**42**', formatted=True)
        self.bridge.telegram.assert_called_with('sendMessage', chat_id=42, text='42')

    def test_network_failure_does_not_trigger_duplicate_plain_reply(self):
        self.bridge.telegram.side_effect = bot.APIError('Telegram')
        with self.assertRaises(bot.APIError):
            self.bridge.send(42, '**42**', formatted=True)
        self.assertEqual(self.bridge.telegram.call_count, 1)


class TransportTests(unittest.TestCase):
    def test_json_http_contract(self):
        response = MagicMock()
        response.__enter__.return_value = BytesIO(b'{"ok": true, "result": []}')
        with patch('bot.urlopen', return_value=response) as open_url:
            self.assertEqual(bot.request_json('https://example.test', {'text': 'Hi'}, 40, 'Telegram'),
                             {'ok': True, 'result': []})
        request = open_url.call_args.args[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data), {'text': 'Hi'})
        self.assertEqual(open_url.call_args.kwargs['timeout'], 40)

    def test_errors_do_not_expose_token(self):
        error = HTTPError('https://example.test/SECRET', 401, 'SECRET', {}, BytesIO(b'{}'))
        with patch('bot.urlopen', side_effect=error):
            with self.assertRaises(bot.APIError) as caught:
                bot.request_json('https://example.test/SECRET', {}, 40, 'Telegram')
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(caught.exception.status, 401)


if __name__ == '__main__':
    unittest.main()
