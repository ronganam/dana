# Operations: deploy, production, debugging

## Running it

- **Locally**: `python main.py` (README). The web UI and API on `:8080`, Telegram if `TELEGRAM_BOT_TOKEN` is set.
- **Docker Compose**: `docker compose up -d` in `server/` builds the image and keeps the data in the `data` volume (`compose.yaml`; the bank-worker sidecar of the `money` kind: `docker compose --profile money up -d`).
- **Kubernetes** (one replica: SQLite): the image (`Dockerfile`, `app` stage) runs as UID 1001 and keeps everything it writes in the directory holding the database (`DATABASE_PATH`, default `/app/db/db.sqlite3`; `core/paths.py`), so mount one volume there. Secrets come in as env vars (`docs/configuration.md`). Probes: TCP on 8080. The bank worker (`bank-worker/README.md`) runs as a sidecar on `127.0.0.1:8090` mounting only `<data dir>/money`. Backups: copy `<data dir>/snapshots/db.sqlite3` after `python3 -m core.db_snapshot` (a consistent copy; a raw copy of a live SQLite file can catch it mid-write), e.g. from a Velero pre-backup hook.

## Deploy

`git push origin master` tests and, when configured, deploys, all from your machine. The repository's `.githooks/pre-push` hook (once per clone: `git config core.hooksPath .githooks`), on a clean checkout of the pushed commit:

1. runs `bin/test` when the push changes more than docs under `server/`, plus the Dockerfile's `test` stage (the suite on the exact image, Python 3.14) when it changes `server/Dockerfile` or `server/requirements.txt`;
2. when it changes what the images contain (`core/`, `integrations/`, `static/`, `bank-worker/`, `main.py`, `requirements.txt`, `Dockerfile`, `VERSION`, `bin/users.py`) and `server/deploy.env` exists, runs `bin/deploy`.

A failure stops the push. `DANA_NO_DEPLOY=1 git push …` pushes without deploying; `git push --no-verify` skips tests and deploy. Android changes are not tested by the hook.

- **`bin/deploy [<commit>] [--wait]`** reads `deploy.env` (gitignored; from `deploy.env.example`): builds the `app` stage of that commit (default HEAD) from a clean checkout as `$DANA_IMAGE:<VERSION>-<commit>` and `latest`, plus the bank-worker sidecar `$DANA_WORKER_IMAGE:<VERSION>-<commit>` when set, with the plugin kinds of `DANA_IMAGE_PLUGINS` copied in (`docs/plugins.md`), and pushes them. With `DANA_GITOPS_REPO` it writes both tags into `DANA_GITOPS_VALUES` in that repository (from its own clone in `~/.cache/dana-deploy/gitops`) and pushes, for ArgoCD / Flux to roll the pod. It runs no tests. Use it to redeploy or roll back (`bin/deploy <older commit>`).
- The release name `<VERSION>-<commit>` is written into the image's `VERSION` file: `/api/health` reports it and the dashboard's `?v=` cache-buster uses it, so every deploy reaches cached clients. **No VERSION bump is needed to ship**; bump it to name a release.
- `bin/deploy --wait` waits until a pod in `DANA_K8S_NAMESPACE` matching `DANA_K8S_SELECTOR` runs the release and is ready.

## Database schema

