# Operations: releasing, deploying, production, debugging

## Running it

- **Quick start** (the published image, no checkout): `compose.yaml` at the repository root runs `ghcr.io/ronganam/dana:latest`, the image each release publishes (`.github/workflows/release.yml`; amd64 and arm64).

  ```bash
  curl -fsSLO https://raw.githubusercontent.com/ronganam/dana/master/compose.yaml
  docker compose up -d
  docker compose logs dana    # open the setup link it prints
  ```

  The setup link creates the first admin in the browser and opens the first-run wizard (you, Home, the model and its key, your phone, add-ons, sample data); settings go in an optional `.env` next to `compose.yaml` (`docs/configuration.md`). Headless (no browser at hand): put `API_TOKEN` in that `.env` before the first start. Upgrade: `docker compose pull && docker compose up -d`. The administration CLI is `dana` on the image's PATH: `docker compose exec dana dana users list`, `docker compose exec dana dana addons install <name>` (into `<data dir>/addons`, in the volume, kept across upgrades; then `docker compose restart dana`), or from your own machine `bin/dana addons install <name | repo@commit | ./folder> --server <url> --restart` (`../../docs/addons.md`, "Installing an addon"). Pin a release with `DANA_VERSION=<version>` in the `.env` (the image tag; `bin/deploy` sets it: "Deploying"). The image ships no add-on catalog: it reads the `dana-catalog` repository's `catalog.json` (`DANA_CATALOG_URL`; offline, set `DANA_CATALOG_FILE`). Phones, tablets and TVs find the server on the home network when something advertises `_dana._tcp` (`docs/devices.md`, "Finding the server on the home network": an Avahi example). The data lives in the `data` volume; the port is `127.0.0.1:8080` (reverse proxy or VPN to reach it from elsewhere, below).
