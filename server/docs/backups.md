# Backups

One archive file holds everything a Dana server keeps; an admin chooses when one is made, where it goes and how many
stay, downloads one in the browser, and restores one with `bin/dana backups restore`. Code: `core/backups.py` (making,
listing, pruning, the schedule, restoring), `core/backups_api.py` (the API), `cli/backups.py` (the CLI),
`static/backups.js` (Settings → Backups), the `backups` self-check in `core/checks.py`.

## What an archive holds

`dana-backup-YYYYMMDD-HHMMSS.tar.gz` (the server's local time; a second one in the same second gets `-2`), mode 0600:

| Member | What |
|---|---|
| `db.sqlite3` | A consistent copy of the database, made with SQLite's online backup API while the server runs (never a raw copy of the live file) |
| `credentials.key` | The generated credentials key (`<data dir>/credentials.key`), when the key comes from that file. When `CREDENTIALS_KEY` is set in env the key is **not** in the archive: keep it in your secret store, a restore needs the same key to read the stored credentials. The manifest and Settings → Backups say which |
| `files/…` | Everything else in the data directory: `recordings/`, `media/`, `uploads/`, `knowledge/`, `addons/` (installed add-ons), `money/` and any other folder or file there |
| `manifest.json` | `format` (1), `dana_version`, `created_at`, `timezone`, `schema` (each owner's latest applied migration, `core` included), `credentials_key` (`file` / `env` / `none`), `dirs`, `files` and `files_bytes`, `left_out` |

Left out on purpose: `snapshots/` (copies of the database itself, taken before migrations), `app-updates/` (offered
Android APKs: read again from GitHub Releases or uploaded again), `backups/` and `.backup-work/` (backups themselves),
the live database's own files (`-wal`, `-shm`, `-journal`), symbolic links and half-written `*.tmp` / `*.partial`
files. An archive holds every user's data and the key that decrypts their credentials: keep it as safe as the server.

## Settings

Stored in `server_settings['backups']` (no env, no migration), changed in Settings → Backups or `PUT /api/backups`:

- **Schedule**: off (the default), daily at HH:MM, or weekly on a day at HH:MM, in the server's timezone
  (`core/clock.py`). The loop (`backups.schedule_loop`, started with the server) looks every minute; a backup is due at
  the first scheduled time after the last scheduled start (or after the schedule last changed), so a server that was
  down at that time makes one backup when it starts, not one per missed run.
- **Destination**: an absolute folder on the server; empty means the default, `/backups` when a volume is mounted there
  (`compose.yaml`; the image's own empty `/backups` folder does not count: it would be lost on restart), else
  `<data dir>/backups`. Saving checks that it is absolute, is not a file, can be
  created and the server can write to it. It may not be the data directory or a folder inside it, except
  `<data dir>/backups` and its subfolders (left out of every archive). Best on another disk than the data: a backup in
  `<data dir>/backups` survives a mistake or a broken database, not a lost volume.
- **Keep**: the newest N archives in the destination stay (1–100, default 7); after each backup there the older ones
  are removed. Only files named like an archive are ever listed or removed.

One backup runs at a time (another answers 409 `busy`). It runs in a worker thread, writes under a hidden temporary
name in the destination (`.dana-backup-…`, removed by the next backup if one was interrupted) and renames the archive
when complete. The last attempt and the last success are kept in `server_settings['backups.state']`.

## Self-check

`backups`, for users with `manage_server` (`core/checks.py`): red with the reason when the last backup into the
destination failed (scheduled, "Back up now" or `bin/dana backups create`), and when a schedule is more than two hours
past due with no start (the server is stuck). A download in the browser does not count. Off is green ("no schedule").

## API

All `manage_server` (others 403 `{"error": "forbidden", "needs": "manage_server"}`):

| Endpoint | What |
|---|---|
| `GET /api/backups` | `{schedule: {every, at, day}, destination, default_destination, keep, next_at, running, last, last_ok, backups: [{name, size, created_at}], credentials_key: file \| env \| none, timezone}` |
| `PUT /api/backups` | `{schedule?, destination?, keep?}` → the same; 400 `invalid` `{field, detail}`, nothing stored |
| `POST /api/backups/run` | Back up into the destination now, answered when done: `{backup: {name, size, created_at}, status}`; 409 `busy`; 500 `backup_failed` `{detail}` |
| `GET /api/backups/download` | A fresh archive as an attachment, built under `<data dir>/.backup-work` and deleted as it is sent (not kept, not pruned, not recorded); 409 `busy` |
| `GET /api/backups/files/{name}` | One archive of the destination; any name that is not an archive's (a path, another file) → 404 |

`last` / `last_ok`: `{ok, at, trigger: schedule | manual | cli, name, size, seconds, removed}`, or `{ok: false, at,
trigger, error}`.

## CLI

```bash
bin/dana backups create                    # into the destination, pruned to keep
bin/dana backups list                      # the archives in the destination
bin/dana backups restore ARCHIVE           # says what it would restore, changes nothing
bin/dana backups restore ARCHIVE --yes     # with the server stopped
```

A restore checks the archive first (a Dana backup of this format, only plain files under its own names, nothing
outside the data directory, a database that opens), then writes a copy of the current database to
`snapshots/db-before-restore-<time>.sqlite3`, replaces the database (removing its `-wal` / `-shm`), the key file when
the archive has one, and each folder and file of `files/` (a folder is replaced whole, so files added after the backup
go; folders the archive does not have are left alone). An older database is migrated at the next start; a newer one
does not start on an older Dana. When the manifest says the key came from env, start the server with that same
`CREDENTIALS_KEY`. Steps for Docker Compose and Kubernetes: `operations.md`, "Backups".

`tests/test_backups.py` rehearses it: back up, change data, restore, the change is gone.
