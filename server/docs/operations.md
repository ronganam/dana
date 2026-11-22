# Operations: deploy, production, debugging

## Running it

- **Quick start** (the published image, no checkout): `compose.yaml` at the repository root runs `ghcr.io/ronganam/dana:latest`, the image each release publishes (`.github/workflows/release.yml`; amd64 and arm64).

  ```bash
  curl -fsSLO https://raw.githubusercontent.com/ronganam/dana/master/compose.yaml
  docker compose up -d
  docker compose logs dana    # open the setup link it prints
  ```

  The setup link creates the first admin in the browser and opens the first-run wizard (you, Home, the model and its key, your phone, add-ons, sample data); settings go in an optional `.env` next to `compose.yaml` (`docs/configuration.md`). Headless (no browser at hand): put `API_TOKEN` in that `.env` before the first start. Upgrade: `docker compose pull && docker compose up -d`. The administration CLI is `dana` on the image's PATH: `docker compose exec dana dana users list`, `docker compose exec dana dana addons install <name>` (into `<data dir>/addons`, in the volume, kept across upgrades; then `docker compose restart dana`). The image ships no add-on catalog: it reads the `dana-catalog` repository's `catalog.json` (`DANA_CATALOG_URL`; offline, set `DANA_CATALOG_FILE`). Phones, tablets and TVs find the server on the home network when something advertises `_dana._tcp` (`docs/devices.md`, "Finding the server on the home network": an Avahi example). The data lives in the `data` volume; the port is `127.0.0.1:8080` (reverse proxy or VPN to reach it from elsewhere, below).
