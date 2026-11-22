# Dana

**Dana** is a self-hosted personal assistant for a household. It runs on your own server, talks to your own accounts (Google, Home Assistant, WhatsApp, Telegram, your banks), and meets you on your phone, your watch, a wall tablet, the TV, the web and Telegram.

It is one person's daily driver, published as is: opinionated, built around one family's life (Hebrew and English, Israeli banks and phone numbers), and written to be easy to bend to yours.

## What it does

- **Chat and voice** with an agent that uses tools: a custom ReAct loop over [LiteLLM](https://github.com/BerriAI/litellm) (Claude by default, any provider by env), streamed to every client; realtime voice calls; push-to-talk; "Hey Dana" wake word on the tablet and TV.
- **Addons**, each self-contained, owning its tools and is connected per user: tasks and reminders (time, location, car and wake triggers), a shared shopping list, projects with decisions compared side by side, people and their briefs, phone notifications and call recordings turned into suggestions, Home Assistant, WhatsApp (WAHA), Google Calendar / Contacts / Gmail, your own Telegram account (read-only), money (bank and card sync, dashboards, a balance forecast, ways to save), Frigate cameras.
- **Proactive**: a morning briefing, a weekly review, a "Now" queue of what needs you, urgent-notification escalation, caller cards for incoming calls.
- **Safe by default**: reversible actions run at once with Undo; sending to other people or destroying something asks first; content written by others (mail, a shared screen) cannot make Dana act outside without asking.
- **Several users** on one server, each with their own addons, data and tokens; sharing of lists and projects between them.

## Repository

| Path | What |
|---|---|
| [`server/`](server/) | The server (Python 3.12+, tested on 3.14; aiohttp, SQLite): agent, addons, APIs, web UI, Telegram bot, background jobs |
| [`android/`](android/) | The phone app, the wall-tablet kiosk and the TV home screen (flavors of one app), and the Wear OS watch app (Kotlin, Compose) |
| [`extensions/raycast-assistant/`](extensions/raycast-assistant/) | A Raycast extension |
| [`examples/addons/`](examples/addons/) | A working example of an addon you write yourself |
| [`website/`](website/) | The project's one-page site: open `website/index.html` |

## Run the server

An LLM key is the only thing you need, and the setup page asks for it (Claude by default; OpenAI, Gemini or a local model through Ollama also work). Three steps with Docker, no checkout:

```bash
curl -fsSLO https://raw.githubusercontent.com/ronganam/dana/master/compose.yaml
docker compose up -d
docker compose logs dana                # open the setup link it prints
```

The setup link (one-time, on this machine) creates you as the admin, in that browser, then walks you through a short wizard: you, your home, the model and its key (tested on the spot), your phone (scan a QR code), add-ons, and an optional sample household to look around with. Household members are added and invited from Settings → Household. The port is published on 127.0.0.1 only; to reach Dana from other devices, put a reverse proxy with HTTPS in front of it or use a VPN ([`server/docs/operations.md`](server/docs/operations.md)). Upgrade: `docker compose pull && docker compose up -d`.

Headless (no browser at hand to open the link): also put `API_TOKEN` in `.env` (generate one: `openssl rand -hex 32`) before the first start, and sign in with it.

Add-ons from the catalog install on the web (Settings → Addons) or with the `dana` command inside the container; either way they go into the `data` volume and survive upgrades:

```bash
docker compose exec dana dana addons install <name>   # from the catalog (or REPO@COMMIT[:PATH])
docker compose restart dana                           # load it
```

The same command manages users: `docker compose exec dana dana users list`. The people in your household are added and invited in the browser (Settings → Household).

### Build from source

With Docker, building the image from your checkout:

```bash
cd server
cp .env.example .env        # set ANTHROPIC_API_KEY
docker compose up -d        # then open the setup link from docker compose logs
```

Without Docker (Python 3.12 or newer; the image and the tests run 3.14):

```bash
cd server
python3 -m venv .venv                        # or python3.14 / python3.12 when python3 is older
.venv/bin/pip install -r requirements.lock   # every dependency pinned
cp .env.example .env                         # set ANTHROPIC_API_KEY
.venv/bin/python main.py                     # open the setup link it logs
```

At setup the server creates you as its admin, turns on the addons that need no account (tasks, shopping, projects, people, phone…) and, unless `CREDENTIALS_KEY` is set, generates the key that encrypts addon credentials into `credentials.key` next to the database: back it up with the database. Then connect the rest on the web UI (Settings → Addons) or with `bin/dana addons connect`. Configuration: [`server/docs/configuration.md`](server/docs/configuration.md). Deploying (Docker, Kubernetes, the push-to-deploy hook): [`server/docs/operations.md`](server/docs/operations.md).

## What each feature needs

Each feature is an addon you turn on per user. Before you turn one on, this says what it needs from you; the Addons page says the same for each addon, with why, and what it may touch (the hosts it contacts, whether it acts for you outside Dana, whether it keeps credentials, which other addons' data it reads). The table is generated from the addons' declarations (`server/bin/dana addons table`).

<!-- addons-table:start -->
| Feature | What it does | Required | Optional | Region |
|---|---|---|---|---|
| Essentials | The time, asking you to pick, web search, the weather at Home | — | — | Any |
| Tasks | Task lists, reminders and their triggers | — | Dana's phone app, or Home Assistant; the Phone addon | Any |
| Shopping | Your shopping list, sorted by aisle and shared live | — | the Phone addon | Any |
| Projects | Projects, decisions and saved notes, links and files | — | a transcription model: an OpenAI or Gemini key, or your own Whisper server | Any |
| About you | What Dana remembers about you | — | — | Any |
| People | The people you talk to and a brief on each | — | the Google Contacts addon; the Phone addon | Any |
| Phone | The Android phone: notifications Inbox, calls and suggestions, push | Dana for Android on your phone, with notification access | a dialer that saves call recordings to Recordings/Call (Samsung's); a transcription model: an OpenAI or Gemini key, or your own Whisper server; the ntfy app on your phone (UnifiedPush); `FCM_SERVICE_ACCOUNT_JSON`; `TYPESAFE_API_KEY` | Any |
| Home Assistant | Lights, devices and the TV, reminders when you arrive or get in the car | a Home Assistant server Dana's server can reach; a Home Assistant long-lived access token | the Home Assistant phone app | Any |
| WhatsApp | Read and send WhatsApp messages, voice notes as text | a WAHA server (a WhatsApp Web bridge) Dana's server can reach; a WhatsApp account, linked to WAHA | `WAHA_WEBHOOK_SECRET`; a transcription model: an OpenAI or Gemini key, or your own Whisper server; `TELEGRAM_BOT_TOKEN` | Any |
| Google Calendar | Your events in chat, on the Now screen and in the briefing | `GOOGLE_CLIENT_ID`; `GOOGLE_CLIENT_SECRET`; a Google account | `PUBLIC_URL` | Any |
| Google Contacts | Names, numbers and birthdays of the people you know | `GOOGLE_CLIENT_ID`; `GOOGLE_CLIENT_SECRET`; a Google account | `PUBLIC_URL` | Any |
| Gmail | Search, read and write your mail in chat | `GOOGLE_CLIENT_ID`; `GOOGLE_CLIENT_SECRET`; a Google account | `PUBLIC_URL` | Any |
| Telegram account | Your own Telegram chats and channels, read-only | `TELEGRAM_API_ID`; `TELEGRAM_API_HASH`; a Telegram account | — | Any |
| Money | Your bank and card spending, balances and ways to save | — | the Phone addon | Any |
| Cameras | Your Frigate cameras on the dashboard | a Frigate server with your cameras | the Home Assistant addon | Any |
| Ops | Read-only kubectl and Python run on the server (asks first; not a sandbox) | Dana running in Kubernetes, with a service account allowed to read | — | Any |
<!-- addons-table:end -->

Some parts need something without being an addon:

- **The Android apps** (phone, watch, wall tablet, TV): the APKs on each GitHub release, or built from [`android/`](android/) ([Build the apps](#build-the-apps)). Each pairs with your server by a QR code or a short code from the web's Devices page; the watch is paired through the phone app.
- **Direct push** to the phone: the free [ntfy](https://ntfy.sh) app on the phone (UnifiedPush; public ntfy.sh or your own ntfy server). The ping carries no content. If you build the app yourself with a Firebase project, FCM works instead (`FCM_SERVICE_ACCOUNT_JSON` on the server). Without either, the app still works and catches up when it syncs.
- **Telegram**: `TELEGRAM_BOT_TOKEN` for chatting with Dana in Telegram and for the messages Dana sends there.
- **Voice**: `OPENAI_API_KEY` for realtime voice calls; a transcription model for voice messages and call recordings: an OpenAI or Gemini key, or a local Whisper server (`docker compose --profile whisper up -d` and `WHISPER_URL=http://whisper:8000`); push-to-talk uses the phone's own speech recognition.

## Your own deployment

Run Dana as upstream ships it and keep what is yours in a **site** folder beside it: deploy settings, your own addons, the apps' server address and TV setup, and notes for you and your coding agents. Start from `site.example/`. Updating is then `git pull`, and your deployment never drifts from Dana: [`docs/site.md`](docs/site.md).

Addons: write your own with `server/bin/dana addons new <name>` (a working addon in `site/addons/<name>`), or install one from the catalog on the Addons page or with `bin/dana addons install`. Installed addons are pinned to a git commit and **run with the server's full access**: install only code you trust. [`docs/addons.md`](docs/addons.md).

## Build the apps

```bash
cd android
./gradlew :app:assemblePhoneDebug     # the phone app; :app:assembleKioskDebug, :app:assembleTvDebug, :wear:assembleDebug
```

JDK 21 and the Android SDK are needed. Point the app at your server in its Settings. Direct push needs your own Firebase project (your site's `google-services.json`: `android/docs/release.md` → Your site); without it the app works and syncs when opened.

## Extend it

- A new addon is a package in `server/addons/<name>/`: declare it in `addon.py`, write its tools as typed Python functions with docstrings (the schema is generated), and it shows up for users who connect it. The design: [`docs/addons.md`](docs/addons.md); the reference: [`server/docs/access.md`](server/docs/access.md) → Addon.
- Something only your setup needs can stay out of the repository as a **site addon** in your site's `addons/`: the same code as an official addon ([`server/docs/writing-an-addon.md`](server/docs/writing-an-addon.md); working examples: [`examples/addons/hello/`](examples/addons/hello/), the smallest, and [`examples/addons/showcase/`](examples/addons/showcase/), every capability once).
- **The add-on kit**, made to be driven by a coding agent: `bin/dana addons new <name>` writes an add-on in its own repository with an `AGENTS.md` guide and the generated reference ([`server/docs/addon-reference.md`](server/docs/addon-reference.md)); `check` says what is wrong and how to fix it, `test` runs its tests, `dev` runs it with sample data, and `publish` releases it and opens a pull request to the community catalog, [dana-catalog](https://github.com/ronganam/dana-catalog).
- Every area has a doc: start from [`server/AGENTS.md`](server/AGENTS.md) and [`android/AGENTS.md`](android/AGENTS.md). They are written for people and coding agents alike.

## Contributing

Issues and pull requests are welcome: [`CONTRIBUTING.md`](CONTRIBUTING.md). Security reports: [`SECURITY.md`](SECURITY.md).

## License

[MIT](LICENSE). Bundled fonts, icons and wake-word models keep their own licenses: [`THIRD_PARTY.md`](THIRD_PARTY.md).
