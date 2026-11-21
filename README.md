# Dana

**Dana** is a self-hosted personal assistant for a household. It runs on your own server, talks to your own accounts (Google, Home Assistant, WhatsApp, Telegram, your banks), and meets you on your phone, your watch, a wall tablet, the TV, the web and Telegram.

It is one person's daily driver, published as is: opinionated, built around one family's life (Hebrew and English, Israeli banks and phone numbers), and written to be easy to bend to yours.

## What it does

- **Chat and voice** with an agent that uses tools: a custom ReAct loop over [LiteLLM](https://github.com/BerriAI/litellm) (Claude by default, any provider by env), streamed to every client; realtime voice calls; push-to-talk; "Hey Dana" wake word on the tablet and TV.
- **Integrations**, each a self-contained kind that owns its tools and is connected per user: tasks and reminders (time, location, car and wake triggers), a shared shopping list, projects with decisions compared side by side, people and their briefs, phone notifications and call recordings turned into suggestions, Home Assistant, WhatsApp (WAHA), Google Calendar / Contacts / Gmail, your own Telegram account (read-only), money (bank and card sync, dashboards, a balance forecast, ways to save), Frigate cameras.
- **Proactive**: a morning briefing, a weekly review, a "Now" queue of what needs you, urgent-notification escalation, caller cards for incoming calls.
- **Safe by default**: reversible actions run at once with Undo; sending to other people or destroying something asks first; content written by others (mail, a shared screen) cannot make Dana act outside without asking.
- **Several users** on one server, each with their own integrations, data and tokens; sharing of lists and projects between them.

## Repository

| Path | What |
|---|---|
| [`server/`](server/) | The server (Python 3.14, aiohttp, SQLite): agent, integrations, APIs, web UI, Telegram bot, background jobs |
| [`android/`](android/) | The phone app, the wall-tablet kiosk and the TV home screen (flavors of one app), and the Wear OS watch app (Kotlin, Compose) |
| [`extensions/raycast-assistant/`](extensions/raycast-assistant/) | A Raycast extension |

## Run the server

With Docker:

```bash
cd server
cp .env.example .env        # at least ANTHROPIC_API_KEY (or another provider's key + MODEL) and API_TOKEN
docker compose up -d        # http://localhost:8080, sign in with API_TOKEN
```

From source:

```bash
cd server
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python main.py
```

Then connect integrations on the web UI (Settings → Integrations) or with `bin/users.py`. Configuration: [`server/docs/configuration.md`](server/docs/configuration.md). Deploying (Docker, Kubernetes, the push-to-deploy hook): [`server/docs/operations.md`](server/docs/operations.md).

## Your own deployment

Run Dana as upstream ships it and keep what is yours in a **site** folder beside it: deploy settings, your own plugins, the apps' server address and TV setup, and notes for you and your coding agents. Start from `site.example/`. Updating is then `git pull`, and your deployment never drifts from Dana: [`docs/site.md`](docs/site.md).

## Build the apps

```bash
cd android
./gradlew :app:assemblePhoneDebug     # the phone app; :app:assembleKioskDebug, :app:assembleTvDebug, :wear:assembleDebug
```

JDK 21 and the Android SDK are needed. Point the app at your server in its Settings. Direct push needs your own Firebase project (your site's `google-services.json`: `android/docs/release.md` → Your site); without it the app works and syncs when opened.

## Extend it

- A new integration is a package in `server/integrations/<kind>/`: declare the kind, write its tools as typed Python functions with docstrings (the schema is generated), and it shows up for users who connect it. Start with [`server/docs/access.md`](server/docs/access.md) → Integration.
- Something only your setup needs can stay out of the repository as a **plugin** in your site's `plugins/`: the same code as a built-in kind ([`server/docs/plugins.md`](server/docs/plugins.md); a working example: [`server/examples/plugins/hello/`](server/examples/plugins/hello/)).
- Every area has a doc: start from [`server/AGENTS.md`](server/AGENTS.md) and [`android/AGENTS.md`](android/AGENTS.md). They are written for people and coding agents alike.

## Contributing

Issues and pull requests are welcome: [`CONTRIBUTING.md`](CONTRIBUTING.md). Security reports: [`SECURITY.md`](SECURITY.md).

## License

[MIT](LICENSE). Bundled fonts, icons and wake-word models keep their own licenses: [`THIRD_PARTY.md`](THIRD_PARTY.md).
