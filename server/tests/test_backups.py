"""Backups: core/backups.py, core/backups_api.py, cli/backups.py and the ``backups`` self-check.
Run: bin/test test_backups

The archive (a consistent database copy that opens with the data, the key file when the key is a file and the
manifest's "env" when it is not, the data folders without snapshots/ app-updates/ backups/, the manifest's
versions); the schedule (daily, weekly, in a timezone, due after the last start or the last change, the loop
starting one); pruning to the newest N without touching other files; the settings' validation (relative, inside
the data directory, a file, not writable, keep, schedule); the API (a member 403 everywhere, back up now, busy
409, a download streamed and deleted, a listed archive by name, traversal refused); a failing destination turns
the admin's self-check red (a member has none); the restore rehearsal: back up, change data, restore with
``bin/dana backups restore`` (a dry run first), the change is gone and the database before it kept.
"""

import asyncio
import atexit
import datetime
import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import zoneinfo
from pathlib import Path
from typing import Any, Dict, List, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("backups_test")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import addons  # noqa: F401
from addons import bootstrap as addons_bootstrap
from cli import main as cli
from core import backups, backups_api, checks, db, paths, server_settings, users
from core.addons import crypto
from core.auth import setup_auth

db.migrate()
users.bootstrap()
addons_bootstrap.bootstrap()  # the credentials key file
paths.ensure_data_dirs()
ADMIN = users.first_admin()
assert ADMIN is not None
MAYA = users.create("Maya")
AUTH = {u.id: {"Authorization": f"Bearer {users.issue_token(u.id, 'test')[0]}"} for u in (ADMIN, MAYA)}
OUTSIDE = Path(tempfile.mkdtemp(prefix="dana-backups-dest-"))
atexit.register(shutil.rmtree, OUTSIDE, True)
UTC = datetime.timezone.utc

results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def user_names(path: Path = paths.DATABASE_PATH) -> List[str]:
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT name FROM users"))
    finally:
        conn.close()


def write(rel: str, data: bytes) -> Path:
    p = paths.DATA_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def members(archive: Path) -> Dict[str, tarfile.TarInfo]:
    with tarfile.open(archive, "r:gz") as tar:
        return {m.name: m for m in tar.getmembers()}


def read_member(archive: Path, name: str) -> bytes:
    with tarfile.open(archive, "r:gz") as tar:
        f = tar.extractfile(name)
        assert f is not None
        return f.read()


def set_destination(folder: Path, **more: Any) -> None:
    backups.save_settings({"destination": str(folder), **more}, by=ADMIN.id)


# --- The archive ------------------------------------------------------------------------------------------

