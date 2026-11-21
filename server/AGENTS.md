# Dana server — Agent Guide

The server of **Dana**, a personal assistant: aiohttp server + Telegram bot + web UI, one process (one pod), several users with their own addons. This file is the map; each area's details live in `docs/` — read the section you need of the matching doc before changing that area. **How to work fast** (the test loop, the local environment, parallel agents and their briefs, shipping): `docs/development.md`. Paths below are relative to `server/`; the repository map is `../AGENTS.md`.

## Clients

Clients of this server's payloads: the Android app / tablet / TV / watch (`../android/`, its own `AGENTS.md`; REST/SSE/WebSocket with a Bearer token), the web UI in `static/`, and the Raycast extension (`../extensions/raycast-assistant/`, its `README.md`). Change a payload only on purpose, and keep them in mind: a change that needs the app too lands in the same commit.

## Quick start

```bash
python3 -m venv .venv            # Python 3.12+ (3.14 is what the image and the tests run)
.venv/bin/pip install -r requirements.lock   # then always use .venv/bin/python (system python lacks deps)
cp .env.example .env  # required: ANTHROPIC_API_KEY and API_TOKEN; optional: GEMINI_API_KEY (transcription), TELEGRAM_BOT_TOKEN
bin/test                       # the whole suite in parallel (~1.5 min)
python main.py                 # run Telegram bot + web UI on :8080
```

No linter/formatter/typecheck — only `pyrightconfig.json` (points to `.venv`). Python 3.14 is tested (the container, `Dockerfile`, and the local venv); 3.12+ is supported (no newer syntax or library). Dependencies: `requirements.txt` (direct) → `requirements.lock` (pinned, what the image installs; `docs/operations.md` → Dependencies). Run `git config core.hooksPath .githooks` once (from the repository root): pushing master then runs the tests first (`docs/operations.md`).

## Tests

