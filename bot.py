#!/usr/bin/env python3
"""Private Telegram chat bridge to a local Ollama server (Python 3.10+)."""
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from formatting import formatted_chunks

log = logging.getLogger("ollamabot")


class APIError(Exception):
    def __init__(self, service, status=None, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"{service} request failed" + (f" (HTTP {status})" if status else ""))


def load_env(path=Path('.env')):
    """Read simple KEY=value settings without executing shell code."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key.strip()):
            raise ValueError(f"Invalid .env setting on line {number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def request_json(url, payload, timeout, service):
    request = Request(url, data=json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        retry_after = None
        try:
            retry_after = json.load(exc).get('parameters', {}).get('retry_after')
        except (ValueError, OSError):
            pass
        # Never include URLs, tokens, prompts, or remote response bodies in logs.
        raise APIError(service, exc.code, retry_after) from None
    except (URLError, OSError, ValueError):
        raise APIError(service) from None


def chunks(text, limit=4000):
    """Keep messages below Telegram's limit, including UTF-16 emoji pairs."""
    part, size = [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > limit:
            yield ''.join(part)
            part, size = [], 0
        part.append(char)
        size += width
    if part:
        yield ''.join(part)


class Bot:
    def __init__(self):
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
            raise ValueError('Set TELEGRAM_BOT_TOKEN in .env to the token from @BotFather.')
        self.telegram_url = f'https://api.telegram.org/bot{token}/'
        ids = os.environ.get('TELEGRAM_ALLOWED_USER_IDS', '')
        try:
            self.allowed = {int(x.strip()) for x in ids.split(',') if x.strip()}
        except ValueError:
            raise ValueError('TELEGRAM_ALLOWED_USER_IDS must contain comma-separated numeric IDs.') from None
        if any(x <= 0 for x in self.allowed):
            raise ValueError('Telegram user IDs must be positive.')
        self.ollama_url = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
        self.model = os.environ.get('OLLAMA_MODEL', '').strip()
        if not self.model:
            raise ValueError('Set OLLAMA_MODEL in .env to a model shown by ollama list.')
        self.timeout = int(os.environ.get('OLLAMA_TIMEOUT', '300'))
        self.turns = int(os.environ.get('HISTORY_TURNS', '10'))
        if self.timeout <= 0 or self.turns <= 0:
            raise ValueError('OLLAMA_TIMEOUT and HISTORY_TURNS must be positive integers.')
        self.system = os.environ.get('SYSTEM_PROMPT', 'You are a helpful assistant. Answer clearly and concisely.')
        self.history = {}

    def telegram(self, method, **payload):
        for attempt in range(3):
            try:
                result = request_json(self.telegram_url + method, payload, 40, 'Telegram')
                if not isinstance(result, dict) or not result.get('ok'):
                    raise APIError('Telegram', result.get('error_code') if isinstance(result, dict) else None)
                return result['result']
            except APIError as exc:
                if exc.status != 429 or attempt == 2:
                    raise
                time.sleep(min(max(int(exc.retry_after or 5), 1), 60))

    def send(self, chat_id, text, formatted=False):
        if formatted:
            for html, plain in formatted_chunks(text):
                if not plain.strip():
                    continue
                try:
                    self.telegram('sendMessage', chat_id=chat_id, text=html, parse_mode='HTML')
                except APIError as exc:
                    if exc.status != 400:
                        raise
                    # A rejected format must not prevent delivery of the answer.
                    self.telegram('sendMessage', chat_id=chat_id, text=plain)
            return
        for part in chunks(text):
            if part.strip():
                self.telegram('sendMessage', chat_id=chat_id, text=part)

    def handle(self, update):
        message = update.get('message', {})
        chat = message.get('chat', {})
        sender = message.get('from', {})
        if chat.get('type') != 'private' or sender.get('is_bot') or not sender.get('id'):
            return
        user_id, chat_id = sender['id'], chat['id']
        text = message.get('text', '').strip()
        command = text.split(maxsplit=1)[0].split('@')[0].lower() if text else ''
        # ID discovery exposes no model access, even with an empty allowlist.
        if command == '/id':
            self.send(chat_id, f'Your Telegram user ID: {user_id}')
            return
        if user_id not in self.allowed:
            if command == '/start':
                self.send(chat_id, 'Access is not enabled. Send /id, then add that ID to TELEGRAM_ALLOWED_USER_IDS on the computer running this bot and restart it.')
            return
        if command in ('/start', '/help'):
            self.send(chat_id, f'Send me text to talk to {self.model}.\n/reset — clear conversation\n/model — show model\n/id — show your user ID')
            return
        if command == '/reset':
            self.history.pop(chat_id, None)
            self.send(chat_id, 'Conversation cleared.')
            return
        if command == '/model':
            self.send(chat_id, f'Current model: {self.model}')
            return
        if command.startswith('/'):
            self.send(chat_id, 'Unknown command. Send /help for commands.')
            return
        if not text:
            self.send(chat_id, 'Please send a text message. Attachments and voice messages are not supported.')
            return
        try:
            self.telegram('sendChatAction', chat_id=chat_id, action='typing')
        except APIError:
            pass
        messages = [{'role': 'system', 'content': self.system}]
        messages += self.history.get(chat_id, []) + [{'role': 'user', 'content': text}]
        try:
            result = request_json(self.ollama_url + '/api/chat',
                                  {'model': self.model, 'messages': messages, 'stream': False},
                                  self.timeout, 'Ollama')
            answer = result.get('message', {}).get('content') if isinstance(result, dict) else None
            if not isinstance(answer, str) or not answer.strip():
                raise APIError('Ollama returned an empty reply')
        except APIError as exc:
            log.warning('%s', exc)
            self.send(chat_id, 'Ollama could not reply. Check that Ollama is running, the configured model is downloaded, and OLLAMA_TIMEOUT is long enough. Then try again.')
            return
        self.send(chat_id, answer, formatted=True)
        self.history[chat_id] = (messages[1:] + [{'role': 'assistant', 'content': answer}])[-2 * self.turns:]

    def run(self):
        me = self.telegram('getMe')
        info = self.telegram('getWebhookInfo')
        if info.get('url'):
            raise ValueError('This bot has a webhook configured. Use a new BotFather bot or remove its webhook before using polling.')
        log.info('Connected as @%s; model: %s', me['username'], self.model)
        if not self.allowed:
            log.warning('No users authorized. Send /id to the bot, add your ID to .env, and restart.')
        offset = None
        while True:
            try:
                updates = self.telegram('getUpdates', offset=offset, timeout=30, allowed_updates=['message'])
                for update in updates:
                    try:
                        self.handle(update)
                    except APIError as exc:
                        log.warning('Could not deliver reply: %s. Update will be skipped.', exc)
                    # Avoid repeating inference when delivery fails.
                    offset = update['update_id'] + 1
            except APIError as exc:
                if exc.status in (401, 404, 409):
                    raise ValueError('Telegram rejected polling. Check the bot token and ensure only one bot instance is running.') from None
                log.warning('%s; retrying in 5 seconds', exc)
                time.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        load_env()
        Bot().run()
    except KeyboardInterrupt:
        log.info('Stopped.')
    except (ValueError, APIError) as exc:
        log.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