def test_archive() -> Path:
    write("recordings/call-1.m4a", b"audio-bytes")
    write("media/link/clip.mp4", b"video-bytes")
    write("uploads/images/photo.jpg", b"jpeg-bytes")
    write("knowledge/quote.pdf", b"pdf-bytes")
    write("snapshots/db-before-core-0002.sqlite3", b"old copy")
    write("app-updates/phone-1.0.apk", b"apk")
    write("recordings/half.tmp", b"partial")
    os.symlink("/etc/hostname", paths.DATA_DIR / "media" / "link-out")
    check("key: generated into the key file (the default)", crypto.key_source() == "file")
    set_destination(OUTSIDE, keep=7)
    b = backups.back_up("manual")
    archive = OUTSIDE / b.name
    m = members(archive)
    check("archive: named dana-backup-<date>-<time>.tar.gz, owner-only", backups.NAME_RE.match(b.name)
          and archive.stat().st_mode & 0o077 == 0, (b.name, oct(archive.stat().st_mode)))
    for name in ("manifest.json", "db.sqlite3", "credentials.key", "files/recordings/call-1.m4a",
                 "files/media/link/clip.mp4", "files/uploads/images/photo.jpg", "files/knowledge/quote.pdf"):
        check(f"archive: holds {name}", name in m, sorted(m))
    left_out = [n for n in m if n.startswith(("files/snapshots", "files/app-updates", "files/backups", "files/.backup"))
                or n.endswith((".tmp", "link-out")) or n in ("files/credentials.key", f"files/{paths.DATABASE_PATH.name}")]
    check("archive: no snapshots, app updates, half-written files, links, live database or key under files/",
          not left_out, left_out)
    check("archive: the key file's content", read_member(archive, "credentials.key").decode().strip()
          == paths.CREDENTIALS_KEY_FILE.read_text().strip())
    with tempfile.TemporaryDirectory() as t:
        Path(t, "db.sqlite3").write_bytes(read_member(archive, "db.sqlite3"))
        names = user_names(Path(t, "db.sqlite3"))
    check("archive: the database copy opens and has the users", names == sorted([ADMIN.name, "Maya"]), names)
    manifest = json.loads(read_member(archive, "manifest.json"))
    check("manifest: format, version, created at, timezone, key from the file",
          manifest["format"] == 1 and manifest["dana_version"] and manifest["created_at"]
          and manifest["timezone"] and manifest["credentials_key"] == "file", manifest)
    check("manifest: schema versions per owner (core's included)",
          manifest["schema"].get("core", 0) >= 1 and manifest["schema"] == {
              a: max(v for x, v in db.applied_migrations() if x == a) for a, _ in db.applied_migrations()},
          manifest["schema"])
    check("manifest: what is inside", {"recordings", "media", "uploads", "knowledge"} <= set(manifest["dirs"])
          and "snapshots" not in manifest["dirs"] and manifest["files"] == 4 and manifest["files_bytes"] > 0
          and "snapshots" in manifest["left_out"] and "app-updates" in manifest["left_out"], manifest)
    st = backups.state()
    check("state: the last attempt and the last success recorded",
          st["last"]["ok"] and st["last"]["name"] == b.name and st["last_ok"]["size"] == b.size
          and st["last"]["trigger"] == "manual", st)
    check("archive: no work files left in the destination", sorted(p.name for p in OUTSIDE.iterdir()) == [b.name])
    (paths.DATA_DIR / "media" / "link-out").unlink()

    os.environ["CREDENTIALS_KEY"] = paths.CREDENTIALS_KEY_FILE.read_text().strip()
    try:
        b2 = backups.back_up("manual")
        m2 = members(OUTSIDE / b2.name)
        man2 = json.loads(read_member(OUTSIDE / b2.name, "manifest.json"))
        check("key from env: not in the archive, the manifest and the status say so",
              "credentials.key" not in m2 and man2["credentials_key"] == "env"
              and backups.status()["credentials_key"] == "env", (sorted(m2)[:5], man2["credentials_key"]))
        check("a second backup in the same second gets its own name", b2.name != b.name and backups.NAME_RE.match(b2.name))
    finally:
        del os.environ["CREDENTIALS_KEY"]
    return archive


# --- Schedule ---------------------------------------------------------------------------------------------

