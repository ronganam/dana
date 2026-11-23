"""Backups of the whole server: one archive file with everything Dana keeps
(docs/backups.md).

An archive ``dana-backup-YYYYMMDD-HHMMSS.tar.gz`` (the server's local time)
holds:

- ``db.sqlite3``: a consistent copy of the database (SQLite's online backup
  API, never a raw copy of the live file);
- ``credentials.key``: the generated credentials key, when the key lives in
  that file (core/addons/crypto.py); a key from ``CREDENTIALS_KEY`` is not in
  the archive and the manifest says so;
- ``files/…``: everything else in the data directory (recordings, media,
  uploads, knowledge files, installed addons, money's folder…), except
  ``snapshots/`` (copies of the database), ``app-updates/`` (offered APKs,
  downloaded again from releases or uploaded again), ``backups/`` and
  ``.backup-work/`` (backups themselves), the live database's files, symbolic
  links and half-written ``*.tmp`` / ``*.partial`` files;
- ``manifest.json``: the format, Dana's version, each owner's schema version,
  when, the server's timezone, where the key comes from, what is inside.

Settings (``server_settings['backups']``, chosen in Settings → Backups):
``schedule`` {every: off | daily | weekly, at: "HH:MM", day: mon…sun} in the
server's timezone (core/clock.py), ``destination`` (an absolute folder on the
server; empty: :func:`default_destination`) and ``keep`` (the newest N
archives in the destination stay, older ones are removed). The last attempt
and the last success live in ``server_settings['backups.state']``; a failed
one is the ``backups`` self-check (core/checks.py).

One backup runs at a time (:class:`Busy` otherwise), in a worker thread when
called from the event loop; an archive is written under a hidden temporary
name in the destination and renamed when complete. :func:`restore` puts an
archive back (``bin/dana backups restore``, with the server stopped).
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import clock, db, db_snapshot, paths, server_settings
from core.addons import crypto
from core.pages_api import server_version

logger = logging.getLogger(__name__)

FORMAT = 1
SETTINGS_KEY = "backups"
STATE_KEY = "backups.state"
EVERY = ("off", "daily", "weekly")
DEFAULT_SCHEDULE: Dict[str, str] = {"every": "off", "at": "03:30", "day": "sun"}
DEFAULT_KEEP = 7
MAX_KEEP = 100
# A folder mounted here (compose.yaml's `backups` volume) is the default destination.
MOUNT = Path("/backups")
NAME_RE = re.compile(r"^dana-backup-(\d{8}-\d{6})(?:-(\d+))?\.tar\.gz$")
_WORK_PREFIX = ".dana-backup-"
_SKIP_SUFFIXES = (".tmp", ".partial")
# A schedule this late with no attempt is the self-check's "overdue".
OVERDUE = datetime.timedelta(hours=2)
LOOP_SECONDS = 60

_lock = threading.Lock()


class Busy(RuntimeError):
    """Another backup is being made."""


class Invalid(ValueError):
    """A setting that cannot be stored: ``field`` and what is wrong in words."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail


class RestoreError(RuntimeError):
    """The archive cannot be restored (not a Dana backup, unsafe paths, a broken database)."""


@dataclass
class Backup:
    """One archive in a folder."""
    name: str
    size: int
    created_at: str

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "size": self.size, "created_at": self.created_at}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def default_destination() -> Path:
    """``/backups`` when a volume is mounted there, else ``<data dir>/backups`` (the image has an
    empty ``/backups`` folder for the volume's owner: without a mount it would be lost on restart)."""
    return MOUNT if os.path.ismount(MOUNT) else paths.BACKUPS_DIR


def settings() -> Dict[str, Any]:
    """The stored settings with defaults: {schedule, destination (resolved), keep, set_at}."""
    raw = server_settings.get(SETTINGS_KEY) or {}
    schedule = dict(DEFAULT_SCHEDULE)
    if isinstance(raw.get("schedule"), dict):
        schedule.update({k: v for k, v in raw["schedule"].items() if k in DEFAULT_SCHEDULE})
    keep = raw.get("keep")
    return {"schedule": schedule,
            "destination": str(raw.get("destination") or default_destination()),
            "keep": keep if isinstance(keep, int) and 1 <= keep <= MAX_KEEP else DEFAULT_KEEP,
            "set_at": raw.get("set_at")}


