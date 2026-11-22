# Configuration (environment variables)

Local: `.env` (gitignored, from `.env.example`). Production: the same names as environment variables, from whatever secret store you use (a Kubernetes Secret, Infisical, Vault, …); the process reads them at start. Never print or commit secret values.

## The .env file

`main.py` and `bin/dana` load exactly one file, first thing, before any other module of Dana (several read their configuration at import) and before anything imports LiteLLM (`core/env_file.py`): `server/.env`, or the file `DANA_ENV_FILE` names (`DANA_ENV_FILE=""`: none; the tests set that, so a `bin/dana` they start never reads a real `.env`). A variable already set in the environment wins over the file. They also set `LITELLM_MODE=PRODUCTION` (whatever the file says): otherwise LiteLLM, when imported, loads any `.env` that python-dotenv finds walking up from LiteLLM's own install folder, so a virtual environment inside one checkout would read that checkout's `.env` even when another checkout or a test is running. Nothing else in Dana loads a `.env`.

## Models

Every model is a LiteLLM id (`provider/model`: `anthropic/claude-haiku-5-5`, `gemini/gemini-3.1-flash-lite`, `openai/gpt-5-mini`…). All calls go through `core/llm.py` (the web/app chat through the profile catalog in `core/models.py`). Which model a job uses, first match wins: its env variable (the table below), else the provider an admin chose in the app (next section), else Claude: `anthropic/claude-sonnet-5-5` for `MODEL` and `SEARCH_MODEL`, `anthropic/claude-haiku-5-5` for the background jobs, `anthropic/claude-opus-5-5` for `deep`. Transcription has its own order (below, "Transcription"): Claude takes no audio.

### The provider and key chosen in the app

A stranger's server needs one LLM key and no file editing: an admin picks the provider in the wizard's Model step or in Settings → The model (`static/llm.js`; `core/llm_provider.py`, `core/llm_api.py`) and pastes its key. `PUT /api/llm {provider, key?, base_url?, model?}` (`manage_server` only) first makes a tiny completion with the provider's fast model and the new key (`llm.check_model`, purpose `setup_check`); a refusal answers 422 `check_failed` in plain words (never the provider's message, which may quote the key) and stores nothing. Then the choice is stored server-wide in `server_settings['llm']` (core migration `m0007_server_settings`) with the key encrypted by the credentials key (`core/addons/crypto.py`; only its last four characters are kept in clear). `GET /api/llm` answers `{provider, providers, key: {set, from_env, last4}, base_url, model, models}`: never the key. Leaving the key out keeps the one stored for that provider.

| Provider | Key (env name) | fast (background) | balanced (chat, search) | deep | Transcription |
|---|---|---|---|---|---|
| `claude` (default) | `ANTHROPIC_API_KEY` | `anthropic/claude-haiku-5-5` | `anthropic/claude-sonnet-5-5` | `anthropic/claude-opus-5-5` | none of its own (Transcription, below) |
| `openai` | `OPENAI_API_KEY` | `openai/gpt-5.4-mini` | `openai/gpt-5.5` | `openai/gpt-5.5-pro` | `openai/gpt-4o-transcribe` |
| `gemini` | `GEMINI_API_KEY` | `gemini/gemini-3.1-flash-lite` | `gemini/gemini-3.5-flash` | `gemini/gemini-3.1-pro-preview` | `gemini/gemini-3.1-flash-lite` |
| `ollama` (experimental for tool use) | none: the server's address (`OLLAMA_API_BASE`) and a model name | `ollama_chat/<model>` | the same | the same | none of its own (Transcription, below) |

The choice sets every job's default (`llm.model`, `llm.model_for`: chat → balanced, search → balanced, the background jobs → fast, a purpose → its profile), the chat profiles (`core/models.py`: Claude's own catalog for `claude`, the provider's three models otherwise) and the agent's model for Telegram and triggers, at once, without a restart. At start and after each change `llm_provider.apply()` puts the stored key in the process environment under the provider's own name (where LiteLLM reads it) and Ollama's address under `OLLAMA_API_BASE`. **Env stays the override**: a model variable set in env wins over the choice, and a key variable the operator set in env is never overwritten (the answer then says `from_env`). A stored key the credentials key cannot read is logged and left out (enter it again).