def test_schedule() -> None:
    tokyo = zoneinfo.ZoneInfo("Asia/Tokyo")
    daily = {"every": "daily", "at": "03:30", "day": "sun"}
    after = datetime.datetime(2026, 11, 23, 17, 0, tzinfo=UTC)  # 02:00 on Tuesday the 24th in Tokyo
    nxt = backups.next_run(daily, after, tokyo)
    check("daily: the next 03:30 in the server's zone (Tokyo, the same night)",
          nxt == datetime.datetime(2026, 11, 24, 3, 30, tzinfo=tokyo), nxt)
    nxt = backups.next_run(daily, datetime.datetime(2026, 11, 23, 19, 0, tzinfo=UTC), tokyo)  # 04:00 Tokyo
    check("daily: past today's time → tomorrow's", nxt == datetime.datetime(2026, 11, 25, 3, 30, tzinfo=tokyo), nxt)
    nxt = backups.next_run(daily, datetime.datetime(2026, 11, 23, 3, 30, tzinfo=UTC), UTC)
    check("daily: exactly at the time → the next day (strictly after)",
          nxt == datetime.datetime(2026, 11, 24, 3, 30, tzinfo=UTC), nxt)
    weekly = {"every": "weekly", "at": "22:15", "day": "sun"}
    nxt = backups.next_run(weekly, datetime.datetime(2026, 11, 23, 12, 0, tzinfo=UTC), UTC)  # a Monday
    check("weekly: the coming Sunday", nxt == datetime.datetime(2026, 11, 29, 22, 15, tzinfo=UTC), nxt)
    nxt = backups.next_run(weekly, datetime.datetime(2026, 11, 29, 21, 0, tzinfo=UTC), UTC)
    check("weekly: later the same Sunday", nxt == datetime.datetime(2026, 11, 29, 22, 15, tzinfo=UTC), nxt)
    nxt = backups.next_run({**weekly, "day": "mon"}, datetime.datetime(2026, 11, 29, 20, 0, tzinfo=UTC), tokyo)
    check("weekly: Monday in Tokyo is already Monday 05:00 there → that evening",
          nxt == datetime.datetime(2026, 11, 30, 22, 15, tzinfo=tokyo), nxt)
    check("off: never", backups.next_run({"every": "off", "at": "03:30", "day": "sun"}, after, UTC) is None)

    now = backups.clock.now()
    backups.save_settings({"schedule": {"every": "daily", "at": now.strftime("%H:%M")}}, by=ADMIN.id)
    check("a schedule saved now is not due until its time comes", not backups.is_due(now))
    stored = server_settings.get(backups.SETTINGS_KEY)
    stored["set_at"] = (now - datetime.timedelta(days=2)).isoformat()
    server_settings.put(backups.SETTINGS_KEY, stored)
    check("a server that was down: due at once (one catch-up)", backups.is_due(now))
    server_settings.put(backups.STATE_KEY, {**(server_settings.get(backups.STATE_KEY) or {}),
                                            "last_scheduled_at": now.isoformat()})
    check("after today's start: not due again until tomorrow", not backups.is_due(now)
          and backups.next_due(now) > now)
    backups.save_settings({"schedule": {"every": "off"}}, by=ADMIN.id)
    check("off: never due", not backups.is_due(now) and backups.next_due(now) is None)


async def test_loop() -> None:
    now = backups.clock.now()
    server_settings.put(backups.STATE_KEY, {**(server_settings.get(backups.STATE_KEY) or {}), "last_scheduled_at": None})
    backups.save_settings({"schedule": {"every": "daily", "at": (now - datetime.timedelta(minutes=5)).strftime("%H:%M")}},
                          by=ADMIN.id)
    stored = server_settings.get(backups.SETTINGS_KEY)
    stored["set_at"] = (now - datetime.timedelta(days=1)).isoformat()
    server_settings.put(backups.SETTINGS_KEY, stored)
    before = {b.name for b in backups.list_backups(OUTSIDE)}
    task = asyncio.create_task(backups.schedule_loop())
    for _ in range(100):
        await asyncio.sleep(0.1)
        if (backups.state()["last"] or {}).get("trigger") == "schedule":
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    st = backups.state()
    check("loop: the due backup ran as 'schedule' and was written",
          (st["last"] or {}).get("trigger") == "schedule" and st["last"]["ok"]
          and st["last"]["name"] not in before and st["last_scheduled_at"], st)
    check("loop: not due again right after", not backups.is_due())
    backups.save_settings({"schedule": {"every": "off"}}, by=ADMIN.id)


# --- Pruning ----------------------------------------------------------------------------------------------