- **Locally**: `python main.py` (README). The web UI and API on `:8080`, Telegram if `TELEGRAM_BOT_TOKEN` is set. It needs only an LLM key: a fresh database starts in setup mode and logs the setup link (`docs/devices.md`); `API_TOKEN` instead creates the first admin from env (headless), and `DANA_INSECURE_DEV=1` turns authentication off (`127.0.0.1` only).
- **Docker Compose from source** (contributors): `docker compose up -d` in `server/` builds the image and keeps the data in the `data` volume (`compose.yaml`; an addon's worker runs under its profile, e.g. `docker compose --profile money_il up -d`: "Workers" below). The port is published on `127.0.0.1:8080` only: to reach Dana from other machines, put a reverse proxy with HTTPS in front of it (Caddy, Traefik, nginx) or change the mapping knowingly. The healthcheck polls `/api/health`.
- **Data to back up**: the database directory (`DATABASE_PATH`'s folder: the database, `recordings/`, `media/`, `uploads/`…) including `credentials.key` when `CREDENTIALS_KEY` is not set (the generated key: without it the stored addon credentials are lost). Settings → Backups makes one archive of all of it on a schedule ("Backups" below).
- **Kubernetes** (one replica: SQLite): the image (`Dockerfile`, `app` stage) runs as UID 1001 and keeps everything it writes in the directory holding the database (`DATABASE_PATH`, default `/app/db/db.sqlite3`; `core/paths.py`), so mount one volume there. Secrets come in as env vars (`docs/configuration.md`). Probes: TCP on 8080. An addon's worker runs as a sidecar in the same pod ("Workers" below; e.g. `money_il`'s on `127.0.0.1:8090`, mounting only `<data dir>/money`). Backups: "Backups" below.

## Dependencies

- `requirements.txt`: the direct dependencies, each with a lower bound (what the code imports, plus the `mcp` workaround noted there). `requirements.lock`: every package pinned with its hashes, for Python 3.12 and newer on every platform; the image installs it (`pip install --require-hashes -r requirements.lock`) and so does the suggested local setup (README).
- After changing `requirements.txt` (or to take upgrades), regenerate the lock and run the suite:

  ```bash
  uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes --no-header --no-annotate -o requirements.lock
  .venv/bin/pip install -r requirements.lock && bin/test
  ```

  (`uv pip compile --upgrade` takes the newest versions; without it, versions already pinned stay.) Commit both files together; the pre-push hook then also runs the image's `test` stage.
- No new runtime dependency without a reason in the commit; check its license against `../THIRD_PARTY.md` (e.g. `mutagen` is GPL-2.0-or-later).

## Releasing

Every deployment runs a published image, the maintainer's production included: `ghcr.io/ronganam/dana:<version>` (amd64 and arm64), built by `.github/workflows/release.yml` from a pushed tag. Nothing builds an image on your machine.

1. Merge to `master`. Pushing master runs the tests first: the repository's `.githooks/pre-push` hook (once per clone: `git config core.hooksPath .githooks`), on a clean checkout of the pushed commit, runs `bin/test` when the push changes more than docs under `server/` or `examples/`, plus the Dockerfile's `test` stage (the suite on the exact image, Python 3.14) when it changes `server/Dockerfile` or `server/requirements.txt` / `requirements.lock`. A failure stops the push; `git push --no-verify` skips it. Android changes are not tested by the hook. It deploys nothing.
2. Bump the version: `server/VERSION` and the apps' `versionName` / `versionCode` (`android/app/build.gradle.kts`, `android/wear/build.gradle.kts`; `../../android/docs/release.md`), in one commit.
3. Tag and push: `git tag v<version> && git push origin v<version>`. The release workflow checks the tag equals `server/VERSION` and the apps' `versionName`, publishes the server image as `:<version>` and `:latest`, and makes the GitHub Release with the signed APKs.

`/api/health` reports the running image's `VERSION`, and the web release digest (`pages_api.web_release()`, the dashboard's and the service worker's `?v=`) follows it, so every release reaches cached clients.

## Deploying

**`bin/deploy [<version>] [--wait]`** (default: `server/VERSION`) rolls out a published release. It reads the site's `deploy.env` (`site/deploy.env` at the repository root, or `$DANA_SITE/deploy.env`; `DANA_DEPLOY_CONFIG` names another file; the template is `site.example/deploy.env.example`; `../../docs/site.md`) and builds nothing:

1. waits until `$DANA_IMAGE:<version>` exists (`docker manifest inspect`; `DANA_IMAGE` defaults to `ghcr.io/ronganam/dana`), saying so while the release workflow is still building it, up to `DANA_IMAGE_WAIT` seconds (default 1800);
2. with `DANA_EXEC` (how to run `dana` where the server runs: `kubectl -n dana exec deploy/dana -c dana --`, or `docker compose exec -T dana`), asks `dana addons workers --json` for the installed addons' workers ("Workers" below);
3. rolls it out: with `DANA_GITOPS_REPO`, writes `<version>` into every `tag:` line of `DANA_GITOPS_VALUES` and each worker's pinned image into its `image:` line, in that repository (from its own clone in `~/.cache/dana-deploy/gitops`), and pushes, for ArgoCD / Flux to roll the pod; `--wait` then waits until a pod in `DANA_K8S_NAMESPACE` matching `DANA_K8S_SELECTOR` runs that image and is ready. With `DANA_COMPOSE_DIR` (the folder of the repository root's `compose.yaml`), writes `DANA_VERSION=<version>` and each worker's `<ADDON>_WORKER_TAG` into its `.env`, then `docker compose pull` and `up -d`. With neither, it says the image is ready.

Roll back with `bin/deploy <older version>` (within one schema: "Database schema" below). The whole release, in order: bump, tag, push the tag, then `bin/deploy <version> --wait`.

### Workers

