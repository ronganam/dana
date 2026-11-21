# Configuration (environment variables)

Local: `.env` (gitignored, from `.env.example`). Production: the same names as environment variables, from whatever secret store you use (a Kubernetes Secret, Infisical, Vault, …); the process reads them at start. Never print or commit secret values.

## Models

Every model is a LiteLLM id (`provider/model`: `anthropic/claude-haiku-5-5`, `gemini/gemini-3.1-flash-lite`, `openai/gpt-5-mini`…), so switching provider is an env change plus that provider's key. All calls go through `core/llm.py` (the web/app chat through the profile catalog in `core/models.py`). Unset → Claude: `anthropic/claude-sonnet-5-5` for `MODEL` and `SEARCH_MODEL`, `anthropic/claude-haiku-5-5` for the background jobs; `TRANSCRIBE_MODEL` → `gemini/gemini-3.1-flash-lite` (Claude takes no audio).

| Variable | Used for |
|---|---|
| `MODEL` | Telegram + trigger agent runs, small helpers (task emoji/group, WhatsApp name matching, voice-note and media summaries) |
| `BACKGROUND_MODEL` | default for the structured jobs below |
| `CALL_ANALYSIS_MODEL` | call analysis (→ `BACKGROUND_MODEL`) |
| `NOTIFICATION_MODEL` | notification → task/event extraction and Inbox summaries (→ `BACKGROUND_MODEL`) |
| `BRIEFING_MODEL` | people briefs, wake briefing, weekly review (→ `BACKGROUND_MODEL`) |
| `SHOPPING_MODEL` | the shopping aisle of new item names (→ `BACKGROUND_MODEL`; without its key items stay under Other, `docs/shopping.md`) |
| `MONEY_MODEL` | money transaction categories and clean merchant names (→ `BACKGROUND_MODEL`; without its key, rows the rules cannot settle stay uncategorized, `docs/money.md`) |
| `TELEGRAM_SUMMARY_MODEL` | summaries of the user's unread Telegram chats (→ `BACKGROUND_MODEL`, `docs/telegram-account.md`) |
| `SEARCH_MODEL` | web-grounded answers through LiteLLM's `web_search_options`: the money price checks (→ `MODEL`; needs a model with web search: Claude's web search tool, Gemini's Google Search grounding…; without one the price checks are unavailable, `docs/money.md` → Price checks) |
| `TRANSCRIBE_MODEL` | all audio → text (voice notes, call recordings, voice chat, media). Must hear audio: a chat model with audio input (Gemini) or a speech-to-text model (`openai/gpt-4o-transcribe`, `deepgram/nova-3`…). Claude has no audio input |
| `ASSISTANT_MODELS` / `ASSISTANT_DEFAULT_MODEL` | chat profiles for the web/app/Raycast picker (below) |
| `DEEP_MODEL` | the `deep` profile's model for LLM purposes that default to it (→ `anthropic/claude-opus-5-5`); `fast` purposes use `BACKGROUND_MODEL`, `balanced` ones `MODEL` |
| `MODEL_<PURPOSE>` | the model of one LLM purpose alone (`MODEL_TASK_EMOJI`, `MODEL_PEOPLE_BRIEF`…), above every variable here. Purposes: core's in `core/purposes.py`, each addon's in its manifest (`llm_jobs=`); an unknown purpose is an error, not a fallback |

Chat profiles (`core/models.py`): `fast` Claude Haiku 5.5 at low effort (thinking hidden), `balanced` Claude Sonnet 5.5 and `deep` Claude Opus 5.5, both at high effort with their thinking summary streamed (LiteLLM turns `reasoning_effort` into adaptive thinking, a summarized display and `output_config.effort`; Sonnet at medium often answers without thinking, so nothing would show). Telegram / trigger runs on a Claude `MODEL` think at medium.