def test_prune() -> None:
    folder = Path(tempfile.mkdtemp(prefix="dana-prune-"))
    try:
        names = [f"dana-backup-2026110{d}-033000.tar.gz" for d in range(1, 6)]
        for n in names:
            (folder / n).write_bytes(b"x")
        (folder / "notes.txt").write_text("mine")
        (folder / "dana-backup-latest.tar.gz").write_bytes(b"not ours")
        removed = backups.prune(folder, 2)
        left = sorted(p.name for p in folder.iterdir())
        check("prune: the newest 2 stay, the older 3 are removed", sorted(removed) == names[:3]
              and left == sorted(names[3:] + ["notes.txt", "dana-backup-latest.tar.gz"]), (removed, left))
        check("list: newest first, only archives", [b.name for b in backups.list_backups(folder)] == [names[4], names[3]])
        second = "dana-backup-20261105-033000-2.tar.gz"
        (folder / second).write_bytes(b"x")
        check("list: a second archive of the same second is the newer one",
              [b.name for b in backups.list_backups(folder)] == [second, names[4], names[3]])
        check("prune: keeps that one over the first of its second", backups.prune(folder, 1) == [names[4], names[3]])
    finally:
        shutil.rmtree(folder, True)
    set_destination(OUTSIDE, keep=2)
    backups.back_up("manual")
    check("back up: prunes the destination to keep", len(backups.list_backups(OUTSIDE)) == 2,
          [b.name for b in backups.list_backups(OUTSIDE)])
    set_destination(OUTSIDE, keep=7)


# --- Settings validation ----------------------------------------------------------------------------------

def test_settings() -> None:
    def refused(body: Dict[str, Any], field: str) -> bool:
        before = server_settings.get(backups.SETTINGS_KEY)
        try:
            backups.save_settings(body, by=ADMIN.id)
        except backups.Invalid as e:
            return e.field == field and server_settings.get(backups.SETTINGS_KEY) == before
        return False

    a_file = OUTSIDE / "a-file"
    a_file.write_text("x")
    check("destination: a relative path refused", refused({"destination": "backups"}, "destination"))
    check("destination: the data directory itself refused", refused({"destination": str(paths.DATA_DIR)}, "destination"))
    check("destination: a folder inside the data directory refused",
          refused({"destination": str(paths.RECORDINGS_DIR)}, "destination"))
    check("destination: a file refused", refused({"destination": str(a_file)}, "destination"))
    a_file.unlink()
    if os.getuid() != 0:
        locked = OUTSIDE.parent / f"{OUTSIDE.name}-locked"
        locked.mkdir(mode=0o500)
        try:
            check("destination: a folder the server cannot write refused", refused({"destination": str(locked)}, "destination"))
            check("destination: one that cannot be created refused",
                  refused({"destination": str(locked / "sub")}, "destination"))
        finally:
            locked.chmod(0o700)
            locked.rmdir()
    for keep in (0, 101, "7", True, 2.5):
        check(f"keep: {keep!r} refused", refused({"keep": keep}, "keep"))
    check("schedule: an unknown frequency refused", refused({"schedule": {"every": "hourly"}}, "schedule"))
    check("schedule: a bad time refused", refused({"schedule": {"every": "daily", "at": "25:00"}}, "schedule"))
    check("schedule: a bad day refused", refused({"schedule": {"every": "weekly", "at": "03:00", "day": "xyz"}}, "schedule"))
    s = backups.save_settings({"destination": str(paths.BACKUPS_DIR / "nightly"), "keep": 3,
                               "schedule": {"every": "weekly", "at": "4:05", "day": "Saturday"}}, by=ADMIN.id)
    check("destination: the dedicated <data dir>/backups folder allowed (created); values normalised",
          s["destination"] == str((paths.BACKUPS_DIR / "nightly").resolve()) and (paths.BACKUPS_DIR / "nightly").is_dir()
          and s["keep"] == 3 and s["schedule"] == {"every": "weekly", "at": "04:05", "day": "sat"}, s)
    s = backups.save_settings({"destination": "", "schedule": {"every": "off"}}, by=ADMIN.id)
    check("destination: empty → the default (<data dir>/backups without a /backups mount)",
          s["destination"] == str(backups.default_destination()) == str(paths.BACKUPS_DIR), s)
    mount = backups.MOUNT
    try:
        backups.MOUNT = OUTSIDE  # a plain folder, like the image's own empty /backups
        check("default: a /backups folder that is not a mount is not used", backups.default_destination() == paths.BACKUPS_DIR)
        backups.MOUNT = Path("/proc")  # a mount point
        check("default: a volume mounted at /backups is", backups.default_destination() == Path("/proc"))
    finally:
        backups.MOUNT = mount
    set_destination(OUTSIDE, keep=7)