An addon that declares `worker=Worker(port, url_env, image)` (`core/addons/hooks.py`) has a sidecar. Its image is **published** by the addon's own repository (`image="ghcr.io/…"`, built from its `worker/` folder: e.g. `money_il`'s bank worker, `ghcr.io/ronganam/dana-addon-money-il-worker`, Node + Chromium, built and pushed by that repository's workflow on every push to `main`, tagged with the commit and `latest`). No official addon has a worker; the app image leaves `addons/*/worker` out.

- **`bin/dana addons workers --json`**, run where the data lives, lists `[{name, image, tag}]` for every installed addon (`<data dir>/addons`) whose worker names an image, **pinned**: to the tag or digest (`tag` `@sha256:…`) the manifest names when it names one, else to the commit the addon was installed at (its `.installed.json`; `core/addon_dirs.py` `worker_image`). An addon installed from a folder records no commit, so its image needs a tag; one without either fails the command (and the deploy). A worker that names no image is not deployed by `bin/deploy` (run it yourself: `bin/dana addons dev` builds it locally).
- **GitOps values**: `bin/deploy` rewrites any `image: "<published image>:<old tag>"` (or `@<digest>`) to the pinned one, so name each worker's image in full under one key per addon; the convention is `workers.<addon>.image`:

  ```yaml
  image:
    repository: ghcr.io/ronganam/dana
    tag: "10.4.0"
  workers:
    money_il:
      image: "ghcr.io/ronganam/dana-addon-money-il-worker:<commit>"   # pinned by bin/deploy
      port: 8090
  ```

  The chart (yours) runs it in the pod next to the app, sharing its network namespace and data volume, and the app reaches it at the addon's `url_env` (`MONEY_WORKER_URL`, default `http://127.0.0.1:8090`).
- **Compose** (`compose.yaml`): each worker is a service under a profile named after its addon: `docker compose --profile money_il up -d` adds `money-il-worker`, run from its published image (`MONEY_IL_WORKER_TAG`, default `latest`; `bin/deploy` sets it to the pinned tag).
- Tests: a worker's own (`addons/<name>/worker/test/test_*.mjs`) run in `bin/test` with the rest; an installed addon's run in its own repository.

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

## Backups