- **Locally**: `python main.py` (README). The web UI and API on `:8080`, Telegram if `TELEGRAM_BOT_TOKEN` is set. It needs only an LLM key: a fresh database starts in setup mode and logs the setup link (`docs/devices.md`); `API_TOKEN` instead creates the first admin from env (headless), and `DANA_INSECURE_DEV=1` turns authentication off (`127.0.0.1` only).
- **Docker Compose from source** (contributors): `docker compose up -d` in `server/` builds the image and keeps the data in the `data` volume (`compose.yaml`; an addon's worker runs under its profile, e.g. `docker compose --profile money_il up -d`: "Workers" below). The port is published on `127.0.0.1:8080` only: to reach Dana from other machines, put a reverse proxy with HTTPS in front of it (Caddy, Traefik, nginx) or change the mapping knowingly. The healthcheck polls `/api/health`.
- **Data to back up**: the database directory (`DATABASE_PATH`'s folder: the database, `recordings/`, `media/`, `uploads/`…) including `credentials.key` when `CREDENTIALS_KEY` is not set (the generated key: without it the stored addon credentials are lost).
- **Kubernetes** (one replica: SQLite): the image (`Dockerfile`, `app` stage) runs as UID 1001 and keeps everything it writes in the directory holding the database (`DATABASE_PATH`, default `/app/db/db.sqlite3`; `core/paths.py`), so mount one volume there. Secrets come in as env vars (`docs/configuration.md`). Probes: TCP on 8080. An addon's worker runs as a sidecar in the same pod ("Workers" below; e.g. `money_il`'s on `127.0.0.1:8090`, mounting only `<data dir>/money`). Backups: copy `<data dir>/snapshots/db.sqlite3` after `python3 -m core.db_snapshot` (a consistent copy; a raw copy of a live SQLite file can catch it mid-write), e.g. from a Velero pre-backup hook.

## Dependencies

- `requirements.txt`: the direct dependencies, each with a lower bound (what the code imports, plus the `mcp` workaround noted there). `requirements.lock`: every package pinned with its hashes, for Python 3.12 and newer on every platform; the image installs it (`pip install --require-hashes -r requirements.lock`) and so does the suggested local setup (README).
- After changing `requirements.txt` (or to take upgrades), regenerate the lock and run the suite:

  ```bash
  uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes --no-header --no-annotate -o requirements.lock
  .venv/bin/pip install -r requirements.lock && bin/test
  ```

  (`uv pip compile --upgrade` takes the newest versions; without it, versions already pinned stay.) Commit both files together; the pre-push hook then also runs the image's `test` stage.
- No new runtime dependency without a reason in the commit; check its license against `../THIRD_PARTY.md` (e.g. `mutagen` is GPL-2.0-or-later).

## Deploy

`git push origin master` tests and, when configured, deploys, all from your machine. The repository's `.githooks/pre-push` hook (once per clone: `git config core.hooksPath .githooks`), on a clean checkout of the pushed commit:

1. runs `bin/test` when the push changes more than docs under `server/`, plus the Dockerfile's `test` stage (the suite on the exact image, Python 3.14) when it changes `server/Dockerfile` or `server/requirements.txt` / `requirements.lock`;
2. when it changes what the images contain (`core/`, `addons/` with the addon workers, `static/`, `cli/`, `main.py`, `requirements.txt` / `requirements.lock`, `Dockerfile`, `VERSION`, `bin/dana`) and your site has a `deploy.env` (`site/deploy.env` at the repository root, or `$DANA_SITE/deploy.env`; `../docs/site.md`), runs `bin/deploy`.

A failure stops the push. `DANA_NO_DEPLOY=1 git push …` pushes without deploying; `git push --no-verify` skips tests and deploy. Android changes are not tested by the hook.

- **`bin/deploy [<commit>] [--wait]`** reads the site's `deploy.env` (`DANA_DEPLOY_CONFIG` names another file; the template is `site.example/deploy.env.example`): builds the `app` stage of that commit (default HEAD) from a clean checkout, with the site's addons as the `site_addons` build context (`site/addons/`, or the folders in `DANA_IMAGE_ADDONS`; the image's `/app/addons-site`; `docs/writing-an-addon.md`), as `$DANA_IMAGE:<release>` and `latest`, plus one image per addon worker (below), and pushes them. The release is `<VERSION>-<commit>`, plus `-a<hash>` of the site addons' files when there are any, so changing only a site addon and running `bin/deploy` again is a new release that rolls out. With `DANA_GITOPS_REPO` it writes the release into `DANA_GITOPS_VALUES` (every `tag:` line and every worker's `image:`) in that repository (from its own clone in `~/.cache/dana-deploy/gitops`) and pushes, for ArgoCD / Flux to roll the pod. It runs no tests. Use it to redeploy or roll back (`bin/deploy <older commit>`).
- The release name is written into the image's `VERSION` file: `/api/health` reports it and the dashboard's `?v=` cache-buster uses it, so every deploy reaches cached clients. **No VERSION bump is needed to ship**; bump it to name a release.
- `bin/deploy --wait` waits until a pod in `DANA_K8S_NAMESPACE` matching `DANA_K8S_SELECTOR` runs the release and is ready.

### Workers

An addon that declares `worker=Worker(port, url_env, image)` (`core/addons/hooks.py`) has a sidecar. Its image is either **published** by the addon's own repository (`image="ghcr.io/…"`: a catalog addon, e.g. `money_il`'s bank worker, `ghcr.io/ronganam/dana-addon-money-il-worker`, Node + Chromium, built and pushed by that repository's workflow on every push to `main`, tagged with the commit and `latest`) or **built** here from the addon's `worker/` folder (`worker/Dockerfile`, self-contained: the app image leaves `addons/*/worker` out; a site addon's).

- **`bin/deploy`**, for the commit's official addons and the site's addons (DANA_WORKERS in `deploy.env`: `all` (default), `none`, or addon names, space or comma separated):
  - a published image is **pinned**, never built: to the tag or digest the manifest names when it names one, else to the commit the addon was installed at (`site/addons/<name>/.installed.json`, written by `bin/dana addons install`; `core/addon_dirs.py` `worker_image`). An addon whose image has no tag and that records no commit stops the deploy.
  - a `worker/Dockerfile` is built as `$DANA_IMAGE-<addon>:<release>` (the same release as the app) and pushed.
  - An addon installed at runtime into the data directory (the Addons page, `/api/addons/install`) is not in the image, so `bin/deploy` does not see it: set its worker image in the GitOps values by hand (its repository's commit tag), and again when you update the addon.
- **GitOps values**: besides every `tag:` line, `bin/deploy` rewrites any `image: "<DANA_IMAGE>-<addon>:<old tag>"` to the new release and any `image: "<published image>:<old tag>"` (or `@<digest>`) to the pinned one, so name each worker's image in full under one key per addon; the convention is `workers.<addon>.image`:

  ```yaml
  image:
    repository: registry.example/dana
    tag: "9.0.0-abc1234"
  workers:
    money_il:
      image: "ghcr.io/ronganam/dana-addon-money-il-worker:<commit>"   # published: pinned, not built
      port: 8090
    my_addon:
      image: "registry.example/dana-my_addon:9.0.0-abc1234"   # built by bin/deploy from worker/
      port: 8091
  ```

  The chart (yours) runs it in the pod next to the app, sharing its network namespace and data volume, and the app reaches it at the addon's `url_env` (`MONEY_WORKER_URL`, default `http://127.0.0.1:8090`).
- **Compose** (`compose.yaml`): each worker is a service under a profile named after its addon: `docker compose --profile money_il up -d` adds `money-il-worker`, run from its published image (`MONEY_IL_WORKER_TAG`, default `latest`; set it to the commit you installed). A built worker's service has a `build:` of its `worker/` folder.
- Tests: a worker's own (`addons/<name>/worker/test/test_*.mjs`) run in `bin/test` with the rest; a catalog addon's run in its own repository.

### Restarting

Installing, updating or removing an addon (the web Addons page, `/api/addons/install`, `/api/addons/<name>/remove`) changes files on disk; the server loads addons only at start. `POST /api/server/restart` (admins: `manage_server`; the page's Restart button) makes `main.py` stop the bot and the web server and exit with status 0 (`core/lifecycle.py`); the **supervisor starts it again**: Docker's `restart: unless-stopped` (`compose.yaml`), Kubernetes' default `restartPolicy: Always`. Run by hand (`python main.py`) it just exits. Disabling or enabling an addon on the server needs no restart (`../docs/addons.md`).

## App updates

The server offers the Android phone and watch apps' updates (`core/app_updates.py`): the manifest `GET /api/app/version` (`{"phone": {...}, "wear": {...}}`) and the files `GET /api/app/download/{file}`, kept in `<data dir>/app-updates/`. The phone app checks it (and offers the watch app's update); the watch face, the tablet and the TV are installed by hand from a release. Two sources fill it; for each artifact the higher `versionCode` wins, whichever source it came from, and only that APK is kept:

- **GitHub Releases**: at start and every 6 hours, the latest release of `APP_RELEASES_REPO` (default `ronganam/dana`, empty: off) through the public API (no token). Its `versions.json` asset says each artifact's `versionCode`; only a newer phone / wear APK is downloaded, checked against `versions.json`'s and `SHA256SUMS`' checksum (a mismatch installs nothing and logs a warning), and offered. A failed check is retried at the next one.
- **Uploads** (your own builds): `android/bin/deploy-app.sh` → `POST /api/app/upload` with `APP_PUBLISH_TOKEN`. An upload with a lower `versionCode` than the one offered is refused (409).

## Database schema

- **Who owns what.** Core's tables are created by `core/db/migrations/`, each addon's by its own `addons/<name>/migrations/` (`mNNNN_<name>.py`, numbered 1, 2, 3… per owner; a gap or a repeat fails at import). Each module's docstring says what it changes and where existing rows go: that is the reference, not this page. Applied ones are recorded in `schema_migrations (addon, version, name, applied_at)` (core's are `addon = 'core'`).
- **When they run.** `main.py` loads the addons, then calls `core.db.migrate()` once, before anything reads the database: core's migrations first, then each loaded addon's in `needs` order (`docs/addons.md`, "Data"). Tests call it in their setup. Importing a module never creates tables.
- **Snapshot first.** Before the first pending migration touches an existing database, a consistent copy is written to `<data dir>/snapshots/db-before-<addon>-<version>.sqlite3` (`core/db_snapshot.py`; e.g. `db-before-core-0002.sqlite3`). Restore from it if a migration goes wrong. The volume needs room for one more copy of the database per upgrade.
- **Upgrading from Dana 9.0.** A 9.0 database (one migration sequence, 1..33 recorded, optionally 34) is converted in place on its first start (`core/db/convert.py`), after a snapshot named `db-before-addons-from-<0033|0034>.sqlite3`: it applies old migration 34 if missing, then replaces `schema_migrations` with the per-addon table, marking core's baseline and each official addon's baseline applied. Rows are not touched (connections keep their user; credentials stay encrypted with the same `CREDENTIALS_KEY`). An older database, or one missing any of 1..33, refuses to start ("start Dana 9.0.0 on it once first").
- **Rolling back.** An older image does not start on a newer database (its own migrations fail on the changed schema, inside a transaction, so nothing is damaged). Rolling back is: stop the pod, restore the snapshot taken before the upgrade (for 9.0 → this version: `snapshots/db-before-addons-from-0033.sqlite3`) over `db.sqlite3`, deploy the old image. Anything written after the upgrade is lost, so roll back early or not at all.
- **Upgrade with one copy running.** A conversion must not race the old version: deploy it with the old pod stopped (Kubernetes: scale to 0 first, or `strategy: Recreate`), never as a rolling update.
- **Adding a change.** The next `mNNNN_<name>.py` in the owner's `migrations/` with `VERSION` (= NNNN), `NAME`, `up(conn)` (runs inside the runner's transaction; do not commit) and the docstring; nothing to register. A migration that rebuilds a table other tables reference sets `FOREIGN_KEYS_OFF = True`. Never edit a shipped migration. An addon never references another addon's table with a foreign key unless its `needs` names it. `bin/test test_db` checks that a fresh database equals a converted 9.0 one (`tests/fixtures/db_9_0.sql`).
- **Startup data steps** (not schema) are core's `users.bootstrap()` (the first admin, `docs/access.md`) and the addons' `@addon.job(at_start=True)` jobs (e.g. tasks gives each user any default list they lack; about loads each user's prompt block).

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

The web server listens on port 8080. Every path but the public ones (`docs/access.md`) needs `Authorization: Bearer <token>` (a device's or user's token, or `API_TOKEN` when set), from inside a cluster too; the inbound webhook `/webhook/waha` takes no token but only events WAHA signed with `WAHA_WEBHOOK_SECRET` (`docs/whatsapp.md`). A server with no users and no `API_TOKEN` is in setup mode: only the setup page and API, the health check and static files answer until the first admin is created (everything else 503 `setup_required`; `docs/devices.md`). `DANA_INSECURE_DEV=1` for local development: no authentication, `127.0.0.1` only.

| Endpoint | What it returns |
|---|---|
| `POST /api/assistant/message` | Start a run (`{session_id, message, model?, images?}`) → SSE stream of its events, closed by a `state` frame |
| `GET /api/sessions/{id}/stream?run_id=&after=` | Reattach to the session's current/just-finished run (SSE); only `state` when nothing is running |
| `POST /api/sessions/main/clear` | New topic in the shared thread: Dana forgets it so far, messages stay (docs/agent-actions.md) |
| `GET /api/assistant/models` | Chat model catalog + default id (the profiles the caller's access level may use) |
| `GET /api/assistant/voice_stream` | WebSocket: PCM in, then the same run events as JSON frames, closed by `state` (or `no_speech`) |
| `GET /api/me` | The caller: `{id, name, access, capabilities, addons, languages, currency}` |
| `GET` / `PUT /api/profile` | The caller's languages and currency (`core/profile_api.py`) |
| `GET /api/setup` · `POST /api/setup/admin` | Setup mode (public, only while there are no users): `{setup_required, version}` / create the first admin `{code, name}` → 201 `{user, device, token}` with the session cookie; 403 `invalid_code`, 409 `already_set_up`, 429 (`docs/devices.md`) |
| `POST /api/pair` | A new device redeems a pairing code (public, rate-limited): `{code, kind, name?}` → `{token, device, user, server}`; 403 `invalid_code`, 409 `wrong_kind`, 429 |
| `POST /api/devices/pairings` · `GET` / `DELETE /api/devices/pairings/{id}` | Start a pairing for the caller (or `user_id` with `manage_users`) → 201 `{id, code, expires_at, qr, …}` / its status (`pending \| paired \| expired`) / cancel it |
| `GET /api/devices` · `PATCH` / `DELETE /api/devices/{id}` | The caller's devices (`?user=` with `manage_users`) / rename, room, role / remove (its token stops working at once) |
| `GET /api/household` · `POST /api/household/members` · `PATCH` / `DELETE /api/household/members/{id}` | The household (a member sees only themselves; changes need `manage_users`; the last admin and yourself are protected: 409) — `docs/household.md` |
| `GET` / `POST` / `DELETE /api/demo` · `POST` / `DELETE /api/demo/view` | The sample household (`manage_users`): loaded?, load (201, idempotent), remove all of it; view the app as a demo user / back |
| `GET` / `POST /api/zones` · `PATCH` / `DELETE /api/zones/{id}` · `PUT /api/zones/home` · `GET /api/places?q=` | The caller's zones (`docs/zones.md`); place search (Open-Meteo geocoding, `GEOCODING_URL`) |
| `POST /api/ha/zones/import` | Copy the caller's Home Assistant zones into their Dana zones once |
| `GET /api/wizard` · `POST /api/wizard/steps/{step}` · `POST /api/wizard/finish` · `PUT /api/wizard/home` | The caller's first-run wizard: steps and progress, mark a step done or skipped, finish, set Home (and for an admin the server timezone) |
| `GET` / `PUT /api/llm` | `manage_server`: the model provider and whether its key is set (last 4 characters only) / choose a provider and store a key after testing it (422 in plain words when refused) |
| `POST /api/phone/register` | The calling device's push registration: `{transport: "unifiedpush", endpoint}` or `{transport: "fcm", token}` (400 `fcm_not_configured` without `FCM_SERVICE_ACCOUNT_JSON`, 400 for an endpoint that is not https) |
| `GET /api/users` | `manage_users` only: `{users: [{id, name, access}]}` — the Addons page's user picker |
| `GET /api/addons` · `GET /api/addons/{name}` | The caller's addons (`?user=<id>` with `manage_users`): fields with their type, whether each credential is set (never a secret's value; an address's value is shown), settings, account, and whether the caller may change each addon / field (`can_change`, `why_not`, `rule`) (`docs/access.md` → Managing users) |
| `PUT /api/addons/{name}` | Connect (201) or update (200): `{credentials?, settings?}`, merged; 400 `invalid` with the field; 403 `forbidden` for an addon the level may not have |
| `DELETE /api/addons/{kind}` | Disconnect |
| `POST /api/addons/{kind}/test` | Does the service answer with these credentials: `{kind, checked, ok, reason}` (Home Assistant, WhatsApp, Calendar, cameras) |
| `GET /api/addons/{kind}/options` | Candidates for the settings, read from the service (Home Assistant: people, car sensors and devices, phone sensors, weather) |
| `GET /api/addons/catalog` · `POST /api/addons/install` · `POST /api/addons/{name}/remove` | `manage_server` only: the catalog / install a catalog entry (`{name}`) or a repository (`{repo, ref, path?}`), with `accept` (the touches the admin agreed to) → 201 `{name, commit, touches, restart_needed}`; 409 `confirm_touches {touches, added}` when it may touch more than the replaced version plus `accept` (nothing written; send it again with `accept` once agreed) / remove it (`../docs/addons.md`) |
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
| `GET /api/money/meals?months=` | The meal card: the budget now, its cycles with their card line, meals and places (`docs/money.md` → Meal cards) |
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
| `GET /api/google/connect` · `/api/google/callback` · `/api/google/status` | The Google sign-in that connects the caller's Google addons (`docs/access.md` → Google account) |
| `GET /api/ha/states?ids=a,b` / `?domains=light,climate` | Compact states of the caller's Home Assistant for control tiles (ids, in order) or pickers (domains); 1.5 s cache per connection, cleared by `POST /api/ha/service`; 403 `not_connected` without the addon |
| `GET /api/frigate/events/{camera}?limit=` | Latest Frigate detection events (audio dropped) + `server_time` |
| `GET /api/frigate/event/{id}/preview.gif` | Relay of Frigate's event preview GIF |
| `GET /api/frigate/event/{id}/clip.mp4` | Event clip, buffered (4-clip LRU) and served with Range support |
| `GET /api/people?sort=&q=` / `GET /api/people/{id}` | People with stats / one person: brief, timeline, tasks (`docs/people.md`) |
| `GET /api/people/lookup?phone=` | The incoming-call card for a number |
| `POST /api/phone/unlock` | Phone unlocked (`{at}` epoch ms) — the wake signal; returns `confirming` / `done` / `outside_window` (`unavailable` for a user without Home Assistant) |
| `GET /api/briefing/today` | Today's wake briefing (`?generate=1` makes it) |
| `GET /api/review/latest` | The latest weekly review with its proposals |
| `GET /api/app/version` | The Android app versions offered (from releases and uploads; manifest in `<data dir>/app-updates/`; App updates above) |
| `GET /api/app/download/{filename}` | Download an offered Android APK |
| `POST /api/app/upload` | Publish an Android APK you built (Bearer `APP_PUBLISH_TOKEN`; an older `versionCode` than the offered one: 409) |