# --- API --------------------------------------------------------------------------------------------------

def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    backups_api.setup(app)
    return app


async def test_api() -> None:
    async with TestClient(TestServer(build_app())) as c:
        for method, path in (("GET", "/api/backups"), ("PUT", "/api/backups"), ("POST", "/api/backups/run"),
                             ("GET", "/api/backups/download"), ("GET", f"/api/backups/files/{backups.list_backups()[0].name}")):
            r = await c.request(method, path, headers=AUTH[MAYA.id], json={"keep": 3} if method == "PUT" else None)
            body = await r.json()
            check(f"member: {method} {path.split('/files/')[0]} → 403 manage_server",
                  r.status == 403 and body == {"error": "forbidden", "needs": "manage_server"}, (r.status, body))
        check("member: changed nothing", backups.settings()["keep"] == 7)

        r = await c.get("/api/backups", headers=AUTH[ADMIN.id])
        body = await r.json()
        check("GET: settings, state, the list and the key's source",
              r.status == 200 and body["destination"] == str(OUTSIDE) and body["keep"] == 7
              and body["schedule"]["every"] == "off" and body["next_at"] is None and body["running"] is False
              and body["last"]["ok"] and body["credentials_key"] == "file" and body["timezone"]
              and body["backups"] and all(set(b) == {"name", "size", "created_at"} for b in body["backups"]), body)
        r = await c.put("/api/backups", headers=AUTH[ADMIN.id], json={"destination": "relative/dir"})
        body = await r.json()
        check("PUT: a bad destination → 400 invalid with the field", r.status == 400 and body["error"] == "invalid"
              and body["field"] == "destination", body)
        r = await c.put("/api/backups", headers=AUTH[ADMIN.id],
                        json={"schedule": {"every": "daily", "at": "02:45"}, "keep": 5})
        body = await r.json()
        check("PUT: stored, the next run given", r.status == 200 and body["keep"] == 5
              and body["schedule"]["at"] == "02:45" and body["next_at"], body)
        await c.put("/api/backups", headers=AUTH[ADMIN.id], json={"schedule": {"every": "off"}, "keep": 7})

        count = len(backups.list_backups())
        r = await c.post("/api/backups/run", headers=AUTH[ADMIN.id])
        body = await r.json()
        check("back up now: answered when written, in the list", r.status == 200
              and body["backup"]["name"] in {b["name"] for b in body["status"]["backups"]}
              and len(body["status"]["backups"]) == min(count + 1, 7), body)

        backups._lock.acquire()
        try:
            r1 = await c.post("/api/backups/run", headers=AUTH[ADMIN.id])
            r2 = await c.get("/api/backups/download", headers=AUTH[ADMIN.id])
            check("busy: back up now and download → 409 while one runs", r1.status == 409 and r2.status == 409
                  and (await r1.json())["error"] == "busy")
        finally:
            backups._lock.release()

        r = await c.get("/api/backups/download", headers=AUTH[ADMIN.id])
        data = await r.read()
        check("download: a fresh archive as an attachment", r.status == 200
              and r.headers["Content-Type"] == "application/gzip"
              and 'attachment; filename="dana-backup-' in r.headers["Content-Disposition"]
              and int(r.headers["Content-Length"]) == len(data), dict(r.headers))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = tar.getnames()
        check("download: holds the database, the key and the manifest",
              {"db.sqlite3", "credentials.key", "manifest.json"} <= set(names), names)
        work = paths.BACKUP_WORK_DIR
        check("download: nothing left under the data directory once sent", not work.exists() or not any(work.iterdir()),
              list(work.iterdir()) if work.exists() else [])
        check("download: not recorded as the destination's backup and not in its list",
              backups.state()["last"]["name"] == body["backup"]["name"]
              and len(backups.list_backups()) == len(body["status"]["backups"]))

        name = body["backup"]["name"]
        r = await c.get(f"/api/backups/files/{name}", headers=AUTH[ADMIN.id])
        got = await r.read()
        check("a listed archive by name: its bytes", r.status == 200 and got == (OUTSIDE / name).read_bytes())
        (OUTSIDE / "secret.txt").write_text("not a backup")
        for bad in ("..%2F..%2Fetc%2Fpasswd", "..%2Fdb.sqlite3", "secret.txt", "dana-backup-20990101-000000.tar.gz",
                    "dana-backup-1.tar.gz%2F..%2Fsecret.txt"):
            r = await c.get(f"/api/backups/files/{bad}", headers=AUTH[ADMIN.id])
            check(f"by name: {bad!r} → 404", r.status == 404, r.status)
        r = await c.get("/api/backups/files/../../credentials.key", headers=AUTH[ADMIN.id])
        check("by name: a path with .. never reaches a file", r.status == 404 and b"Fernet" not in await r.read())
        (OUTSIDE / "secret.txt").unlink()