What an archive holds, the settings, the API and the CLI: `docs/backups.md`. In short: Settings → Backups (admins) picks a schedule (off, daily or weekly at a time, in the server's timezone), a destination folder on the server (default `/backups` when a volume is mounted there, else `<data dir>/backups`) and how many to keep; "Back up now" and "Download backup" make one at once. Each archive is `dana-backup-<date>-<time>.tar.gz`: a consistent copy of the database, the generated credentials key file (not a `CREDENTIALS_KEY` from env: keep that in your secret store), the data folders and a manifest. A failed backup is the admins' `backups` self-check.

- **Docker Compose**: `compose.yaml` mounts the `backups` volume at `/backups` (the image creates that folder for UID 1001). To keep backups off the machine's data disk, bind-mount a folder there instead (`- /mnt/nas/dana-backups:/backups`, writable by UID 1001). Restore, from an archive in that folder:

  ```bash
  docker compose stop dana
  docker compose run --rm --no-deps dana dana backups restore /backups/dana-backup-20261123-033000.tar.gz          # what it would do
  docker compose run --rm --no-deps dana dana backups restore /backups/dana-backup-20261123-033000.tar.gz --yes
  docker compose start dana
  ```

  An archive downloaded in the browser: add `-v ./dana-backup-….tar.gz:/restore.tar.gz:ro` to `run` and restore `/restore.tar.gz`.
- **Kubernetes**: mount a second volume (another PVC, NFS) at `/backups` in the pod, or set the destination to a folder on it; with only the data volume, backups go to `<data dir>/backups` on the same volume (protects against mistakes, not a lost volume: copy them off, e.g. with your volume backups). Restore with the pod stopped: scale the deployment to 0, run a one-off pod with the same image and both volumes (`kubectl run … --overrides` or a Job), `dana backups restore /backups/<archive> --yes` in it, delete it, scale back to 1. A volume-level backup (Velero) can still run `python3 -m core.db_snapshot` as its pre-backup hook: it writes a consistent `<data dir>/snapshots/db.sqlite3` (a raw copy of a live SQLite file can catch it mid-write).
- **Restore** (`bin/dana backups restore`, the server stopped) keeps the database it replaces as `snapshots/db-before-restore-<time>.sqlite3`; an older backup's database is migrated at the next start, a newer one does not start on an older Dana. When the manifest says the key came from env, start with that same `CREDENTIALS_KEY`.

## Debugging production

```bash
NS=dana    # your namespace
POD=$(kubectl -n "$NS" get pod -l app=dana -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" logs "$POD" --tail 200 | grep -v aiohttp.access   # the access log: only non-GET requests, 4xx / 5xx and slow ones (core/debug_api.py QuietAccessLog)
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
| `GET` / `PUT /api/profile` | The caller's languages, currency and `wake_window` (`core/profile_api.py`) |
| `GET` / `PUT /api/dashboard?device=`, `GET /api/dashboard/widget?id=`, `GET /api/dashboard/catalog`, `GET /dashboard` | A tablet's or TV's dashboard: layout and widget data (`core/dashboard_api.py`, `docs/dashboard.md`) |
| `GET /api/usage`, `PUT /api/usage/budget` | Usage and cost; the monthly budget (admin) (`core/usage_api.py`) |
| `GET /api/setup` · `POST /api/setup/admin` | Setup mode (public, only while there are no users): `{setup_required, version}` / create the first admin `{code, name}` → 201 `{user, device, token}` with the session cookie; 403 `invalid_code`, 409 `already_set_up`, 429 (`docs/devices.md`) |
| `POST /api/pair` | A new device redeems a pairing code (public, rate-limited): `{code, kind, name?}` → `{token, device, user, server}`; 403 `invalid_code`, 409 `wrong_kind`, 429 |
| `POST /api/devices/pairings` · `GET` / `DELETE /api/devices/pairings/{id}` | Start a pairing for the caller (or `user_id` with `manage_users`) → 201 `{id, code, expires_at, qr, …}` / its status (`pending \| paired \| expired`) / cancel it |
| `GET /api/devices` · `PATCH` / `DELETE /api/devices/{id}` | The caller's devices and `unpaired_tokens` (tokens of no device; `?user=` with `manage_users`) / rename, room, role / remove (its token stops working at once) |
| `DELETE /api/devices/tokens/{id}` | Revoke a token of no device (the caller's own, or anyone's with `manage_users`; another user's answers 404) (`docs/devices.md`, "Tokens without a device") |
| `GET /api/live` · `POST /api/live/{id}/act` · `POST /api/live/{id}/select` · `DELETE /api/live/{id}` | The caller's live activities / a button / a choice / stop (`docs/live.md`; another user's id answers 404) |
| `GET /api/household` · `POST /api/household/members` · `PATCH` / `DELETE /api/household/members/{id}` | The household (a member sees only themselves; changes need `manage_users`; the last admin and yourself are protected: 409) — `docs/household.md` |
| `GET` / `POST` / `DELETE /api/demo` · `POST` / `DELETE /api/demo/view` | The sample household (`manage_users`): loaded?, load (201, idempotent), remove all of it; view the app as a demo user / back |
| `GET` / `POST /api/zones` · `PATCH` / `DELETE /api/zones/{id}` · `PUT /api/zones/home` · `GET /api/places?q=` | The caller's zones (`docs/zones.md`); place search (Open-Meteo geocoding, `GEOCODING_URL`) |
| `POST /api/ha/zones/import` | Copy the caller's Home Assistant zones into their Dana zones once |
| `GET /api/wizard` · `POST /api/wizard/steps/{step}` · `POST /api/wizard/finish` · `PUT /api/wizard/home` | The caller's first-run wizard: steps and progress, mark a step done or skipped, finish, set Home (and for an admin the server timezone) |
| `GET` / `PUT /api/llm` | `manage_server`: the model provider and whether its key is set (last 4 characters only) / choose a provider and store a key after testing it (422 in plain words when refused) |
| `GET` / `PUT /api/phone/app-icons…` | The caller's app icons from the phone: `GET /api/phone/app-icons` → `{icons: {package: hash}}`; `PUT /api/phone/app-icons/{package}` (PNG, square 48-192 px, ≤ 64 KB, ≤ 500 per user) → 204; `GET /api/phone/app-icons/{package}.png` (ETag, 304) (`docs/notifications.md`, "App icons") |
| `POST /api/phone/register` | The calling device's push registration: `{transport: "unifiedpush", endpoint}` or `{transport: "fcm", token}` (400 `fcm_not_configured` without `FCM_SERVICE_ACCOUNT_JSON`, 400 for an endpoint that is not https) |
| `GET /api/users` | `manage_users` only: `{users: [{id, name, access}]}` — the Addons page's user picker |
| `GET /api/addons` · `GET /api/addons/{name}` | The caller's addons (`?user=<id>` with `manage_users`): fields with their type, whether each credential is set (never a secret's value; an address's value is shown), settings, account, and whether the caller may change each addon / field (`can_change`, `why_not`, `rule`) (`docs/access.md` → Managing users) |
| `PUT /api/addons/{name}` | Connect (201) or update (200): `{credentials?, settings?}`, merged; 400 `invalid` with the field; 403 `forbidden` for an addon the level may not have |
| `DELETE /api/addons/{kind}` | Disconnect |
| `POST /api/addons/{kind}/test` | Does the service answer with these credentials: `{kind, checked, ok, reason}` (Home Assistant, WhatsApp, Calendar, cameras) |
| `GET /api/addons/{kind}/options` | Candidates for the settings, read from the service (Home Assistant: people, car sensors and devices, phone sensors, weather) |
| `GET /api/addons/catalog` · `POST /api/addons/install` · `POST /api/addons/{name}/remove` | `manage_server` only: the catalog / install a catalog entry (`{name}`), a repository (`{repo, ref, path?}`) or an uploaded folder (`multipart/form-data`: `archive`, a `.tar.gz` of the addon folder, at most 20 MB, 2000 files, 50 MB unpacked; 413 `too_large`), with `accept` (the touches the admin agreed to; a JSON part for an upload) → 201 `{name, kind, commit?, touches, restart_needed}`; 409 `confirm_touches {touches, added}` when it may touch more than the replaced version plus `accept` (nothing written; send it again with `accept` once agreed) / remove it (`../../docs/addons.md`, "Installing an addon") |
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
| `POST /api/signals` | The caller's phone events (unlock, screen, car, zone, sleep) → `{accepted, rejected}` (phone addon; `docs/signals.md`) |
| `GET /api/signals` | The caller's signals and their sources, the wake window (`core/signals_api.py`) |
| `POST /api/checks/{key}/fix {fix}` | Run a failing self-check's server fix for the caller → `{ok, message}` (`core/now_api.py`, `docs/now.md`) |
| `GET` / `PUT /api/backups`, `POST /api/backups/run`, `GET /api/backups/download`, `GET /api/backups/files/{name}` | Backups: settings, the list, back up now, a fresh archive to the browser, a stored archive (admin; `core/backups_api.py`, `docs/backups.md`) |
| `GET /api/briefing/today` | Today's wake briefing (`?generate=1` makes it) |
| `GET /api/review/latest` | The latest weekly review with its proposals |
| `GET /api/app/version` | The Android app versions offered (from releases and uploads; manifest in `<data dir>/app-updates/`; App updates above) |
| `GET /api/app/download/{filename}` | Download an offered Android APK |
| `POST /api/app/upload` | Publish an Android APK you built (Bearer `APP_PUBLISH_TOKEN`; an older `versionCode` than the offered one: 409) |