def check_schedule(value: Any) -> Dict[str, str]:
    """A schedule from a request, normalised.

    Raises:
        Invalid: not {every, at "HH:MM", day} with known values.
    """
    if not isinstance(value, dict):
        raise Invalid("schedule", "The schedule is {every, at, day}.")
    every = value.get("every", "off")
    if every not in EVERY:
        raise Invalid("schedule", "Choose off, daily or weekly.")
    at = str(value.get("at") or DEFAULT_SCHEDULE["at"]).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", at)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise Invalid("schedule", "The time is HH:MM, like 03:30.")
    day = str(value.get("day") or DEFAULT_SCHEDULE["day"]).strip().lower()[:3]
    if day not in clock.DAYS:
        raise Invalid("schedule", "The day is one of mon, tue, wed, thu, fri, sat, sun.")
    return {"every": every, "at": f"{int(m.group(1)):02d}:{m.group(2)}", "day": day}


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def check_destination(raw: Any) -> Path:
    """The folder a destination names, created when missing and tried for writing.

    Allowed: an absolute folder outside the data directory, or the dedicated
    ``<data dir>/backups`` (or a folder in it). Never the data directory itself
    or any other folder inside it (a backup would hold the backups before it).

    Raises:
        Invalid: not absolute, inside the data directory, a file, cannot be created or not writable.
    """
    text = str(raw or "").strip()
    if not text:
        return default_destination()
    if not os.path.isabs(text):
        raise Invalid("destination", "Give an absolute folder on the server, like /backups.")
    path = Path(text).resolve()
    data = paths.DATA_DIR.resolve()
    if _inside(path, data) and not _inside(path, paths.BACKUPS_DIR.resolve()):
        raise Invalid("destination", f"That folder is inside the data directory ({data}). Use {paths.BACKUPS_DIR} "
                                     "or a folder outside it, best on another disk.")
    if path.exists() and not path.is_dir():
        raise Invalid("destination", f"{path} is a file, not a folder.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Invalid("destination", f"{path} cannot be created: {e.strerror or e}.")
    probe = path / f"{_WORK_PREFIX}write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        raise Invalid("destination", f"The server (user id {os.getuid()}) cannot write to {path}.")
    return path


def save_settings(body: Dict[str, Any], *, by: Optional[str] = None) -> Dict[str, Any]:
    """Store the fields of ``body`` that are given (schedule, destination, keep); returns :func:`settings`.

    Raises:
        Invalid: a field that cannot be stored (nothing is stored then).
    """
    stored = dict(server_settings.get(SETTINGS_KEY) or {})
    if "schedule" in body:
        schedule = check_schedule(body["schedule"])
        if schedule != stored.get("schedule"):
            stored["schedule"] = schedule
            stored["set_at"] = clock.now().isoformat(timespec="seconds")
    if "destination" in body:
        text = str(body["destination"] or "").strip()
        check_destination(text)
        stored["destination"] = str(Path(text).resolve()) if text else ""
    if "keep" in body:
        keep = body["keep"]
        if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= MAX_KEEP:
            raise Invalid("keep", f"Keep between 1 and {MAX_KEEP} backups.")
        stored["keep"] = keep
    server_settings.put(SETTINGS_KEY, stored, by=by)
    return settings()


def state() -> Dict[str, Any]:
    """{last, last_ok, last_scheduled_at}: the last attempt (ok or not), the last success, the last scheduled start."""
    raw = server_settings.get(STATE_KEY) or {}
    return {"last": raw.get("last"), "last_ok": raw.get("last_ok"), "last_scheduled_at": raw.get("last_scheduled_at")}


def _put_state(**changes: Any) -> None:
    raw = dict(server_settings.get(STATE_KEY) or {})
    raw.update(changes)
    server_settings.put(STATE_KEY, raw)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def next_run(schedule: Dict[str, str], after: datetime.datetime,
             tz: Optional[datetime.tzinfo] = None) -> Optional[datetime.datetime]:
    """The first scheduled time strictly after ``after``, in ``tz`` (default: the server's); None when off."""
    every = schedule.get("every", "off")
    if every not in ("daily", "weekly"):
        return None
    tz = tz or clock.get_timezone()
    local = after.astimezone(tz)
    hour, minute = (int(x) for x in schedule.get("at", DEFAULT_SCHEDULE["at"]).split(":"))
    weekday = clock.DAYS.index(schedule.get("day", DEFAULT_SCHEDULE["day"]))
    for i in range(8):
        day = local.date() + datetime.timedelta(days=i)
        if every == "weekly" and day.weekday() != weekday:
            continue
        at = datetime.datetime.combine(day, datetime.time(hour, minute), tz)
        if at > local:
            return at
    return None


def _parse(value: Any) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def next_due(now: Optional[datetime.datetime] = None) -> Optional[datetime.datetime]:
    """When the schedule runs next: the first time after the last scheduled start (or the
    schedule's last change); a time in the past means it is due now. None when off."""
    s = settings()
    marks = [t for t in (_parse(state()["last_scheduled_at"]), _parse(s["set_at"])) if t]
    since = max(marks) if marks else (now or clock.now())
    return next_run(s["schedule"], since)


def is_due(now: Optional[datetime.datetime] = None) -> bool:
    """Whether the scheduled backup should start now."""
    now = now or clock.now()
    at = next_due(now)
    return at is not None and at <= now


# ---------------------------------------------------------------------------
# Making an archive
# ---------------------------------------------------------------------------

def running() -> bool:
    """Whether a backup is being made now."""
    return _lock.locked()


def _excluded_names() -> set:
    db_name = paths.DATABASE_PATH.name
    return {db_name, f"{db_name}-wal", f"{db_name}-shm", f"{db_name}-journal",
            paths.SNAPSHOTS_DIR.name, paths.APP_UPDATES_DIR.name, paths.BACKUPS_DIR.name,
            paths.BACKUP_WORK_DIR.name, paths.CREDENTIALS_KEY_FILE.name}


def _kept(name: str) -> bool:
    return not name.endswith(_SKIP_SUFFIXES) and not name.startswith(_WORK_PREFIX)


def data_files(avoid: Optional[Path] = None) -> Tuple[List[str], List[Tuple[str, Path]]]:
    """What a backup takes from the data directory besides the database and key: the
    top-level folders, and every regular file as (path relative to the data directory, path)."""
    data = paths.DATA_DIR
    excluded = _excluded_names()
    avoid = avoid.resolve() if avoid else None
    dirs: List[str] = []
    files: List[Tuple[str, Path]] = []
    if not data.is_dir():
        return dirs, files
    for entry in sorted(data.iterdir(), key=lambda p: p.name):
        if entry.name in excluded or entry.is_symlink() or not _kept(entry.name):
            continue
        if avoid and _inside(entry.resolve(), avoid):
            continue
        if entry.is_file():
            files.append((entry.name, entry))
        elif entry.is_dir():
            dirs.append(entry.name)
            for root, subdirs, names in os.walk(entry):
                subdirs[:] = sorted(d for d in subdirs if not os.path.islink(os.path.join(root, d)))
                for n in sorted(names):
                    p = Path(root, n)
                    if _kept(n) and not p.is_symlink() and p.is_file():
                        files.append((p.relative_to(data).as_posix(), p))
    return dirs, files


def _copy_database(target: Path) -> None:
    src = sqlite3.connect(f"file:{paths.DATABASE_PATH}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _schema(db_copy: Path) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for addon, version in db.applied_migrations(db_copy):
        out[addon] = max(out.get(addon, 0), version)
    return out


def _free_name(folder: Path, at: datetime.datetime) -> str:
    base = f"dana-backup-{at.strftime('%Y%m%d-%H%M%S')}"
    name, n = f"{base}.tar.gz", 1
    while (folder / name).exists():
        n += 1
        name = f"{base}-{n}.tar.gz"
    return name


def _clear_leftovers(folder: Path) -> None:
    """Remove what an interrupted backup left in ``folder`` (only while holding the lock)."""
    for p in folder.glob(f"{_WORK_PREFIX}*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _write_archive(folder: Path) -> Backup:
    """Write one archive into ``folder`` (complete, or nothing). Call with the lock held."""
    folder.mkdir(parents=True, exist_ok=True)
    _clear_leftovers(folder)
    now = clock.now()
    name = _free_name(folder, now)
    work = Path(tempfile.mkdtemp(prefix=_WORK_PREFIX, dir=folder))
    try:
        db_copy = work / "db.sqlite3"
        _copy_database(db_copy)
        source = crypto.key_source()
        key_file = paths.CREDENTIALS_KEY_FILE if source == "file" else None
        dirs, files = data_files(avoid=folder)
        partial = work / f"{name}.partial"
        added, size = 0, 0
        with tarfile.open(partial, "w:gz", compresslevel=6) as tar:
            tar.add(db_copy, arcname="db.sqlite3")
            if key_file is not None:
                tar.add(key_file, arcname="credentials.key")
            for d in dirs:
                tar.add(paths.DATA_DIR / d, arcname=f"files/{d}", recursive=False)
            for rel, path in files:
                try:
                    tar.add(path, arcname=f"files/{rel}", recursive=False)
                except FileNotFoundError:
                    continue  # removed while the backup ran
                added += 1
                size += path.stat().st_size if path.exists() else 0
            manifest = {
                "format": FORMAT,
                "dana_version": server_version(),
                "created_at": now.isoformat(timespec="seconds"),
                "timezone": str(clock.get_timezone()),
                "schema": _schema(db_copy),
                "database": "db.sqlite3",
                "credentials_key": "file" if key_file is not None else ("env" if source == "env" else "none"),
                "dirs": dirs,
                "files": added,
                "files_bytes": size,
                "left_out": sorted(_excluded_names() - {paths.DATABASE_PATH.name, paths.CREDENTIALS_KEY_FILE.name}),
            }
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
        os.chmod(partial, 0o600)  # it holds the credentials key and everyone's data
        final = folder / name
        os.replace(partial, final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    st = final.stat()
    return Backup(name, st.st_size, clock.from_timestamp(st.st_mtime).isoformat(timespec="seconds"))


def list_backups(folder: Optional[Path] = None) -> List[Backup]:
    """The archives in ``folder`` (default: the destination), newest first; other files are ignored."""
    folder = Path(folder or settings()["destination"])
    out = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []
    for p in entries:
        if NAME_RE.match(p.name) and p.is_file() and not p.is_symlink():
            st = p.stat()
            out.append(Backup(p.name, st.st_size, clock.from_timestamp(st.st_mtime).isoformat(timespec="seconds")))
    return sorted(out, key=_order, reverse=True)


def _order(b: Backup) -> Tuple[str, int]:
    """Oldest first by the time in the name, then by its ``-N`` (a second one in the same second)."""
    m = NAME_RE.match(b.name)
    return (m.group(1), int(m.group(2) or 1)) if m else ("", 0)


def prune(folder: Path, keep: int) -> List[str]:
    """Remove all but the newest ``keep`` archives in ``folder``; returns the removed names."""
    removed = []
    for b in list_backups(folder)[max(1, keep):]:
        try:
            (folder / b.name).unlink()
            removed.append(b.name)
        except OSError as e:
            logger.warning(f"Could not remove the old backup {b.name}: {e}")
    return removed


def find(name: str) -> Optional[Path]:
    """The archive called ``name`` in the destination; None for any other name (no paths)."""
    if not NAME_RE.match(name or ""):
        return None
    path = Path(settings()["destination"]) / name
    return path if path.is_file() and not path.is_symlink() else None


def back_up(trigger: str = "manual") -> Backup:
    """Make a backup in the destination, prune to ``keep``, record the result (blocking).

    Raises:
        Busy: another backup is running.
        Invalid / OSError / sqlite3.Error: it failed (recorded, the self-check shows it).
    """
    if not _lock.acquire(blocking=False):
        raise Busy("A backup is already being made.")
    started = time.monotonic()
    at = clock.now().isoformat(timespec="seconds")
    try:
        s = settings()
        folder = check_destination(s["destination"])
        backup = _write_archive(folder)
        removed = prune(folder, s["keep"])
    except Exception as e:
        reason = e.detail if isinstance(e, Invalid) else f"{type(e).__name__}: {e}"
        _put_state(last={"ok": False, "at": at, "trigger": trigger, "error": reason[:300]})
        logger.warning(f"Backup ({trigger}) failed: {reason}")
        raise
    finally:
        _lock.release()
    result = {"ok": True, "at": at, "trigger": trigger, **backup.to_json(),
              "seconds": round(time.monotonic() - started, 1), "removed": removed}
    _put_state(last=result, last_ok=result)
    logger.info(f"Backup ({trigger}) written: {backup.name} ({backup.size:,} bytes)")
    return backup


def build_download() -> Path:
    """Make a fresh archive under ``<data dir>/.backup-work`` for the browser (blocking);
    the caller deletes it once sent. Not recorded and not pruned.

    Raises:
        Busy: another backup is running.
    """
    if not _lock.acquire(blocking=False):
        raise Busy("A backup is already being made.")
    try:
        backup = _write_archive(paths.BACKUP_WORK_DIR)
    finally:
        _lock.release()
    return paths.BACKUP_WORK_DIR / backup.name


def clear_work_dir() -> None:
    """Remove downloads left from before a restart (at start)."""
    shutil.rmtree(paths.BACKUP_WORK_DIR, ignore_errors=True)


async def schedule_loop() -> None:
    """Background task: start the scheduled backup when it is due (checked every minute)."""
    await asyncio.to_thread(clear_work_dir)
    while True:
        try:
            now = clock.now()
            if is_due(now) and not running():
                _put_state(last_scheduled_at=now.isoformat(timespec="seconds"))
                try:
                    await asyncio.to_thread(back_up, "schedule")
                except Exception:
                    pass  # Busy (a manual one is running), or a failure back_up recorded and logged
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Backup schedule check failed: {e}", exc_info=True)
        await asyncio.sleep(LOOP_SECONDS)


def status() -> Dict[str, Any]:
    """Everything Settings → Backups shows."""
    s = settings()
    st = state()
    nxt = next_due()
    return {"schedule": s["schedule"], "destination": s["destination"],
            "default_destination": str(default_destination()), "keep": s["keep"],
            "next_at": nxt.isoformat(timespec="seconds") if nxt else None,
            "running": running(), "last": st["last"], "last_ok": st["last_ok"],
            "backups": [b.to_json() for b in list_backups(Path(s["destination"]))],
            "credentials_key": crypto.key_source() or "none",
            "timezone": str(clock.get_timezone())}


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def _safe_members(tar: tarfile.TarFile) -> List[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        parts = Path(m.name).parts
        if m.name.startswith("/") or ".." in parts or not parts:
            raise RestoreError(f"Unsafe path in the archive: {m.name!r}")
        if not (m.isreg() or m.isdir()):
            raise RestoreError(f"Not a plain file in the archive: {m.name!r}")
        if parts[0] not in ("manifest.json", "db.sqlite3", "credentials.key", "files"):
            raise RestoreError(f"Not part of a Dana backup: {m.name!r}")
    return members


def read_manifest(archive: Path) -> Dict[str, Any]:
    """The manifest of ``archive``.

    Raises:
        RestoreError: not a Dana backup this version can read.
    """
    try:
        with tarfile.open(archive, "r:gz") as tar:
            _safe_members(tar)
            f = tar.extractfile("manifest.json")
            manifest = json.loads(f.read()) if f else None
    except (OSError, tarfile.TarError, KeyError, ValueError) as e:
        raise RestoreError(f"{archive} is not a Dana backup ({e}).")
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RestoreError(f"{archive} is not a Dana backup of format {FORMAT}.")
    return manifest


def restore(archive: Path) -> Dict[str, Any]:
    """Put ``archive`` back into the data directory, with the server stopped: a
    ``db-before-restore-<time>.sqlite3`` snapshot of the current database first,
    then the database, the key file (when the archive has one) and each of its
    top-level folders and files (replacing what is there). Returns
    {manifest, snapshot, key: file | env | none, restored: [names]}.

    Raises:
        RestoreError: the archive is not a readable Dana backup (nothing is changed).
    """
    manifest = read_manifest(archive)
    data = paths.DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    paths.BACKUP_WORK_DIR.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="restore-", dir=paths.BACKUP_WORK_DIR))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(stage, members=_safe_members(tar), filter="data")
        staged_db = stage / "db.sqlite3"
        if not staged_db.is_file():
            raise RestoreError("The archive has no database.")
        try:
            conn = sqlite3.connect(staged_db)
            try:
                ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise RestoreError(f"The archive's database cannot be read: {e}")
        if ok != "ok":
            raise RestoreError(f"The archive's database is damaged: {ok}")

        snapshot = None
        if paths.DATABASE_PATH.exists():
            stamp = clock.now().strftime("%Y%m%d-%H%M%S")
            snapshot = db_snapshot.snapshot(paths.DATABASE_PATH, f"db-before-restore-{stamp}.sqlite3")
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{paths.DATABASE_PATH}{suffix}").unlink(missing_ok=True)
        os.replace(staged_db, paths.DATABASE_PATH)

        staged_key = stage / "credentials.key"
        if staged_key.is_file():
            os.chmod(staged_key, 0o600)
            os.replace(staged_key, paths.CREDENTIALS_KEY_FILE)

        restored = []
        files = stage / "files"
        if files.is_dir():
            excluded = _excluded_names()
            for entry in sorted(files.iterdir(), key=lambda p: p.name):
                if entry.name in excluded:
                    continue
                target = data / entry.name
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
                os.replace(entry, target)
                restored.append(entry.name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"manifest": manifest, "snapshot": str(snapshot) if snapshot else None,
            "key": manifest.get("credentials_key", "none"), "restored": restored}