# --- Self-check -------------------------------------------------------------------------------------------

def test_selfcheck() -> None:
    c = {x["key"]: x for x in checks.for_user(ADMIN)}
    check("self-check: green after a good backup", c["backups"]["ok"], c.get("backups"))
    check("self-check: a member has no backups check", "backups" not in {x["key"] for x in checks.for_user(MAYA)})
    gone = OUTSIDE.parent / f"{OUTSIDE.name}-gone"
    gone.mkdir()
    set_destination(gone)
    shutil.rmtree(gone)
    gone.write_text("now a file")  # the folder became a file: the next backup cannot write there
    try:
        backups.back_up("schedule")
        check("failing destination: back_up raises", False)
    except backups.Invalid as e:
        check("failing destination: back_up raises with the reason", "is a file" in e.detail, e.detail)
    c = {x["key"]: x for x in checks.compute(user=ADMIN, keys=("backups",))}
    check("self-check: red with the reason and the last good backup",
          not c["backups"]["ok"] and c["backups"]["title"] == "The last backup failed"
          and "is a file" in c["backups"]["why"]
          and any("Last good backup" in i for i in c["backups"]["blocks"][0]["items"]), c["backups"])
    check("self-check: its fix opens Settings, where Backups lives",
          c["backups"]["fix"] == {"kind": "screen", "label": "Open Backups", "screen": "settings"}, c["backups"]["fix"])
    gone.unlink()
    set_destination(OUTSIDE)
    backups.back_up("manual")
    c = {x["key"]: x for x in checks.compute(user=ADMIN, keys=("backups",))}
    check("self-check: green again after the next good backup", c["backups"]["ok"], c["backups"])
    stored = server_settings.get(backups.SETTINGS_KEY)
    now = backups.clock.now()
    stored.update(schedule={"every": "daily", "at": "03:30", "day": "sun"},
                  set_at=(now - datetime.timedelta(days=3)).isoformat())
    server_settings.put(backups.SETTINGS_KEY, stored)
    server_settings.put(backups.STATE_KEY, {**server_settings.get(backups.STATE_KEY), "last_scheduled_at": None})
    c = {x["key"]: x for x in checks.compute(user=ADMIN, keys=("backups",))}
    check("self-check: a schedule hours past due with no start is overdue", not c["backups"]["ok"]
          and c["backups"]["detail"] == "overdue", c["backups"])
    backups.save_settings({"schedule": {"every": "off"}}, by=ADMIN.id)


