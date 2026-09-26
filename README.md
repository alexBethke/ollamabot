# Ollama ↔ Telegram

Talk to a local Ollama model through a private Telegram bot. Runs with Python 3.10+ and its standard library; no pip dependencies, public server, or inbound ports required.

## Setup

1. Install [Ollama](https://ollama.com/download) and start it. On macOS, open the Ollama application; with the CLI, run `ollama serve` if a server is not already running.
2. Run `ollama list` to find your installed model. If needed, download a model first, for example `ollama pull llama3.2`.
3. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and follow its prompts to obtain a bot token.
4. From this directory, run:

   ```sh
   cp .env.example .env
   chmod 600 .env
   ```

   Edit `.env`: set `TELEGRAM_BOT_TOKEN` and `OLLAMA_MODEL` to the exact installed model name. Keep your token private; `.env` is ignored by Git. Environment variables override `.env` values.

5. Start the bridge:

   ```sh
   python3 bot.py
   ```

6. Open your new bot in Telegram and send `/id`. Stop the bridge with Ctrl+C, put the returned number in `TELEGRAM_ALLOWED_USER_IDS` in `.env`, then restart `python3 bot.py`.
7. Send a text message. Your local model's answer will arrive in Telegram.

Keep this process and Ollama running, and keep the computer awake and connected to the internet. Run commands from this directory so the bot can find `.env`.

## Commands

| Command | Action |
| --- | --- |
| `/start`, `/help` | Show help |
| `/id` | Show your Telegram user ID (also works before authorization) |
| `/reset` | Clear your conversation history |
| `/model` | Show the configured Ollama model |

Change `OLLAMA_MODEL` in `.env` and restart to switch models. `HISTORY_TURNS` controls retained conversation pairs; `OLLAMA_TIMEOUT` is the request timeout in seconds. `SYSTEM_PROMPT` sets the assistant's instructions.

## Behavior and privacy

Only allowlisted users in private chats can invoke the model. An empty allowlist grants nobody access. Groups, channels, bot senders, and edited messages are ignored. Text only; attachments and voice are unsupported. The bridge processes messages sequentially, suitable for personal use. Replies render common Markdown: `**bold**`, `*italic*`, `~~strikethrough~~`, inline code, and fenced code blocks. Other Markdown remains plain text. Long replies are split with formatting preserved; if Telegram rejects formatting, the affected message is sent as plain text.

History stays in memory and is lost on restart. The bot keeps the last 10 completed turns per chat by default. Telegram may deliver messages queued while the bridge was offline. Delivery is best effort: a failed reply is logged and skipped, and a crash before Telegram acknowledges an update can cause it to be processed again. Run only one instance per token.

Inference happens at `OLLAMA_URL` (localhost by default), but your messages and replies pass through Telegram. Bot chats are not end-to-end encrypted. The bridge does not log message contents or tokens. Do not expose Ollama's port to the internet.

## Troubleshooting

- **No reply:** check the terminal, private-chat the correct bot, and verify your `/id` is allowlisted. Restart after editing `.env`.
- **Ollama could not reply:** confirm Ollama is running and `ollama list` includes the exact configured name. Large models may need a longer timeout.
- **Polling rejected:** check your token and stop any other process using it. Existing webhooks must be removed or a separate bot created.
- **macOS Python certificate errors:** use the `Install Certificates.command` bundled with a python.org installation. Keep TLS verification enabled.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests use mocked APIs and require neither credentials nor a running model.

API references: [Telegram Bot API](https://core.telegram.org/bots/api), [Ollama chat API](https://docs.ollama.com/api/chat).