| Variable | Used for |
|---|---|
| `MODEL` | Telegram + trigger agent runs, small helpers (task emoji/group, WhatsApp name matching, voice-note and media summaries) |
| `BACKGROUND_MODEL` | default for the structured jobs below |
| `CALL_ANALYSIS_MODEL` | call analysis (→ `BACKGROUND_MODEL`) |
| `NOTIFICATION_MODEL` | notification triage (the LLM judge), notification → task/event extraction and Inbox summaries (→ `BACKGROUND_MODEL`) |
| `BRIEFING_MODEL` | people briefs, wake briefing, weekly review (→ `BACKGROUND_MODEL`) |
| `SHOPPING_MODEL` | the shopping aisle of new item names (→ `BACKGROUND_MODEL`; without its key items stay under Other, `docs/shopping.md`) |
| `MONEY_MODEL` | money transaction categories and clean merchant names (→ `BACKGROUND_MODEL`; without its key, rows the rules cannot settle stay uncategorized, `docs/money.md`) |
| `TELEGRAM_SUMMARY_MODEL` | summaries of the user's unread Telegram chats (→ `BACKGROUND_MODEL`, `docs/telegram-account.md`) |
| `SEARCH_MODEL` | web-grounded answers through LiteLLM's `web_search_options`: the money price checks (→ `MODEL`; needs a model with web search: Claude's web search tool, Gemini's Google Search grounding…; without one the price checks are unavailable, `docs/money.md` → Price checks) |
| `TRANSCRIBE_MODEL` | all audio → text (voice notes, call recordings, voice chat, media), above the rest of the transcription order (below). Must hear audio: a chat model with audio input (Gemini) or a speech-to-text model (`openai/gpt-4o-transcribe`, `deepgram/nova-3`…). Claude has no audio input |
| `WHISPER_URL` | a local speech-to-text server with an OpenAI-compatible `/v1/audio/transcriptions` (e.g. `http://whisper:8000`, the root `compose.yaml`'s `whisper` profile; `/v1` is added when missing); used when nothing above it in the transcription order applies |
| `WHISPER_MODEL` | the model that server transcribes with (default `Systran/faster-whisper-small`; a bigger one is better and slower on a CPU). A server that answers 404 for it is asked to download it once (`POST /v1/models/<model>`, speaches) |
| `ASSISTANT_MODELS` / `ASSISTANT_DEFAULT_MODEL` | chat profiles for the web/app/Raycast picker (below) |
| `DEEP_MODEL` | the `deep` profile's model for LLM purposes that default to it (→ `anthropic/claude-opus-5-5`); `fast` purposes use `BACKGROUND_MODEL`, `balanced` ones `MODEL` |
| `MODEL_<PURPOSE>` | the model of one LLM purpose alone (`MODEL_TASK_EMOJI`, `MODEL_PEOPLE_BRIEF`…), above every variable here. Purposes: core's in `core/purposes.py`, each addon's in its manifest (`llm_jobs=`); an unknown purpose is an error, not a fallback |

### Transcription

Voice notes (WhatsApp, Telegram), call recordings, the tablet / TV / web voice turn and saved media are turned into text by `llm.transcribe`, with the first of these that applies, resolved on every call (`llm.transcriber`):

1. `MODEL_<PURPOSE>` (e.g. `MODEL_CALL_TRANSCRIPT`), then `TRANSCRIBE_MODEL`;
2. the provider chosen in the app, when it hears audio and its key is set: Gemini `gemini/gemini-3.1-flash-lite`, OpenAI `openai/gpt-4o-transcribe` (Claude and Ollama do not; with no choice, the provider of `MODEL`);
3. `OPENAI_API_KEY` set → `openai/gpt-4o-transcribe`;
4. `WHISPER_URL` set → `openai/<WHISPER_MODEL>` on that server (through LiteLLM's transcription with `api_base`; a local model has no price, so its calls show as unpriced on the Usage page). The root `compose.yaml` ships one as an optional profile: `docker compose --profile whisper up -d` starts `ghcr.io/speaches-ai/speaches:0.8.3-cpu` (faster-whisper on the CPU, its models in the `whisper-models` volume) and `WHISPER_URL=http://whisper:8000` points Dana at it;
5. none: transcription is unavailable. `llm.transcribe` raises `llm.TranscriptionUnavailable` (`code` `no_transcription_model`) before sending anything, and each feature says so: the voice upload answers 503 `{"error", "code": "no_transcription_model"}`, the voice stream sends `{"type": "error", "code": "no_transcription_model"}`, Telegram replies with the reason, a call recording is marked failed with "Needs a transcription model" (analyse it again once one is set), a WhatsApp voice note is kept without text, and the `transcription` self-check (admins) turns red once a transcription was refused. The Phone, WhatsApp and Projects addons list it among their optional needs. Startup logs the transcription model or warns that there is none.

Push-to-talk on the phone uses the phone's own speech recognition and needs none of this.

Chat profiles (`core/models.py`): `fast` Claude Haiku 5.5 at low effort (thinking hidden), `balanced` Claude Sonnet 5.5 and `deep` Claude Opus 5.5, both at high effort with their thinking summary streamed (LiteLLM turns `reasoning_effort` into adaptive thinking, a summarized display and `output_config.effort`; Sonnet at medium often answers without thinking, so nothing would show). Telegram / trigger runs on a Claude `MODEL` think at medium.

Claude notes: Claude 5.5 models always think and the thinking counts against `max_tokens`, so `llm.claude_kwargs` gives each job an effort (`chat` helpers low, `search` medium, the rest the model's default) and raises a tiny `max_tokens` to 2048. The agent marks the tool list as a cache breakpoint (`llm.cacheable_tools`; Gemini/OpenAI cache on their own). Thinking blocks are sent back within a turn's tool loop but never stored: Claude binds each block to the exact conversation it came from, and the next turn's system prompt (it holds the time) differs, so a replayed block would be refused. Unsupported sampling params (`temperature` on these models) are dropped. Histories written by Gemini work as they are (LiteLLM cleans its tool-call ids).

## The minimal first run

Nothing: a server with no users and no `API_TOKEN` starts in **setup mode** and logs one line with a one-time setup link (`<PUBLIC_URL or http://localhost:<PORT>>/setup?code=…`): opening it creates the first user (the admin), signs that browser in and runs the first-run wizard, whose Model step takes the LLM key (above; or set `ANTHROPIC_API_KEY` in env for the default models); phones, tablets, TVs and other browsers are paired in the wizard or on the web Devices page (`docs/devices.md`). With `API_TOKEN` set (a headless install) there is no setup mode: the first start creates the admin from the `OWNER_*` values below. Either way the addons that need no account are turned on for the admin once (`default_on`: tasks, shopping, knowledge, about, people, phone; `core/addons/store.py::connect_first_admin_defaults`); one the admin turns off stays off. Everything else is optional; addons (Home Assistant, WhatsApp, Google, Frigate…) are connected per user on the web UI's Addons page (Settings → Addons), with `bin/dana addons connect` or `PUT /api/addons/{name}`, never from env. `ops` (kubectl and Python on the server) is never turned on by itself.

## Security

| Variable | Purpose |
|---|---|
| `API_TOKEN` | optional, for a headless install: always a valid token of the first (admin) user, who is then created at the first start from the `OWNER_*` values (no setup mode). Unset: the admin is created on the setup page, and every app and browser has its own token from pairing (`docs/devices.md`); users' tokens live hashed in the DB (`docs/access.md`). Generate one: `openssl rand -hex 32` |
| `DANA_INSECURE_DEV` | `1` = local development without `API_TOKEN`: a request without credentials is the first admin (created at start from `OWNER_*`), the server listens on `127.0.0.1` only and logs a warning at start. Also (with or without `API_TOKEN`) lets phones register plain-`http` UnifiedPush endpoints (the local test environment's ntfy stub; `docs/notifications.md`). Never in production |
| `CREDENTIALS_KEY` | Fernet key that encrypts connection credentials in the `connections` table and the money addon's bank / card logins (`money_sources`, `docs/money.md`; `core/addons/crypto.py`). Optional: unset, the first start generates one into `<data dir>/credentials.key` (mode 0600, next to the database) and logs where it is; **back that file up with the database**. When set, the env value wins over the file (keep it in your secret store to keep the key outside the data directory). A missing, malformed or wrong key with stored credentials stops the start with a message saying what to do. Losing it loses the stored credentials (reconnect them). Generate one: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `WAHA_WEBHOOK_SECRET` | the shared secret WAHA signs each webhook with (HMAC-SHA512 of the body in `X-Webhook-Hmac`; set the same value as WAHA's `WHATSAPP_HOOK_HMAC_KEY`, `docs/whatsapp.md`). Unset: every unsigned `/webhook/waha` is refused (403), so incoming WhatsApp voice notes stop |
| `APP_PUBLISH_TOKEN` | Bearer token guarding `POST /api/app/upload` (Android APK publishing, for apps you build yourself). At least 24 characters and not the example's placeholder (`openssl rand -hex 32`); otherwise publishing is disabled and the start logs why |

## External services (set in .env)

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | aiogram bot; optional (unset: no Telegram channel, the web UI and apps still work) |
| `TELEGRAM_API_BASE` | optional Bot API server for the bot (aiogram `TelegramAPIServer.from_base`, e.g. a self-hosted `telegram-bot-api`); unset = api.telegram.org. The local test environment points it at its fake Bot API (`docs/local-testing.md`) |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` / … | key of each LLM provider in use (LiteLLM's standard names; by default Anthropic for every model; transcription: Models → Transcription); optional when an admin entered the key in the app (Models), and wins over that one when set; `OPENAI_API_KEY` also powers realtime voice (`core/realtime.py`). Startup logs each job's model and warns when its key is missing |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | the server's Google OAuth client, used by every Google addon (each user's refresh token is the credential of their Google addons, set by the Google sign-in: `docs/access.md` → Google account) |
| *(Google Cloud project of that client)* | needs the Calendar, People and Gmail APIs enabled and the `calendar`, `contacts`, `gmail.modify` and `gmail.settings.basic` scopes on its consent screen (`gmail.modify` is a restricted scope: the app is published unverified for its own few users, who pass Google's warning once); a user already signed in signs in again to grant a new addon's access |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | the server's Telegram app (my.telegram.org → API development tools), used by every user's `telegram_account` sign-in and reading (each user's session is the credential of their connection, `docs/telegram-account.md`); unset = the Connect Telegram page says the server has no app |
| `PUBLIC_URL` | the server's public address (e.g. `https://dana.example.com`): what a pairing QR code tells the app to connect to, the setup link in the log, the Google sign-in's redirect address (`core/public_url.py`); unset = the address each request came to (`X-Forwarded-Proto` / `X-Forwarded-Host` from the proxy in front, else the request's own), and `http://localhost:<PORT>` for the setup link |
| `HA_HOME_MAP_TTL_SECONDS` | age after which a user's home map in their prompt (exposed entity ids, names, aliases) is refreshed in the background (default 600; a failed refresh keeps the old map and retries within a minute) |
| `HA_WATCH_RETRY_MAX_SECONDS` / `HA_WATCH_RECONCILE_SECONDS` | Home Assistant entity watchers: longest back-off between reconnects (300; starts at 1 s, doubles), how often the set of watchers is checked against the connections (10) |
| `ALLOWED_TELEGRAM_USER_IDS` | comma-separated Telegram user ids of the first user, read once when that user is created (`docs/access.md`); while no user has a Telegram id, the bot refuses every sender (and tells them how to be linked). **Read once:** removable after the first start (then `bin/dana users telegram add / move / remove`); startup warns while it lists ids no user has |
| `TELEGRAM_CHAT_ID` | the first user's Telegram chat for proactive messages (reminders, briefing, weekly review, digest), read once when that user is created; after that each user's stored chat is used (`core/notify.py`, `docs/access.md`). **Read once:** removable after the first start (then `bin/dana users update USER --telegram-chat`) |
| `DATABASE_PATH` | SQLite path (defaults to `db.sqlite3`); its directory is the data dir for all other files. The schema comes from migrations applied at startup (`docs/operations.md` → Database schema) |
| `DB_BUSY_TIMEOUT_MS` | how long a connection waits for a locked database (default 30000) |
| `PORT` | web server (default 8080) |
| `DANA_SITE` | your deployment's site folder (`../docs/site.md`); default `site/` at the repository root |
| `DANA_CATALOG_URL` | where the addon catalog is read from (default `https://raw.githubusercontent.com/ronganam/dana-catalog/main/catalog.json`; while it cannot be read, the copy read from it earlier with a notice; `../../docs/addons.md`, "The catalog") |
| `APP_RELEASES_REPO` | the GitHub repository (`owner/name`) whose latest release's phone and watch apps the server offers as app updates (default `ronganam/dana`; read at start and every 6 hours through the public API, no token; `docs/operations.md` → App updates). Empty: only uploaded APKs are offered |
| `DANA_CATALOG_FILE` | a local catalog file instead of the URL (offline servers, tests) |
| `DANA_ADDON_DIRS` | folders of non-official addons, separated by `:` (`docs/writing-an-addon.md`), replacing the default sources; unset: the site's `addons/`, the image's `/app/addons-site` and `<data dir>/addons/` (each when it exists); empty: none (the tests) |
| `TIMEZONE` | the server's timezone (one for every user), e.g. `Asia/Jerusalem`; unset: the one an admin chose in the wizard's Home step (`server_settings['timezone']`), then the first admin's Home Assistant → `TZ` → UTC (`core/clock.py`) |
| `GEOCODING_URL` | the place search of the wizard's Home step and the Zones editor (`core/places.py`, `docs/zones.md`); default Open-Meteo's free geocoding (`https://geocoding-api.open-meteo.com/v1/search`, no key); empty = off (typed coordinates and "Use my location" still work) |
| `MEMBER_ALLOWED_ORIGINS` | optional, comma-separated `addon=origin` pairs (`homeassistant=https://ha.example.com,whatsapp=http://waha.waha:3000`): service addresses a user without `manage_users` may put in their own connection's address field (Home Assistant `host`, WAHA `url`), besides the origins the admins' own connections of that addon use. An origin is scheme + host + port (default ports dropped); anything else they enter is refused (400). Unset: only the admins' origins. A member WAHA allowed here still cannot claim a WhatsApp account another user's connection holds (`docs/whatsapp.md` → One connection per account). `cameras` is changed only by an admin, so it needs none (`docs/access.md` → Security rules) |
| `FRIGATE_MOTION_THRESHOLD` | % of the frame that must move in a 30 s bucket for a "Motion" card on the Camera widget's events rail (large size) (default `0` = off; raw motion also counts sensor noise and changing light, so start around 10 and raise it if empty scenes show up) |
| `ASSISTANT_MODELS` | optional JSON list overriding the chat model catalog (`core/models.py`) |
| `CALL_AUDIO_RETENTION_DAYS` | days call audio is kept (default 90) |
| `OWNER_NAME` / `OWNER_CONTEXT` / `OWNER_PRONOUN` / `OWNER_OTHER_NAMES` | the first user's name (default `Owner`) and profile: context (who they are and who the people they talk about are), pronoun (`he` / `she` / `they`, default `they`) and their first name in another script for transcripts; read once: when that user is created, or by migration `m0016_user_profile` for an existing first admin. Later: `bin/dana users update` |
| `OWNER_LANGUAGES` / `OWNER_CURRENCY` | the first user's languages (comma list of codes, the first is the default reply language, e.g. `he,en`) and currency (ISO 4217, e.g. `ILS`); read once when that user is created, a malformed value is skipped with a warning. Unset: the server's `DEFAULT_LANGUAGES` / `CURRENCY` apply. Later: the web Profile card or `bin/dana users update --languages / --currency` |
| `DEFAULT_LANGUAGES` | the languages of a user who chose none (default `en`; a comma list, e.g. `he,en`): they word the agent's language rule and the transcription prompt, the first one is what the Inbox summaries, the briefing, the weekly review and person briefs are written in, and the apps listen in them (push-to-talk; `core/user_locale.py`) |
| `CURRENCY` | the currency of a user who chose none (default `USD`; ISO 4217): what money values mean — the money addon's amounts (server text through `user_locale.format_money`, the web and the app from `/api/me`) and Projects' budgets and prices |
| `TYPESAFE_API_KEY` | optional: TypeSafe (Jev) key; set, notification triage asks TypeSafe instead of the model (cheaper, calibrated probabilities; `addons/phone/notifications/judges.py`), server-wide (every user's triage uses it). Unset, triage runs on the model (`NOTIFICATION_MODEL`, `docs/notifications.md` → Judges) |
| `TYPESAFE_MODEL` | TypeSafe model (default `jev-latest`) |
| `TYPESAFE_COST_PER_MTOK` | USD per million TypeSafe input tokens for the Usage screen (`core/usage.py`); default 0.042 (Jev's published price) |
| `USAGE_RAW_DAYS` / `USAGE_FLUSH_SECONDS` | Usage tracking: days of per-call rows kept before they are folded into daily totals (60); seconds between buffered writes (3) |
| `NOTIFICATION_RETENTION_DAYS` | days notifications are kept (default 30; ones a task came from are kept) |
| `NOTIFICATION_TRIAGE_BATCH` / `NOTIFICATION_TRIAGE_CONCURRENCY` | notification triage: notifications per model call (10; TypeSafe always takes one per request), triage calls running at once across users (4) |
| `NOTIFICATION_DIGEST_TIMES` | comma-separated `HH:MM` digest times (default `08:30,20:30`; empty = off) |
| `NOTIFICATION_ESCALATE` / `NOTIFICATION_ESCALATIONS_PER_HOUR` | re-announce urgent notifications on the phone (default on, 6/h) |
| `NOTIFICATION_ALERT_STYLE` | `screen` (default: full-screen alert on the phone, push event `alert`) or `notify` (a `⚡ Dana:` notification) |
| `FCM_SERVICE_ACCOUNT_JSON` / `FCM_PROJECT_ID` | optional: direct push through Firebase, only for phones running an app you built with your own Firebase project (`addons/phone/push.py`), server-wide (each event still reaches only its user's devices): service-account key allowed only to send FCM messages. Unset = FCM registrations are refused; prebuilt apps push through UnifiedPush (the ntfy app), which needs no setting |
| `NOTIFICATION_SUMMARY_MIN_ITEMS` / `NOTIFICATION_SUMMARY_MIN_SECONDS` | Inbox AI summaries: min notifications per app (2), min seconds between rewrites (45) |
| `ASSISTANT_DEFAULT_MODEL` | catalog id used when a chat request names no model (default: first entry, `fast`) |
| `DEFAULT_COUNTRY_CODE` | calling code (e.g. `972`, `44`) that replaces the leading 0 of a local phone number in People, Contacts and the WhatsApp tools; server-wide, no default (unset: a number stays as typed) |
| `CONTACTS_SYNC_MINUTES` | minutes between two syncs of every user's Google Contacts copy (60; `docs/contacts.md`) |
| `MONEY_WORKER_URL` | the `money_il` catalog addon's bank worker (default `http://127.0.0.1:8090`: the same pod; that addon's README). Its token is a file Dana creates, `money/worker-token` next to the DB, not an env value |
| `MONEY_SYNC_TIMES` | comma-separated local `HH:MM` slots when every money source syncs (default `07:00,19:00`; each source moved by a stable ±20 min) |
| `PEOPLE_BRIEF_INTERVAL_SECONDS` / `PEOPLE_BRIEF_BATCH` / `PEOPLE_DRIFT_MIN_DAYS` | people briefs: loop interval (600), rewrites per pass per user (6); minimum days before someone counts as drifting (14) |
| `WAKE_CONFIRM_SECONDS` / `WAKE_MIN_SCREEN_SECONDS` | wake: check delay after an unlock (600), screen-on needed since it (90); the wake window is per user (Profile, learned: `docs/signals.md`) |
| `SIGNAL_ACTIVE_HOURS` / `SIGNAL_LATE_MINUTES` | signals: how long a source stays the active one after its last event (72), how late an event may arrive and still fire a reminder (30) |
| `OPEN_METEO_URL` | the Open-Meteo forecast endpoint (default `https://api.open-meteo.com/v1/forecast`) |
| `WAKE_FALLBACK_SECONDS` / `WAKE_FALLBACK_SESSIONS` / `WAKE_POLL_SECONDS` | wake fallback poll: screen-on in the last 30 min (360) across N stretches (3), poll interval (300) |
| `BRIEFING_FALLBACK_TIME` / `BRIEFING_TELEGRAM` | wake briefing (written in the user's first language): make-anyway time (10:00), also send to Telegram (0); the weather entity is the user's Home Assistant setting `weather_entity` |
| `MEETING_CARDS` / `MEETING_CARD_MINUTES` | push a person's card before a meeting with them (on, 10 min) |
| `WEEKLY_REVIEW_AT` / `REVIEW_STALE_DAYS` / `REVIEW_SNOOZE_MIN` / `REVIEW_SUGGESTION_DAYS` / `REVIEW_MAX_ITEMS` / `REVIEW_TELEGRAM` | weekly review: when (`sat 20:00`), item thresholds (21 days, 3 snoozes, 7 days), max items (25), Telegram message (on) |
| `WEEK_START` | the first day of the week (`sun`…`sat`): money's weeks (this / last week, weekly insights), the weekly review's "next week" and the bank days of the balance forecast. Unset: the day after `WEEKLY_REVIEW_AT`'s day, so the default review on Saturday evening gives weeks from Sunday; set `WEEKLY_REVIEW_AT=sun 20:00` (or `WEEK_START=mon`) for weeks from Monday (`core/clock.py`) |
| `THREAD_GAP_MINUTES` / `THREAD_KEEP_MESSAGES` | the shared thread (`session_id: "main"`): quiet gap after which the model's context starts fresh (20), messages kept for display (500) |
| `SHORTCUTS` | `0` turns learned phrases off, both learning and use (default on) |
| `UNDO_TTL_SECONDS` | how long an Undo stays usable, in memory (900) |
| `SUGGESTION_POPUP_NOTIFICATIONS` | `1` = suggestions that come from notifications also open the phone's suggestions pop-up (default 0: they wait quietly in Now) |
| `AGENT_SESSIONS_PUSH_INTERVAL` | seconds between pushes of a coding-agent session's activity to the phone, per user (default 15; status changes go at once; `docs/notifications.md` "Coding-agent sessions") |
| `NOW_MAX_NOTIFICATIONS` / `NOW_MAX_SUGGESTIONS` / `NOW_MAX_REMINDERS` | cards per kind in Now (3 / 4 / 4; further call suggestions go to the quiet list) |
| `NOW_QUIET_CONFIDENCE` / `NOW_REMINDER_MAX_HOURS` / `NOW_CALENDAR_WAIT_SECONDS` | call suggestions below this confidence go to the quiet list (0.7); how long a fired, unhandled reminder stays in Now (48); longest wait for the calendar on a cold cache (1.5) |
| `CHECK_PUSH_FETCH_HOURS` / `CHECK_NOTIFICATION_SYNC_HOURS` / `CHECK_LOCATION_DAYS` / `CHECK_CAR_DAYS` / `CHECK_WAKE_DAYS` / `CHECK_CALLS_DAYS` | self-check: how long each signal may stay quiet before its check fails (24 h, 12 h, 3, 7, 3, 7 days) |
| `CHECK_HA_WATCHER_MINUTES` | self-check: how long a user's Home Assistant watcher may stay disconnected before `ha_watcher` fails (10) |
| `ABOUT_LEARN` / `ABOUT_LEARN_INTERVAL_MINUTES` / `ABOUT_LEARN_QUIET_MINUTES` / `ABOUT_LEARN_MAX_ITEMS` / `ABOUT_LEARN_LOOKBACK_DAYS` | About-you learner: on/off (1), minutes between a user's passes (30; doubled per failure of that user, max 6 h), quiet gap after which a conversation counts as finished (20), conversations + calls per model call (8), how far back the first pass looks (14 days). It runs on `BRIEFING_MODEL` |
| `ABOUT_MAX_CHARS` / `ABOUT_MAX_FACTS` | size cap of everything Dana knows about one user (4000 characters, 80 facts; per user) |