# --- Restore ----------------------------------------------------------------------------------------------

def cli_run(*argv: str) -> Tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(list(argv), stdout=out, stderr=err, stdin=io.StringIO(""))
    return rc, out.getvalue() + err.getvalue()


def test_restore() -> None:
    write("recordings/keep.m4a", b"original")
    rc, out = cli_run("backups", "create")
    check("cli create: written to the destination", rc == 0 and "Backup written" in out, out)
    archive = OUTSIDE / backups.list_backups()[0].name
    key_before = paths.CREDENTIALS_KEY_FILE.read_text()

    users.create("Gal")  # the change the restore must undo
    (paths.DATA_DIR / "recordings" / "keep.m4a").write_bytes(b"changed")
    write("recordings/new-after.m4a", b"new")
    paths.CREDENTIALS_KEY_FILE.write_text("not-the-key")
    check("changed: Gal exists before the restore", "Gal" in user_names())

    rc, out = cli_run("backups", "restore", str(archive))
    check("cli restore without --yes: a dry run, nothing changed", rc == 0 and "Nothing changed" in out
          and "Gal" in user_names(), out)
    rc, out = cli_run("backups", "restore", str(archive), "--yes")
    check("cli restore: done, says where the old database is kept", rc == 0 and "db-before-restore-" in out, out)
    check("restore: the change is gone (Gal), the users as backed up", user_names() == sorted([ADMIN.name, "Maya"]),
          user_names())
    check("restore: files back as they were, newer ones gone",
          (paths.DATA_DIR / "recordings" / "keep.m4a").read_bytes() == b"original"
          and not (paths.DATA_DIR / "recordings" / "new-after.m4a").exists())
    check("restore: the key file back", paths.CREDENTIALS_KEY_FILE.read_text() == key_before
          and paths.CREDENTIALS_KEY_FILE.stat().st_mode & 0o077 == 0)
    snaps = sorted(paths.SNAPSHOTS_DIR.glob("db-before-restore-*.sqlite3"))
    check("restore: the database before it kept as a snapshot (with Gal)", snaps and "Gal" in user_names(snaps[-1]),
          snaps)
    check("restore: snapshots/ and app-updates/ untouched",
          (paths.SNAPSHOTS_DIR / "db-before-core-0002.sqlite3").exists()
          and (paths.APP_UPDATES_DIR / "phone-1.0.apk").exists())
    check("restore: no staging left", not any(paths.BACKUP_WORK_DIR.iterdir()) if paths.BACKUP_WORK_DIR.exists() else True)
    check("restore: the restored database is migrated (the CLI accepts it)", not db.pending_migrations())

    evil = OUTSIDE / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        for name, data in (("manifest.json", json.dumps({"format": 1}).encode()), ("../escape.txt", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    rc, out = cli_run("backups", "restore", str(evil), "--yes")
    check("restore: an archive with a path outside refused, nothing written",
          rc == 1 and "Unsafe path" in out and not (paths.DATA_DIR.parent / "escape.txt").exists(), out)
    not_ours = OUTSIDE / "other.tar.gz"
    with tarfile.open(not_ours, "w:gz") as tar:
        info = tarfile.TarInfo("readme.txt")
        tar.addfile(info, io.BytesIO(b""))
    rc, out = cli_run("backups", "restore", str(not_ours), "--yes")
    check("restore: an archive that is not a Dana backup refused", rc == 1 and "Dana backup" in out, out)
    evil.unlink()
    not_ours.unlink()


async def main() -> None:
    test_archive()
    test_schedule()
    await test_loop()
    test_prune()
    test_settings()
    await test_api()
    test_selfcheck()
    test_restore()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
