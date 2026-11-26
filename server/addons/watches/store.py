"""The watches and their fires in the database, always for one user (another
user's id answers like a missing one). Tables from ``migrations/m0001_watches.py``."""

import datetime as dt
import json
import uuid
from typing import Any, Callable, Dict, List, Optional

from core import clock
from core.db import get_db_connection
from core.users import User

ACTIVE, PAUSED, DONE, EXPIRED = "active", "paused", "done", "expired"
STATUSES = (ACTIVE, PAUSED, DONE, EXPIRED)
FIRE_DAYS = 90
COLUMNS = ("id, user_id, title, kind, status, request, spec, task_id, created_at, updated_at, next_time, "
           "fires_count, last_fired_at, closed_at")

# Called with the user after any change to their watches (the engine's cache).
_listeners: List[Callable[[User], None]] = []


def on_change(fn: Callable[[User], None]) -> None:
    """Call ``fn(user)`` after every change to that user's watches."""
    _listeners.append(fn)


def _changed(user: User) -> None:
    for fn in _listeners:
        fn(user)


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def _row(r: Any) -> Dict[str, Any]:
    d = dict(r)
    d["spec"] = json.loads(d.get("spec") or "{}")
    return d


def create(user: User, *, title: str, kind: str, request: str, spec: Dict[str, Any], task_id: Optional[str],
           next_time: Optional[str]) -> Dict[str, Any]:
    """A new active watch of ``user``; returns its row."""
    wid = uuid.uuid4().hex[:12]
    now = _now()
    with get_db_connection() as conn:
        conn.execute("INSERT INTO watches (id, user_id, title, kind, status, request, spec, task_id, created_at, "
                     "updated_at, next_time) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                     (wid, user.id, title, kind, request, json.dumps(spec, ensure_ascii=False), task_id, now, now,
                      next_time))
        conn.commit()
    _changed(user)
    return get(user, wid) or {}


def get(user: User, watch_id: str) -> Optional[Dict[str, Any]]:
    """``user``'s watch, or None (also for someone else's id)."""
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT {COLUMNS} FROM watches WHERE id = ? AND user_id = ?", (watch_id, user.id)).fetchone()
    return _row(r) if r else None


def list_for(user: User, statuses: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """``user``'s watches, newest first (only these statuses when given)."""
    sql = f"SELECT {COLUMNS} FROM watches WHERE user_id = ?"
    args: List[Any] = [user.id]
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    with get_db_connection() as conn:
        rows = conn.execute(sql + " ORDER BY created_at DESC, rowid DESC", args).fetchall()
    return [_row(r) for r in rows]


def update(user: User, watch_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Change some columns of ``user``'s watch (``spec`` as a dict); returns the row after, None when not theirs."""
    allowed = {"title", "kind", "status", "request", "spec", "task_id", "next_time", "closed_at"}
    sets = {k: (json.dumps(v, ensure_ascii=False) if k == "spec" else v) for k, v in fields.items() if k in allowed}
    if not sets:
        return get(user, watch_id)
    sets["updated_at"] = _now()
    with get_db_connection() as conn:
        cur = conn.execute(f"UPDATE watches SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ? AND user_id = ?",
                           (*sets.values(), watch_id, user.id))
        conn.commit()
    if not cur.rowcount:
        return None
    _changed(user)
    return get(user, watch_id)


def close(user: User, watch_id: str, status: str) -> Optional[Dict[str, Any]]:
    """End ``user``'s watch as ``done`` or ``expired`` (nothing more fires)."""
    return update(user, watch_id, status=status, closed_at=_now(), next_time=None)


def delete(user: User, watch_id: str) -> Optional[Dict[str, Any]]:
    """Delete ``user``'s watch and its fires; returns what was deleted (for undo), None when not theirs."""
    row = get(user, watch_id)
    if row is None:
        return None
    with get_db_connection() as conn:
        fires = [dict(r) for r in conn.execute("SELECT id, watch_id, user_id, at, trigger_id, event_type, event, "
                                                "actions FROM watch_fires WHERE watch_id = ? AND user_id = ?",
                                                (watch_id, user.id)).fetchall()]
        conn.execute("DELETE FROM watch_fires WHERE watch_id = ? AND user_id = ?", (watch_id, user.id))
        conn.execute("DELETE FROM watches WHERE id = ? AND user_id = ?", (watch_id, user.id))
        conn.commit()
    row["_fires"] = fires
    _changed(user)
    return row


def restore(user: User, row: Dict[str, Any]) -> None:
    """Put back a watch :func:`delete` returned (undo), with its fires."""
    with get_db_connection() as conn:
        # scoped: the row is the user's own deleted watch, written back with their user id
        conn.execute(f"INSERT OR REPLACE INTO watches ({COLUMNS}) VALUES ({','.join('?' * 14)})",
                     (row["id"], user.id, row["title"], row["kind"], row["status"], row["request"],
                      json.dumps(row["spec"], ensure_ascii=False), row["task_id"], row["created_at"],
                      row["updated_at"], row["next_time"], row["fires_count"], row["last_fired_at"],
                      row["closed_at"]))
        for f in row.get("_fires") or []:
            conn.execute("INSERT OR REPLACE INTO watch_fires (id, watch_id, user_id, at, trigger_id, event_type, "
                         "event, actions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (f["id"], f["watch_id"], user.id, f["at"], f["trigger_id"], f["event_type"], f["event"],
                          f["actions"]))
        conn.commit()
    _changed(user)


def by_task(user: User, task_id: str) -> List[Dict[str, Any]]:
    """``user``'s open watches about task ``task_id`` (reminders)."""
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT {COLUMNS} FROM watches WHERE user_id = ? AND task_id = ? "
                            "AND status IN ('active', 'paused')", (user.id, task_id)).fetchall()
    return [_row(r) for r in rows]