Claude notes: Claude 5.5 models always think and the thinking counts against `max_tokens`, so `llm.claude_kwargs` gives each job an effort (`chat` helpers low, `search` medium, the rest the model's default) and raises a tiny `max_tokens` to 2048. The agent marks the tool list as a cache breakpoint (`llm.cacheable_tools`; Gemini/OpenAI cache on their own). Thinking blocks are sent back within a turn's tool loop but never stored: Claude binds each block to the exact conversation it came from, and the next turn's system prompt (it holds the time) differs, so a replayed block would be refused. Unsupported sampling params (`temperature` on these models) are dropped. Histories written by Gemini work as they are (LiteLLM cleans its tool-call ids).

## Addon credentials

| Variable | Purpose |
|---|---|
| `CREDENTIALS_KEY` | Fernet key that encrypts connection credentials in the `connections` table (`core/addons/crypto.py`). Required once any connection has credentials or credentials are waiting to be imported from env (below); then a missing, malformed or wrong key stops the start with a message saying what to do. Not needed while nothing is protected. Also encrypts the money addon's bank / card logins (`money_sources`, `docs/money.md`). Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; keep it in your secret store. Losing it loses the stored credentials (reconnect them) |

**Imported into the first admin's connections** (`addons/bootstrap.py`, at startup, once per addon; an addon the admin disconnects is not re-created). Each value is the env value (unset: the default named below, or nothing). **Read once:** after the first start that recorded an addon (`bin/users.py addons show <admin>` lists it; `connection_imports` holds the record), its env names below are never read again and can be removed from the environment; change the values on the web UI's Addons page (Settings → Addons), with `bin/users.py addons update` or `PUT /api/addons/{name}` instead.

| Kind | Connected when | Credentials | Settings |
|---|---|---|---|
| `homeassistant` | `HOMEASSISTANT_TOKEN` is set | `HOMEASSISTANT_HOST` (default `http://homeassistant.local:8123`), `HOMEASSISTANT_TOKEN` | `PRIMARY_PERSON` → `person`, `PHONE_INTERACTIVE_SENSOR` → `phone_interactive_sensor`, `PHONE_BATTERY_SENSOR` → `phone_battery_sensor`, `PHONE_NOTIFY_SERVICE` → `phone_notify_service` (unset: left empty), `BRIEFING_WEATHER_ENTITY` → `weather_entity` (`weather.forecast_home`); `car_sensor` / `car_device` have no env name (set them on the Addons page). Nothing else reads these env names |
| `whatsapp` | `WAHA_URL` is set | `WAHA_URL` → `url`, `WAHA_API_KEY` (or `WAHA_TOKEN`) → `api_key`, `WAHA_SESSION` → `session` (default `default`) | `WHATSAPP_VOICE_TELEGRAM_CHAT_ID` → `voice_telegram_chat_id` (where the user's voice-note transcripts go). The account (`addons.account`) is learned from WAHA, not from env. Nothing else reads these env names |
| `calendar` | `GOOGLE_REFRESH_TOKEN` is set | `GOOGLE_REFRESH_TOKEN` → `refresh_token` | `GOOGLE_CALENDAR_ID` → `calendar_id` (default `primary`: where new events go); `selected_calendars` (the calendars shown and searched) has no env name: it was the `memories` row of the same name, which migration `m0012_calendar_per_user` hands to this import (or writes into the connection when it already exists). Nothing else reads these env names |
| `cameras` | `FRIGATE_URL` is set | — | `FRIGATE_URL` → `url`. Nothing else reads the env name |
| `tasks`, `shopping`, `knowledge`, `about`, `people`, `phone`, `ops` | always (built in) | — | — |

No kind reads these env values for its own configuration: each reads only its connection. `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` stay server-wide.

## External services (set in .env)

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | aiogram bot; optional (unset: no Telegram channel, the web UI and apps still work) |
| `TELEGRAM_API_BASE` | optional Bot API server for the bot (aiogram `TelegramAPIServer.from_base`, e.g. a self-hosted `telegram-bot-api`); unset = api.telegram.org. The local test environment points it at its fake Bot API (`docs/local-testing.md`) |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` / … | key of each LLM provider in use (LiteLLM's standard names; by default Anthropic for every model and Gemini for transcription); `OPENAI_API_KEY` also powers realtime voice (`core/realtime.py`). Startup logs each job's model and warns when its key is missing |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | the server's Google OAuth client, used by every Google addon (each user's refresh token is the credential of their Google addons, set by the Google sign-in: `docs/access.md` → Google account; `GOOGLE_REFRESH_TOKEN` is only imported once for the first admin's `calendar`, above) |
| *(Google Cloud project of that client)* | needs the Calendar, People and Gmail APIs enabled and the `calendar`, `contacts`, `gmail.modify` and `gmail.settings.basic` scopes on its consent screen (`gmail.modify` is a restricted scope: the app is published unverified for its own few users, who pass Google's warning once); a user already signed in signs in again to grant a new addon's access |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | the server's Telegram app (my.telegram.org → API development tools), used by every user's `telegram_account` sign-in and reading (each user's session is the credential of their connection, `docs/telegram-account.md`); unset = the Connect Telegram page says the server has no app |
| `PUBLIC_URL` | the server's public address (e.g. `https://dana.example.com`), used for the Google sign-in's redirect address; unset = the address each request came to |
| `HA_HOME_MAP_TTL_SECONDS` | age after which a user's home map in their prompt (exposed entity ids, names, aliases) is refreshed in the background (default 600; a failed refresh keeps the old map and retries within a minute) |
| `HA_WATCH_RETRY_MAX_SECONDS` / `HA_WATCH_RECONCILE_SECONDS` | Home Assistant entity watchers: longest back-off between reconnects (300; starts at 1 s, doubles), how often the set of watchers is checked against the connections (10) |
| `ALLOWED_TELEGRAM_USER_IDS` | comma-separated Telegram user ids of the first user, read once when that user is created (`docs/access.md`); empty = the bot is public and everyone is the first user. **Read once:** removable after the first start (then `bin/users.py telegram add / move / remove`); startup warns while it lists ids no user has |
| `TELEGRAM_CHAT_ID` | the first user's Telegram chat for proactive messages (reminders, briefing, weekly review, digest), read once when that user is created; after that each user's stored chat is used (`core/notify.py`, `docs/access.md`). **Read once:** removable after the first start (then `bin/users.py update USER --telegram-chat`) |
| `DATABASE_PATH` | SQLite path (defaults to `db.sqlite3`); its directory is the data dir for all other files. The schema comes from migrations applied at startup (`docs/operations.md` → Database schema) |
| `DB_BUSY_TIMEOUT_MS` | how long a connection waits for a locked database (default 30000) |
| `PORT` | web server (default 8080) |
| `DANA_SITE` | your deployment's site folder (`../docs/site.md`); default `site/` at the repository root |
| `DANA_ADDON_DIRS` | folders of non-official addons, separated by `:` (`docs/writing-an-addon.md`), replacing the default sources; unset: the site's `addons/`, the image's `/app/addons-site` and `<data dir>/addons/` (each when it exists); empty: none (the tests) |
| `TIMEZONE` | the server's timezone (one for every user), e.g. `Asia/Jerusalem` (falls back to the first admin's Home Assistant → `TZ` → UTC) |
| `API_TOKEN` | always a valid token of the first (admin) user for the web server/API; other users' tokens live hashed in the DB (`docs/access.md`); unset = auth disabled, every request is the first user (local dev only) |
| `INTERNAL_HOSTS` | comma-separated `Host` values (the in-cluster Service names) allowed to post to the inbound webhook `/webhook/waha` (WAHA) without a token; every other path needs a token from any host. With a token, only a user with `manage_server` may post to it (anyone else 403) |
| `MEMBER_ALLOWED_ORIGINS` | optional, comma-separated `addon=origin` pairs (`homeassistant=https://ha.example.com,whatsapp=http://waha.waha:3000`): service addresses a user without `manage_users` may put in their own connection's address field (Home Assistant `host`, WAHA `url`), besides the origins the admins' own connections of that addon use. An origin is scheme + host + port (default ports dropped); anything else they enter is refused (400). Unset: only the admins' origins. A member WAHA allowed here still cannot claim a WhatsApp account another user's connection holds (`docs/whatsapp.md` → One connection per account). `cameras` is changed only by an admin, so it needs none (`docs/access.md` → Security rules) |
| `APP_PUBLISH_TOKEN` | Bearer token guarding `POST /api/app/upload` (Android APK publishing) |
| `FRIGATE_MOTION_THRESHOLD` | % of the frame that must move in a 30 s bucket for a "Motion" card on the dashboard events rail (default `0` = off; raw motion on this camera is mostly night IR noise, so the activity strip uses dog detections instead) |
| `ASSISTANT_MODELS` | optional JSON list overriding the chat model catalog (`core/models.py`) |
| `CALL_AUDIO_RETENTION_DAYS` | days call audio is kept (default 90) |
| `OWNER_NAME` / `OWNER_CONTEXT` / `OWNER_PRONOUN` / `OWNER_OTHER_NAMES` | the first user's name (default `Owner`) and profile: context (who they are and who the people they talk about are), pronoun (`he` / `she` / `they`, default `they`) and their first name in another script for transcripts; read once: when that user is created, or by migration `m0016_user_profile` for an existing first admin. Later: `bin/users.py update` |
| `TYPESAFE_API_KEY` | TypeSafe (Jev) key for notification triage (`addons/phone/notifications/triage.py`), server-wide (every user's triage uses it); unset = notifications stay untriaged |
| `TYPESAFE_MODEL` | TypeSafe model (default `jev-latest`) |
| `TYPESAFE_COST_PER_MTOK` | USD per million TypeSafe input tokens for the Usage screen (`core/usage.py`); default 0.042 (Jev's published price) |
| `USAGE_RAW_DAYS` / `USAGE_FLUSH_SECONDS` | Usage tracking: days of per-call rows kept before they are folded into daily totals (60); seconds between buffered writes (3) |
| `NOTIFICATION_RETENTION_DAYS` | days notifications are kept (default 30; ones a task came from are kept) |
| `NOTIFICATION_DIGEST_TIMES` | comma-separated `HH:MM` digest times (default `08:30,20:30`; empty = off) |
| `NOTIFICATION_ESCALATE` / `NOTIFICATION_ESCALATIONS_PER_HOUR` | re-announce urgent notifications on the phone (default on, 6/h) |
| `NOTIFICATION_ALERT_STYLE` | `screen` (default: full-screen alert on the phone, push event `alert`) or `notify` (a `⚡ Dana:` notification) |
| `FCM_SERVICE_ACCOUNT_JSON` / `FCM_PROJECT_ID` | direct push to the phone (`addons/phone/push.py`), server-wide (each event still reaches only its user's devices): service-account key allowed only to send FCM messages; unset = events are only queued |
| `NOTIFICATION_SUMMARY_MIN_ITEMS` / `NOTIFICATION_SUMMARY_MIN_SECONDS` | Inbox AI summaries: min notifications per app (2), min seconds between rewrites (45) |
| `ASSISTANT_DEFAULT_MODEL` | catalog id used when a chat request names no model (default: first entry, `fast`) |
| `DEFAULT_COUNTRY_CODE` | country code for local phone numbers in People and the WhatsApp tools (default `972`; server-wide) |
| `CONTACTS_SYNC_MINUTES` | minutes between two syncs of every user's Google Contacts copy (60; `docs/contacts.md`) |
| `MONEY_WORKER_URL` | the bank-worker sidecar (default `http://127.0.0.1:8090`: the same pod; `docs/money.md`). Its token is a file Dana creates, `money/worker-token` next to the DB, not an env value |
| `MONEY_SYNC_TIMES` | comma-separated local `HH:MM` slots when every money source syncs (default `07:00,19:00`; each source moved by a stable ±20 min) |
| `PEOPLE_BRIEF_INTERVAL_SECONDS` / `PEOPLE_BRIEF_BATCH` / `PEOPLE_DRIFT_MIN_DAYS` | people briefs: loop interval (600), rewrites per pass per user (6); minimum days before someone counts as drifting (14) |
| `WAKE_CONFIRM_SECONDS` / `WAKE_MIN_SCREEN_SECONDS` / `WAKE_WINDOW_START_HOUR` / `WAKE_WINDOW_END_HOUR` | wake: check delay after an unlock (600), screen-on needed since it (90), window (5–12) |
| `WAKE_FALLBACK_SECONDS` / `WAKE_FALLBACK_SESSIONS` / `WAKE_POLL_SECONDS` | wake fallback poll: screen-on in the last 30 min (360) across N stretches (3), poll interval (300) |
| `BRIEFING_LANGUAGE` / `BRIEFING_FALLBACK_TIME` / `BRIEFING_TELEGRAM` | wake briefing: language (English), make-anyway time (10:00), also send to Telegram (0); the weather entity is the user's Home Assistant setting `weather_entity` |
| `MEETING_CARDS` / `MEETING_CARD_MINUTES` | push a person's card before a meeting with them (on, 10 min) |
| `WEEKLY_REVIEW_AT` / `REVIEW_STALE_DAYS` / `REVIEW_SNOOZE_MIN` / `REVIEW_SUGGESTION_DAYS` / `REVIEW_MAX_ITEMS` / `REVIEW_TELEGRAM` | weekly review: when (`sat 20:00`), item thresholds (21 days, 3 snoozes, 7 days), max items (25), Telegram message (on) |
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