- The schema is owned by ordered migrations in `core/db/migrations/`: one module per change, `mNNNN_<name>.py`, found by its file name (numbers run 1..N; a gap or a repeat fails at import). **Each module's docstring says what it changes and where existing rows go** (`m0001_baseline` = the complete 8.1.0 schema); that is the reference, not this page. Applied ones are recorded in `schema_migrations (version, name, applied_at)`. `main.py` calls `core.db.migrate()` once at startup, before anything reads the database; tests call it in their setup. Importing a module never creates tables.
- **Before** applying pending migrations to an existing database, a consistent copy is written with `core/db_snapshot.py` to `<data dir>/snapshots/db-before-<first pending version>.sqlite3` (e.g. `db-before-0002.sqlite3`). Restore from it if a migration goes wrong.
- A database made before migrations existed is adopted: the baseline checks it has the complete 8.1.0 schema (tables 8.1.0 created lazily on first use may be missing and are then created; `sessions.created_at`, which 8.1.0 never managed to add to old tables, may be missing) and changes nothing else. An **older or incomplete database refuses to start** with a message naming what is missing ("Start Dana 8.1.0 on it once…"). A database with migrations this code does not know (made by a newer version) refuses too.
- Adding a change: the next `mNNNN_<name>.py` with `VERSION` (= NNNN), `NAME`, `up(conn)` (runs inside the runner's transaction; do not commit) and a docstring saying what it changes and where existing rows go; nothing to register. Parallel branches: agree on the numbers first (docs/development.md). A migration that rebuilds a table other tables reference sets `FOREIGN_KEYS_OFF = True` (the runner turns foreign keys off around its transaction, as SQLite requires). Never edit a shipped migration. `python test_db.py` checks fresh vs migrated 8.1.0 (`test_fixtures/db_8_1_0.sql`) equivalence.
- Startup data steps (not schema): `users.bootstrap()` (first admin user, `docs/access.md`), `integrations.tasks.groups.ensure_default_groups_for_everyone()` (each user with `tasks` gets any default list they lack; a deleted default list comes back on restart, as before), `integrations.about.legacy.migrate_legacy()` (the old memories → their owner's facts) and `integrations.about.facts.load_prompts()` (the About prompt block of every user with `about`).

**Secrets**: `docs/configuration.md`.

## Debugging production

```bash
NS=dana    # your namespace
POD=$(kubectl -n "$NS" get pod -l app=dana -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" logs "$POD" --tail 200 | grep -v aiohttp.access   # the access log is noisy (dashboard and app polling)
kubectl -n "$NS" exec "$POD" -- python3 -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8080/api/health').read())"
```

### Debug session data in DB

`sqlite3` is **not** available in the container. Use Python instead:

```bash
# List recent sessions with debug info size
kubectl exec -n "$NS" "$POD" -- python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/db/db.sqlite3')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT user_id, session_id, status, title, length(debug_info), updated_at FROM sessions ORDER BY updated_at DESC LIMIT 20').fetchall()
for r in rows: d = dict(r); print(f\"{d['updated_at']} | {d['user_id']} | {d['session_id'][:36]:36s} | {d['status']:20s} | debug={d['length(debug_info)']:>6}B | {d['title']}\")
"

# Fetch full debug payload for a specific session
SESSION_ID="<paste-session-id-here>"
kubectl exec -n "$NS" "$POD" -- python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/db/db.sqlite3')
conn.row_factory = sqlite3.Row
for row in conn.execute('SELECT user_id, debug_info FROM sessions WHERE session_id = ?', ('$SESSION_ID',)): print(row['user_id'], row['debug_info'])
"
```

The `debug_info` column holds only run metadata (`timestamp`, `agent_model`, `error_context`). `GET /api/debug/sessions/{id}` (`core.session.build_session_debug_payload`) joins it with the `history` and `pending_approval` columns and the redacted environment.

### Tasks, memories, and calls

```bash
# Recent tasks
kubectl exec -n "$NS" "$POD" -- python3 -c "
import sqlite3, json
conn = sqlite3.connect('/app/db/db.sqlite3')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT id, title, status, priority, triggers FROM tasks ORDER BY updated_at DESC LIMIT 10').fetchall():
    d = dict(r); print(f\"{d['status']:15s} | {d['title'][:50]:50s} | trig={d['triggers'][:60]}\")
"

# Recent call recordings
kubectl exec -n "$NS" "$POD" -- ls -la /app/db/recordings/
kubectl exec -n "$NS" "$POD" -- python3 -c "
import sqlite3
conn = sqlite3.connect('/app/db/db.sqlite3')
conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT id, caller, title, duration_seconds FROM phone_calls ORDER BY created_at DESC LIMIT 5').fetchall():
    d = dict(r); print(f\"{d['id'][:12]} | {d['caller'][:20]:20s} | {d['title'][:40]:40s} | {d['duration_seconds']}s\")
"

# Copy a recording file locally
kubectl cp -n "$NS" "$POD":/app/db/recordings/<filename> ./recording.m4a
```

## API endpoints

The web server listens on port 8080. Every path but the public ones (`docs/access.md`) needs `Authorization: Bearer <token>` (`API_TOKEN` or a user's token), from inside a cluster too: an `INTERNAL_HOSTS` Host only opens the inbound webhook `/webhook/waha` without a token.

| Endpoint | What it returns |
|---|---|
| `POST /api/assistant/message` | Start a run (`{session_id, message, model?, images?}`) → SSE stream of its events, closed by a `state` frame |
| `GET /api/sessions/{id}/stream?run_id=&after=` | Reattach to the session's current/just-finished run (SSE); only `state` when nothing is running |
| `POST /api/sessions/main/clear` | New topic in the shared thread: Dana forgets it so far, messages stay (docs/agent-actions.md) |
| `GET /api/assistant/models` | Chat model catalog + default id (the profiles the caller's access level may use) |
| `GET /api/assistant/voice_stream` | WebSocket: PCM in, then the same run events as JSON frames, closed by `state` (or `no_speech`) |
| `GET /api/me` | The caller: `{id, name, access, capabilities, kinds}` |
| `GET /api/users` | `manage_users` only: `{users: [{id, name, access}]}` — the Integrations page's user picker |
| `GET /api/integrations` · `GET /api/integrations/{kind}` | The caller's integrations (`?user=<id>` with `manage_users`): fields with their type, whether each credential is set (never a secret's value; an address's value is shown), settings, account, and whether the caller may change each kind / field (`can_change`, `why_not`, `rule`) (`docs/access.md` → Managing users) |
| `PUT /api/integrations/{kind}` | Connect (201) or update (200): `{credentials?, settings?}`, merged; 400 `invalid` with the field; 403 `forbidden` for a kind the level may not have |
| `DELETE /api/integrations/{kind}` | Disconnect |
| `POST /api/integrations/{kind}/test` | Does the service answer with these credentials: `{kind, checked, ok, reason}` (Home Assistant, WhatsApp, Calendar, cameras) |
| `GET /api/integrations/{kind}/options` | Candidates for the settings, read from the service (Home Assistant: people, car sensors and devices, phone sensors, weather) |
| `GET` / `PUT /api/sharing/{item_type}/{id}` | Who an item the caller owns is shared with / share it (`{user_id, level: read\|edit\|none}`); owner only (`docs/access.md` → Sharing) |
| `GET /api/usage` · `GET /api/usage/events` | Usage and cost: totals, per day, breakdowns, individual calls (`docs/usage.md`) |
| `GET /api/debug/sessions` | The caller's sessions with history length & pending status |
| `GET /api/debug/sessions/{session_id}` | Full debug payload of one of the caller's sessions (history, env, tool calls); another user's id → 404 |
| `GET /api/debug/logs` | In-memory log buffer (last 2000 entries, rolling); admin only (403 `{"error": "forbidden", "needs": "manage_server"}`) |
| `DELETE /api/debug/logs` | Clear the in-memory log buffer; admin only |
| `GET /api/tasks` | The caller's tasks (own and shared lists) |
| `GET /api/shopping` | The caller's Shopping screen: active list (`?list=<id>`: another list they can see), aisles, items, counts, usuals, invitations, lists (`docs/shopping.md` → API) |
| `GET /api/shopping/items` | The list's items only (a plain array) |
| `POST /api/shopping/items` | Add `{text}` or `{items: [...]}` → screen + `results` + `undo` |
| `PATCH` / `DELETE /api/shopping/items/{id}` | Change (`checked`, `name`, `quantity`, `note`, `category`) / remove an item → screen + `undo` |
| `POST /api/shopping/clear-bought` | Remove every bought item → screen + `undo` |
| `GET` / `DELETE /api/shopping/suggestions` | Names bought before (`?q=`; none: the usuals) / forget one (`{name}`) |
| `GET /api/shopping/events` | Server-sent events: `changed {list_id, version}`, `lists {}`, `end {}` |
| `GET /api/shopping/lists` · `PUT /api/shopping/active` | The caller's own and shared lists and which is active / choose it (`{list_id}`) |
| `POST /api/shopping/invites/{list_id}` | Answer an invitation: `{action: "use" \| "dismiss", move_items}` |
| `GET` / `POST /api/money/sources` · `PATCH` / `DELETE /api/money/sources/{id}` | The caller's bank / card logins (never their credentials) with accounts, state and any waiting code request / add or change one (`docs/money.md` → API) |
| `POST /api/money/sources/{id}/sync` · `GET /api/money/syncs?source=` | Sync now (202 · 429 `too_soon` · 409 `needs_attention`) / recent syncs with their outcome and counts |
| `GET /api/money/code-requests[/{id}]` · `POST /api/money/code-requests/{id}` | Sign-in codes a sync waits for (the phone reads the sender and pattern) / hand one over (`{code}`) |
| `GET /api/money/overview\|pace?month=` · `/categories` · `/months` · `/recurring` | The Money page's numbers (`docs/money.md` → Page API and insights) |
| `GET /api/money/meals?months=` | The meal card (Pluxee): the budget now, its cycles with their card line, meals and places (`docs/money.md` → Meal cards) |
| `GET /api/money/savings[/{key}]` · `PATCH /api/money/savings/{key}` · `POST /api/money/savings/{key}/check` | Ways to save and spending to watch with the caller's choices (status, answers, targets, subscription lines → `undo_id`) / a price check on request (202 · 400 `needs_answer` · 409 · 429 · 503) (`docs/money.md` → Savings) |
| `GET /api/money/portfolio?days=&trades=` | The caller's investment accounts: value, holdings, value per day, activity, allocation (`docs/money.md` → Investment accounts) |
| `GET /api/money/transactions` · `GET` / `PATCH /api/money/transactions/{id}` | Transactions (search, filters, cursor) / one in full / change its category (`for_merchant`) or note → `undo_id` |
| `GET /api/money/changes?since=` | `{changed, cursor}` for the page's 30 s poll |
| `POST /api/money/signals` | The phone's bank / card app payment pushes (`{notifications: [...]}` → `{accepted, duplicates}`); stored in `money_signals` for 60 days, a parsed payment becomes a provisional transaction |
| `GET /api/memories` | The caller's About facts in the old memories shape |
| `GET /api/calls` | The caller's call recordings |
| `GET /api/calls/{id}/audio` | Download recording file |
| `GET /api/calendar/events?start_date=&end_date=` | Calendar events |
| `GET /telegram/connect` · `GET\|POST\|DELETE /api/telegram/login` · `POST /api/telegram/login/password` | The caller's Telegram QR sign-in (`docs/telegram-account.md`) |
| `GET /api/google/connect` · `/api/google/callback` · `/api/google/status` | The Google sign-in that connects the caller's Google kinds (`docs/access.md` → Google account) |
| `GET /api/ha/states?ids=a,b` / `?domains=light,climate` | Compact states of the caller's Home Assistant for control tiles (ids, in order) or pickers (domains); 1.5 s cache per connection, cleared by `POST /api/ha/service`; 403 `not_connected` without the kind |
| `GET /api/frigate/events/{camera}?limit=` | Latest Frigate detection events (audio dropped) + `server_time` |
| `GET /api/frigate/event/{id}/preview.gif` | Relay of Frigate's event preview GIF |
| `GET /api/frigate/event/{id}/clip.mp4` | Event clip, buffered (4-clip LRU) and served with Range support |
| `GET /api/people?sort=&q=` / `GET /api/people/{id}` | People with stats / one person: brief, timeline, tasks (`docs/people.md`) |
| `GET /api/people/lookup?phone=` | The incoming-call card for a number |
| `POST /api/phone/unlock` | Phone unlocked (`{at}` epoch ms) — the wake signal; returns `confirming` / `done` / `outside_window` (`unavailable` for a user without Home Assistant) |
| `GET /api/briefing/today` | Today's wake briefing (`?generate=1` makes it) |
| `GET /api/review/latest` | The latest weekly review with its proposals |
| `GET /api/app/version` | Latest published Android app versions (manifest in `<data dir>/app-updates/`) |
| `GET /api/app/download/{filename}` | Download a published Android APK |
| `POST /api/app/upload` | Publish an Android APK (Bearer `APP_PUBLISH_TOKEN`) |