# --- Fires ----------------------------------------------------------------------------


def _fire(r: Any) -> Dict[str, Any]:
    d = dict(r)
    d["event"] = json.loads(d.get("event") or "{}")
    d["actions"] = json.loads(d.get("actions") or "[]")
    return d


def add_fire(user: User, watch_id: str, *, trigger_id: str, event_type: str, event: Dict[str, Any],
             actions: Optional[List[Dict[str, Any]]] = None, at: Optional[dt.datetime] = None,
             count: bool = True) -> str:
    """Record that ``user``'s watch fired (or noticed evidence: ``count`` False); returns the fire id."""
    fid = uuid.uuid4().hex[:12]
    when = (at or clock.now()).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO watch_fires (id, watch_id, user_id, at, trigger_id, event_type, event, actions) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (fid, watch_id, user.id, when, trigger_id, event_type,
                      json.dumps(event, ensure_ascii=False, default=str), json.dumps(actions or [], ensure_ascii=False)))
        if count:
            conn.execute("UPDATE watches SET fires_count = fires_count + 1, last_fired_at = ?, updated_at = ? "
                         "WHERE id = ? AND user_id = ?", (when, _now(), watch_id, user.id))
        conn.commit()
    if count:
        _changed(user)
    return fid


def set_fire_actions(user: User, fire_id: str, actions: List[Dict[str, Any]]) -> None:
    """Store what the actions of ``user``'s fire did."""
    with get_db_connection() as conn:
        conn.execute("UPDATE watch_fires SET actions = ? WHERE id = ? AND user_id = ?",
                     (json.dumps(actions, ensure_ascii=False), fire_id, user.id))
        conn.commit()


def fires(user: User, watch_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The fires of ``user``'s watch, newest first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id, at, trigger_id, event_type, event, actions FROM watch_fires "
                            "WHERE watch_id = ? AND user_id = ? ORDER BY at DESC, rowid DESC LIMIT ?",
                            (watch_id, user.id, limit)).fetchall()
    return [_fire(r) for r in rows]


def get_fire(user: User, fire_id: str) -> Optional[Dict[str, Any]]:
    """One fire of ``user``'s, with its watch id, or None."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT id, watch_id, at, trigger_id, event_type, event, actions FROM watch_fires "
                         "WHERE id = ? AND user_id = ?", (fire_id, user.id)).fetchone()
    return _fire(r) if r else None


def prune(user: User, now: dt.datetime) -> int:
    """Delete ``user``'s fires older than :data:`FIRE_DAYS`; returns how many."""
    cutoff = (now - dt.timedelta(days=FIRE_DAYS)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM watch_fires WHERE user_id = ? AND at < ?", (user.id, cutoff))
        conn.commit()
    return cur.rowcount
