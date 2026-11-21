# Local test environment

A self-contained copy of the production setup on your machine: the real image
built from the `Dockerfile` (as `bin/deploy` builds it), started from a Dana 9.0
database (`tests/fixtures/db_9_0.sql`, its first admin named as the env file says)
with an env file shaped like production's, so the first start is the real
upgrade (snapshot, conversion to per-addon migrations, core's later
migrations, the first admin's default addons); `up` then connects the
admin's addons to the stubs with `bin/users.py addons connect`, as an
operator does (encrypted connections).
Every outside service is a stub. Nothing reaches production or the internet.

```bash
bin/local-env.sh up      # build the image, generate the env file, seed the data volume, start, connect the stubs
bin/local-env.sh test    # the end-to-end rehearsal (tests/local_env/e2e.py), PASS / FAIL per check
bin/local-env.sh logs    # follow the app's log (`logs --tail 100` etc. pass through)
bin/local-env.sh ps      # the containers
bin/local-env.sh down    # remove containers, networks, the data volume and the generated state
bin/local-env.sh reset   # down + up: a fresh 9.0 start
bin/local-env.sh restart # restart the app
```

**Iterating on code**: with `DANA_LOCAL_SOURCE=1` the app runs your working tree
(`core/`, `addons/`, `static/`, `main.py`, `bin/users.py` mounted read-only
over the image's copy, `tests/local_env/compose.source.yaml`). `up` then builds the
image only when it is missing, `restart` (about 8 s) picks up a Python change, and a
static file change is live on the next browser reload. Rebuild with
`bin/local-env.sh build` after changing `requirements.lock` or the `Dockerfile`.
Without it, the environment runs exactly what `bin/deploy` would build.

```bash
DANA_LOCAL_SOURCE=1 bin/local-env.sh reset   # fresh start on your code (~35 s with the image built)
DANA_LOCAL_SOURCE=1 bin/local-env.sh restart # after a change
bin/local-env.sh test                        # the rehearsal (~2 min)
```

Needs Docker with `docker compose`, and the repo's `.venv` for `test` (it uses
aiohttp from there). `test` expects a fresh environment: it creates the second
user, so run `reset` before running it again.

## What runs

| Container | What |
|---|---|
| `app` | the image `dana-local/app:latest` (the repo's `Dockerfile`, built from the files git tracks or would add, like `bin/deploy`'s clean checkout), `python main.py`, data volume at `/app/db`, env from `tests/local_env/.state/app.env` |
| `stubs` | `tests/local_env/stubs.py` (same image, the directory mounted read-only): every outside service, each on its own port, plus a control API |
| `gateway` | `tests/local_env/gateway.py`: forwards `127.0.0.1:18080` to the app and `127.0.0.1:18090` to the stubs' control API |
| `bank-worker` | only with `DANA_LOCAL_MONEY=1` (`compose.money.yaml`): the bank-worker sidecar built from `bank-worker/`, in the app's network namespace and on its data volume, as in the pod. Signing in to the real bank sites also needs `DANA_LOCAL_EGRESS=1`, and real bank logins (`docs/money.md`) |

The app and the stubs are only on an internal Docker network with no route out
of the machine (`compose.yaml`, `internal: true`); the gateway is the one
container that also has a normal network, for the published ports. So even a
misconfigured URL cannot reach a real service.

**Stubs** (`tests/local_env/stubs.py`):

| Port | Stands in for | Notes |
|---|---|---|
| 8123 | Home Assistant A (the first admin's) | `testfakes.FakeHA`: REST states / services / history / config, WebSocket auth + `subscribe_entities`; the entities the env import names |
| 8124 | Home Assistant B (the second test user's) | another token, other entities and zones |
| 3000 | WAHA | `testfakes.FakeWAHA`: sessions `default` (account `972500000001@c.us`) and `maya` (`972500000002@c.us`), contacts, chats, send, media; records what was sent |
| 5000 | Frigate | events, activity, previews, clips, WebRTC, version |
| 9100 | the model provider | OpenAI-compatible: streamed and plain chat, structured output (the minimal instance of the schema; the shopping aisle lookup gets an aisle per item from common English and Hebrew grocery words, else `pantry`), audio transcription. LiteLLM reaches it as `openai/…` models through `OPENAI_API_BASE`. Agent turns (requests with tools) get scripted replies, else "Done." |
| 8081 | Telegram Bot API | getUpdates long polling fed by the control API, sendMessage etc. recorded. The app points aiogram at it with `TELEGRAM_API_BASE` (`docs/configuration.md`) |
| 9000 | control API | `POST /ha/{a,b}/state {entity_id, state, attributes?}` (pushes the change to subscribers), `GET /ha/{a,b}/log`, `GET /waha/log`, `GET /frigate/log`, `POST /llm/script [{match, reply: {text} \| {tool: {name, args}} \| {tools: [...]}, reasoning?}]`, `GET /llm/requests?match=` (system message, tool names, conversation of every model call), `POST /llm/transcript {text}`, `POST /telegram/message {from_id, chat_id?, text}`, `POST /telegram/callback`, `GET /telegram/log`, `POST /reset` (forget the records) |

Not stubbed: **Google Calendar** (unreachable: calendar reads come back empty,
`POST /api/addons/calendar/test` answers `ok: false`), **FCM** (not
configured: push events queue and the phone fetches them with `GET
/api/phone/events`), **TypeSafe** (no key: notifications stay untriaged),
web search, kubectl.

## The env file

`bin/local-env.sh up` writes `tests/local_env/.state/app.env` (gitignored, mode
600) once: a generated `CREDENTIALS_KEY`, `API_TOKEN`, bot token,
`APP_PUBLISH_TOKEN` and `WAHA_WEBHOOK_SECRET` (the rehearsal signs its
webhook posts with it), the first user's profile (`OWNER_NAME=Robin`,
`OWNER_CONTEXT`, `ALLOWED_TELEGRAM_USER_IDS=111111111`, `TELEGRAM_CHAT_ID`),
the Google OAuth client, `MEMBER_ALLOWED_ORIGINS=homeassistant=http://stubs:8124`
(the second user's Home Assistant stub, which no admin uses, so a member may
connect it) and `TIMEZONE`. The models are `openai/dana-local-*` on the stub.
`down` deletes it. The admin's Home Assistant, WhatsApp, calendar, cameras and
`ops` are not in it: `up` connects them to the stubs after the start
(`connect_addons` in `bin/local-env.sh`; skipped for one already connected).

## Opening it

- Web UI: <http://127.0.0.1:18080>. Log in with a token:
  - the first admin (Robin): `API_TOKEN` from `tests/local_env/.state/app.env`
    (also printed by `up`);
  - the second test user (Maya Levi, a member): after `test` has run,
    `tests/local_env/.state/users.json` holds her token. Without the test, make
    one yourself: `docker exec -i dana-local-app-1 python bin/users.py add "Maya
    Levi"` and `… bin/users.py tokens issue maya`.
  Use a private window for the second user (the session cookie is per browser).
- The Android app (emulator): server `http://10.0.2.2:18080` with one of the tokens.
- Administration: `docker exec -i dana-local-app-1 python bin/users.py list`
  (every command of `docs/access.md` → "bin/users.py").
- Make something happen: `curl -X POST localhost:18090/ha/a/state -H
  'Content-Type: application/json' -d '{"entity_id": "person.robin", "state":
  "Work"}'` (Robin arrives at Work), or script the model: `curl -X POST
  localhost:18090/llm/script -d '[{"match": "lights", "reply": {"tool":
  {"name": "call_ha_service", "args": {"domain": "light", "service": "turn_on",
  "entity_id": "light.living_room"}}}}, {"match": "lights", "reply": {"text":
  "On."}}]'`, then say "lights" in the chat.
- Ports: `DANA_LOCAL_PORT` / `DANA_LOCAL_CONTROL_PORT` change 18080 / 18090.

## The end-to-end rehearsal

`tests/local_env/e2e.py` drives the running container over HTTP only, plus
`docker exec … bin/users.py` and `docker` (restart, start-up refusals). It
covers: the upgrade (log lines, snapshot, every fixture row with its id through
the API, `/api/me`, the default addons and the connections `up` made, their
tests against the stubs, the CLI, a restart that gives nothing twice, missing /
malformed / wrong `CREDENTIALS_KEY` on the migrated database, the key file
generated for a fresh 9.0 one); the
endpoints `static/*.js` load and the fields they read, the pages, the login
cookie, a streamed chat turn compared with `tests/fixtures/golden.sse`'s event
types, reattach, approval, undo; a second user made with the CLI (403
`not_connected`, admin-only endpoints, `ops` refused, her tool list and system
message as the model received them, hidden tools refused and not executed,
her own Home Assistant and WhatsApp connected through the API, empty data, the
admin's ids 404 on every resource); sharing (task list read / edit / none,
a project through `share_item` with approval, the shopping list: an `edit`
share is an invitation on her screen and phone and leaves her list alone,
`use` with moving her items and its single undo, the admin's event stream
seeing her change, typed text moved to its aisles by the model stub's answer
— the run writes real payloads to `.state/shopping-examples.json` — and
unsharing); the Home
Assistant watcher (zones and car per user, a flap, a settings change, a
disconnect); WAHA inbound routing by account and the internal-host rule; phone
events and notifications per user; Telegram (admin, member, unknown id,
proactive reminders to the right chat); usage per user; the security rules
(docs/access.md: a member's debug export has no environment and the admin's
masks credentials, a member's cameras PUT / DELETE 403, refused addresses,
a malformed entity id, Telegram chats and the admin's WAHA session refused,
the webhook 403 with a member token, media named on another host fetched from
the connection's own WAHA, registering the admin's device id leaves the
admin's row); the second review's findings (a task the member moved out of
the admin's shared list and undid is the admin's again, and the undo is
refused with 409 once the list is unshared; her WAHA session reporting the
admin's account gets no account — conflict in the API, the CLI and the log,
voice notes still the admin's only, her WhatsApp tools refuse — and recovers
on her own session; the stub WAHA's `maya-lies` session stands in for a
member-run WAHA); the web Addons page (its assets, every request it
makes answering with the fields it reads for the admin and the member, the
admin's Home Assistant moved to the second stub and back with connection
tests in between, a member's refused change with the reason the page
shows); the web Shopping panel (`/shopping` serves the app with
the panel and its assets, signed out it goes to the login page and back,
the service worker and manifest entries, the member's change made with
`?list=` reaching the admin's live stream with a newer version). It exits non-zero on
any failure and writes `tests/local_env/.state/e2e-results.json`.

## Against a copy of a real database and a real model key

Possible, and not done by the script for you. Read all of this first.

1. Copy the production database out (`docs/operations.md` → DB debugging),
   to a path outside the repo. It holds personal data: keep it local, delete
   it after.
2. `DANA_LOCAL_DB=/path/to/copy.sqlite3 bin/local-env.sh reset` seeds the
   volume from the copy instead of the fixture.
3. A database that already went through the upgrade has encrypted
   credentials: the start refuses without the production `CREDENTIALS_KEY`.
   Put it, and anything else you want to override, in a file of `KEY=value`
   lines and pass `DANA_LOCAL_ENV_EXTRA=/path/to/extra.env` (appended to the
   generated env, so it wins). A pre-upgrade copy needs no key of yours: `up`
   connects the stubs.
4. A real model: in the same extra file set `MODEL`, `BACKGROUND_MODEL`,
   `TRANSCRIBE_MODEL`, `ASSISTANT_MODELS` (or empty it for the built-in
   catalog), the provider key, and `OPENAI_API_BASE=` (empty) when the
   provider is OpenAI. It needs a route out: `DANA_LOCAL_EGRESS=1`
   (`compose.egress.yaml`). **With egress the app can reach everything its
   addons point at**: a post-upgrade copy with the real key talks to
   the real Home Assistant, WAHA, Google and Frigate (and runs the watchers,
   briefings and reminders against them). Before turning egress on, repoint
   or disconnect those connections with `docker exec -i dana-local-app-1
   python bin/users.py addons update|disconnect …` while egress is
   still off. Telegram stays on the stub regardless (`TELEGRAM_API_BASE`).
5. The end-to-end script assumes the fixture's data; on a real copy use the
   environment by hand.