- **`bin/test`** runs every script side by side and prints only failures (a failure is retried once alone and reported as flaky if it then passes). `bin/test --changed` runs only the scripts your diff touches or that import what it changed (seconds; use it while working), `bin/test shopping users` picks by name, `bin/test --quick` leaves out the slow import smoke test, **`bin/test --list` says what each script covers** (the first line of its docstring). Run the whole `bin/test` once before committing; the pre-push hook runs it again before a deploy (`docs/operations.md`).
- Scripts are standalone, **not** pytest: `test_*.py`, plus `test_web_*.mjs` for the web pages' pure parts (Node). Where they live: core tests in `tests/`, each addon's in `addons/<name>/tests/`, the bank worker's `.mjs` in `bank-worker/test/`, a site addon's in its own `tests/`; fixtures in `tests/fixtures/`; `bin/test` finds them all and runs each from `server/` with `server/` and `tests/` on `PYTHONPATH`. Each Python script starts with `import testenv; testenv.isolate("<name>")` before importing the app or LiteLLM (`tests/testenv.py`): LiteLLM does not load `.env`, every addon / identity env value is removed (a test sets fake ones itself), and the temporary database directory is removed at exit — real local credentials never reach a test database. In-process fakes shared by tests: `tests/testfakes.py` (`FakeHA`: Home Assistant REST + WebSocket; `FakeWAHA`: WAHA).
- A new script: `test_<area>.py` (in the addon's `tests/`, or `tests/` for core) with a docstring whose first line says what it covers — that is the list, there is none to update. Assert on data, not only on status codes. Per-user features get two users with the addon plus one without (isolation both ways, 403 `not_connected`).
- Captured fixtures, regenerated only on purpose: `WRITE_PARITY=1 bin/test test_parity` (the first user's tool lists and prompts, `tests/fixtures/parity/`), `WRITE_GOLDEN=tests/fixtures/golden.sse bin/test test_run_stream` (the web stream contract), `WRITE_WEB_FIXTURE=1 bin/test test_addons_api` / `test_shopping_users` / `test_money_page` (the web pages' payloads). `tests/fixtures/db_8_1_0.sql` is a synthetic 8.1.0 database for migration tests.
- `tests/test_agent.py` runs the full ReAct loop against the real `MODEL` (needs its key); it is not part of `bin/test`: `PYTHONPATH=.:tests .venv/bin/python tests/test_agent.py`.
- **Local test environment** (Docker): `bin/local-env.sh up` builds the real image and starts it from the 8.1.0 database against stub services (`tests/local_env/`), `bin/local-env.sh test` runs the end-to-end rehearsal; with `DANA_LOCAL_SOURCE=1` it runs your working tree and `bin/local-env.sh restart` reloads a change in seconds → `docs/local-testing.md`. `scratch/` holds ad-hoc scripts (not tests).

## Architecture

- **Entrypoint** `main.py`: refuse to start without `API_TOKEN` (unless `DANA_INSECURE_DEV=1`), migrate, users bootstrap, addons bootstrap (credentials key or generated key file + the first admin's default addons, once), the conversation core, the Telegram adapter, the aiohttp web server (port 8080) and its background loops.
- **Agent loop** `core/agent.py`: a custom ReAct loop over LiteLLM (no LangChain). Every call is streamed (`stream=True`, assembled with `litellm.stream_chunk_builder`); deltas and tool announcements are emitted as they arrive, a broken stream is retried, consecutive read-only tool calls run concurrently. The system message is the acting user's: their profile, the parts of the addons they have connected, cached per-turn sections (home map, About facts) and the date line.
- **Conversation core** `core/session_runner.py` (starts every run for an explicit `User`), `core/session.py`, `core/thread.py` (the shared `main` thread), `core/history.py` (pruning) — all keyed by user. **Runs** `core/runs.py`: each piece of agent work is a run with an in-memory event log; clients reattach with `GET /api/sessions/{id}/stream?run_id=&after=`, disconnecting never cancels, one run per session per user (409 `active_run`). Event types at the top of `core/runs.py`. **Model profiles** `core/models.py` (`fast` / `balanced` / `deep`).
- **Addons** (`addons/`, framework in `core/addons/`; design: `../docs/addons.md`): each addon is a package `addons/<name>/` — `addon.py` declares it as `addon = Addon("<name>", ...)` (credentials, settings, hooks such as `on_connect`, `prompt_parts`, `check`, `options`), `tools.py` its tools (`@addon.tool()`), every tool taking the user's `Connection` as first parameter `conn`, `tests/` its test scripts. Another module refers to an addon's object as `from addons.<name>.addon import addon as <name>_addon`. Tools are listed and run **for a user**, only from addons that user has connected (a user's instance of an addon is a **connection**: table `connections`, credentials encrypted with `CREDENTIALS_KEY`; `store.connect / update / disconnect / get / require`). Adding a tool: a function in the addon's `tools.py`; adding an addon: a package + one line in `addons/__init__.py` → `docs/access.md` (Addon), `docs/writing-an-addon.md`.

  | Addon | What | Doc |
  |---|---|---|
  | essentials | time, selection, web search, `share_item`; everyone has it | `docs/access.md` |
  | tasks | lists, tasks, links, trigger engine; sharing | `docs/tasks-and-triggers.md` |
  | shopping | one list per user, aisles, invitations, live stream + push; sharing | `docs/shopping.md` |
  | knowledge | projects (date, budget) with sections — decisions whose options are compared in columns with a summary, or notes — entries, files on entries (uploads, WhatsApp, transcripts), FTS search, link media; sharing; the web Projects panel (`static/projects*.js`) | `docs/knowledge.md` |
  | about | short facts about the user, always in their prompt, learned in the background | `docs/about.md` |
  | people | people, numbers, briefs, the incoming-call card | `docs/people.md` |
  | phone | devices + direct push, notifications Inbox, call recordings → suggestions | `docs/notifications.md`, `docs/calls.md` |
  | homeassistant | smart-home control, home map, the entity watcher (location / car triggers) | `docs/access.md` (Home Assistant triggers) |
  | whatsapp | WAHA per connection: read (message ids, files by name), send, forward any received message or file by id, inbound voice notes routed by account | `docs/whatsapp.md` |
  | calendar | Google Calendar per connection; one Google sign-in (`addons/google_account/`) connects every Google addon | `docs/access.md` (Addons, Google account) |
  | contacts | Google Contacts: a per-user copy synced from the People API; search / read tools, add / update ask first; fills in people and unknown callers | `docs/contacts.md` |
  | gmail | Gmail in chat (not in voice): search / read, send and reply ask first, drafts / archive / read state / trash with Undo; mail content is untrusted | `docs/gmail.md` |
  | telegram_account | your own Telegram chats and channels, read-only (Telethon, QR sign-in at `/telegram/connect`): list / read / summarize unread, muted and archived skipped; never marks read; not the bot | `docs/telegram-account.md` |
  | money | bank / card logins (sources, credentials encrypted) synced by the bank-worker sidecar (`bank-worker/`), SMS sign-in codes from the phone, transactions, categories, dashboards (fixed / yearly / everyday / one-off spending, cash flow, the balance forecast without one-offs, insights: `addons/money/dashboard.py`), Psagot Trade portfolios read-only through a quick-view device (`addons/money/portfolio.py`, `bank-worker/src/psagot.js`), the Pluxee (Cibus) meal card read-only on the browser (budget, cycles, meals, never spending; each cycle's overflow matched to its monthly card line: `addons/money/meals.py`, `bank-worker/src/pluxee.js`), ways to save and spending to watch with the user's choices, targets and verified outcomes (`addons/money/savings.py`; Bank of Israel rates `reference.py`; price checks on request through `llm.search`: `market.py`), the web Money tab (`static/money*.js`, with the Save view: ways to save, spending to watch, price checks, targets; the Meal card view) | `docs/money.md` |
  | cameras | Frigate behind `/api/frigate/*` (no tools) | `docs/dashboard.md` |
  | ops | read-only kubectl + Python on the server that asks first (admin only, opt-in; not a sandbox) | `docs/access.md` (Security rules) |
  | *site addons* | a deployment's own addons, in its site's `addons/` (or `DANA_ADDON_DIRS`); a tested example in `../examples/addons/hello/` | `docs/writing-an-addon.md` |

- **Database** SQLite through `core/db` (`get_db_connection()`); ordered migrations in `core/db/migrations/` (`mNNNN_<name>.py`, found by name, each docstring says what it changes), applied at startup after a snapshot → `docs/operations.md` (Database schema).
- **Users and access** (`core/users.py`, `core/access.py`, `core/auth.py`): every web request and Telegram update resolves to a `User`, bound as the current user (`users.acting_as`); sessions, runs, approvals, undo, learned phrases, usage and every background loop are per user. Levels `admin` / `member`; a tool the user lacks is refused like a made-up name; server-wide endpoints use `@requires(capability)`. Managing users: `bin/users.py`, `/api/addons*` and the web Addons page. Security rules (cross-user) → `docs/access.md`.
- **Web server** aiohttp, not FastAPI: `core/server.py` assembles the app (middlewares, `ROUTE_MODULES`, background loops); each route lives in the module that owns it — `core/<area>_api.py` (pages, sessions, assistant, debug, actions, now, briefing, usage, sharing, addons) or an addon's `addons/<name>/api.py`, each with `setup(app)`; static web UI in `static/` (the app's own scripts are `static/app-*.js` + `static/app.js`: classic scripts sharing one global scope, listed and ordered in `static/app.js`'s header; panels such as `inbox.js` / `shopping.js` are self-contained). **Telegram** `core/telegram_bot.py` (aiogram): a channel adapter over the core. **Proactive messages** `core/notify.py` (Telegram); phone push `addons/phone/push.py`.
- **Composers** (`core/composers.py`): Now (`core/now.py`), wake (`core/wake.py`), briefing (`core/briefing.py`), weekly review (`core/weekly_review.py`), search (`core/search.py`), self-checks (`core/checks.py`), context chats, the dashboard endpoints — each works for one user from the addons they have → `docs/now.md`, `docs/people.md`, `docs/dashboard.md`.
- **Agent actions**: reversible writes run at once with Undo (`core/undo.py`); only `confirm` tools ask; learned phrases (`core/shortcuts.py`); on-screen context (`core/device_screen.py`) → `docs/agent-actions.md`.
- **LLM calls**: every non-chat call goes through `core/llm.py` (`complete()`, `complete_json()`, `transcribe()`, pass `purpose=`), one model env var per job → `docs/configuration.md`. Realtime voice (`core/realtime.py`, OpenAI's protocol) is the one exception. **Usage and cost** `core/usage.py` → `docs/usage.md`.
- **Persona**: the assistant is **Dana**; the prompt is the acting user's (`core/user_profile.for_user(user)` for their name / pronoun / context), and an addon's words live in its `addon.py`.
- **Android app updates**: the server hosts APKs (`core/app_updates.py`, `/api/app/*`); publishing happens from the Android repo (`bin/deploy-app.sh`).

## Key conventions

- `@addon.tool()` (in the addon's `tools.py`) auto-generates the OpenAI-compatible JSON schema from Pydantic type hints + Google-style docstrings; the first parameter is always `conn: Connection` (left out of the schema). Code that calls a tool function directly passes `store.require(user, "<name>")`.
- Only `confirm` tools return `{"status": "pending_approval"}` (Approve/Deny in the app, web and Telegram); every other write runs at once and registers an undo (`core/undo.py`, `POST /api/undo/{id}` in `core/actions_api.py`). A tool the user does not have never asks (refused like an unknown name).
- `is_write` / `confirm` / `acts_outside` flags are given to `@addon.tool`; `registry.needs_confirmation(name, args)` (`core/addons/registry.py`) decides in the agent loop. `untrusted_result=True` marks a tool whose result is text written by other people (the Gmail reads): for the rest of that turn `acts_outside` tools ask first.
- `escape_markdown()` from `core.text` required for Telegram-safe strings.
- **Schema**: only through a new migration in `core/db/migrations/` (never `CREATE TABLE` / `ALTER TABLE` in a module, never at import time).
- **Addon config**: whose account / which entities belongs to the user's connection (credentials encrypted, settings JSON); server-wide settings stay in env. Nothing reads an addon's account from env: connections are made on the Addons page, with `bin/users.py` or the API.
- **Time**: the container runs in UTC. Never call `datetime.now()` for anything user-facing — use `core.clock.now()` (aware, user's zone), `clock.from_timestamp()`, and `clock.describe_now()` for prompts. One zone for the server: `TIMEZONE` env → the first admin's Home Assistant → `TZ` → UTC (resolved once at startup).
- **Data paths**: everything persistent lives in the directory holding the DB (`core/paths.py`: `recordings/`, `media/`, `uploads/images/`, `app-updates/`, `snapshots/`). In k8s that is the PVC at `/app/db`; never write state elsewhere — it is lost on restart.
- **Tool results** are capped at `MAX_TOOL_RESULT_CHARS` (`core/agent.py::limit_tool_output`) and stored history at 40 messages / `MAX_HISTORY_CHARS` (`core/history.py::prune_history`, applied by `session.append_history`).
- **User context**: a tool runs only through `registry.execute(user, name, args)` (the run's / request's / handler's user, or `users.current()`), which refuses tools of addons that user has not connected (`registry.ToolNotAvailable`); decide anything about a tool call (asks? write?) only after `registry.has_tool(user, name)`. A handler that calls a tool function directly passes `store.require(user, "<name>")` and lets `NotConnected` reach the middleware (re-raise it before a catch-all `except`). Server-wide endpoints get `@requires("<capability>")`; never compare access level names in code. Shareable items go through `core/addons/sharing.py` (register an `ItemType`; lookups add `shared_ids`, writes check `access`, payloads add `shared_by` / `access` only for shared items). New background work takes a `User` and binds it with `users.acting_as`; never look up the first admin (`users.first_admin()` only for the env token / dev mode, the open Telegram bot, the server timezone, bootstrap / migrations, startup logging). A prompt about a user takes their words from `user_profile.for_user(user)`, never a name or pronoun in the text.
- **Web auth** (`core/auth.py`): every request needs `Authorization: Bearer <token>` (`API_TOKEN` or a user's DB token) or the `/login` session cookie, and gets `request["user"]`; except public paths (`/login`, `/api/health`, `/api/app/*`, `/static/*`, `/sw.js`, `/manifest.json`) and `/webhook/waha`, which takes no token but only events WAHA signed with `WAHA_WEBHOOK_SECRET` (no user set; the `Host` header is never trusted). No `API_TOKEN`: the server refuses to start; `DANA_INSECURE_DEV=1` (local development) makes every request the first admin and listens on 127.0.0.1 only.

## Deploy (summary — details in `docs/operations.md`)

- Commit, `git push origin master` → the pre-push hook runs `bin/test` (a failure stops the push) and, if the push changed the app and the site has a `deploy.env` (`../site/`, `../docs/site.md`), `bin/deploy` builds `<VERSION>-<commit>[-a<site addons hash>]` with the site's addons, pushes the images and (optionally) bumps the tag in your GitOps repo. No VERSION bump needed to ship (bump it to name a release). `bin/deploy --wait` waits for the pod to run it; verify after that.
- Config: env vars listed in `docs/configuration.md`; secrets never get printed or committed.
- Production debugging (logs, DB inspection without sqlite3, API endpoints): `docs/operations.md`.
- A deployment's own setup (where it runs, its secret store, devices, site addons) lives in its site, never in the repository: read `../site/AGENTS.md` when it exists.

## Docs index

| Doc | Read before touching |
|---|---|
| `docs/development.md` | How to work fast: the test loop, the local environment, splitting work across parallel agents and the brief they get, shipping small |
| `docs/notifications.md` | Notification ingest/triage/suggestions/summaries/escalation, web Inbox, phone push (`addons/phone/push.py`) |
| `docs/calls.md` | Call upload/transcription/analysis, suggestions, task links, context chats |
| `docs/tasks-and-triggers.md` | Task triggers (time / location / car / wake) |
| `docs/whatsapp.md` | WAHA webhook, voice-note transcripts |
| `docs/people.md` | People, incoming-call / meeting cards, wake briefing, weekly review |
| `docs/dashboard.md` | Bedroom tablet dashboard, TV-facing endpoints, camera/Frigate proxies |
| `docs/agent-actions.md` | Approval policy, undo events, the shared thread, screen context, learned phrases |
| `docs/now.md` | Now screen (`/api/now`, `/api/now/act`), notifications overview / rule suggestions, self-checks, search |
| `docs/about.md` | About you facts, prompt section, learner, `/api/about`, old `/api/memories` |
| `docs/usage.md` | Usage and cost tracking: what is captured, attribution, storage and roll-up, `/api/usage`, the Usage screen |
| `docs/configuration.md` | Environment variables: the minimal first run, security values (`API_TOKEN`, `CREDENTIALS_KEY` and its key file, `WAHA_WEBHOOK_SECRET`, `APP_PUBLISH_TOKEN`), models, services, tuning |
| `docs/operations.md` | Deploy (`bin/deploy`, the pre-push hook), k8s, logs, DB debugging, database schema and migrations, API endpoint list, backups |
| `docs/shopping.md` | The shopping list: lists, invitations, items and the add rules, aisles, history, undo, the live stream and push, the API, Dana's tools |
| `docs/knowledge.md` | Projects and entries: per user, sharing, FTS search, link media processing and the `/media/` files |
| `docs/local-testing.md` | The local test environment (`bin/local-env.sh`: the real image + stubs, upgrade from 8.1.0, end-to-end rehearsal `tests/local_env/e2e.py`) |
| `docs/contacts.md` | Google Contacts: the per-user copy and its sync, the tools, how people and caller names are filled from it |
| `docs/gmail.md` | Gmail: the tools, approval and undo, the untrusted-content rules, why it is not offered in voice |
| `docs/telegram-account.md` | The user's own Telegram account: the server's app, the QR sign-in, reading without marking read, the tools and the summary |
| `docs/money.md` | The money addon: sources, the bank-worker sidecar and its client, sync and SMS codes, investment accounts (Psagot Trade, the quick-view device, holdings), meal cards (Pluxee: reCAPTCHA sign-in, the 24-hour session, cycles, the monthly card line), schedule, ingest, categories, API, check, insights, dashboards and the balance forecast, Dana's money tools, the web Money tab |
| `docs/writing-an-addon.md` | Where an addon lives (official, site addons: `DANA_ADDON_DIRS`), how it is loaded, deployed and tested; starting from the `hello` example (the design: `../docs/addons.md`) |
| `docs/access.md` | Users, tokens, access levels, web/Telegram identity, the current user, what is per user, `notify`; addons (the framework, tools, connections, encrypted credentials, env import); enforcement (where the per-user tool check runs, refusals, admin-only endpoints); the multi-user design and its build phases |

Keep docs current in the same commit as the code; add new areas as a doc + one line here rather than growing this file. Docs say how the parts fit together and what is built, not its history; details that live next to the code (a migration's docstring, a test script's docstring, an endpoint's handler) are not copied into a doc.
