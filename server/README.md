# Dana server

The server of [Dana](../README.md): a custom ReAct agent over LiteLLM with integrations that own their tools, served over an aiohttp API + web UI and a Telegram bot, plus the pipelines behind the Android apps (call recordings, the notifications Inbox, reminders, push).

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # at least an LLM key (ANTHROPIC_API_KEY by default)
.venv/bin/python main.py  # web UI + API on :8080, Telegram if TELEGRAM_BOT_TOKEN is set
bin/test                  # the test suite
```

Or `docker compose up -d` (`compose.yaml`). Developer and agent guide: [`AGENTS.md`](AGENTS.md), with per-area docs in [`docs/`](docs/).
