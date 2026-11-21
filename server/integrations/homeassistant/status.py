"""What the entity watcher records per user for the self-checks (table
``system_marks``, keys carrying the user id; core/checks.py reads them):

- ``ha_watcher:<user>``: whether the watcher is connected (``ok``), since
  ``last_at`` (written when that changes), the last error in ``detail``;
- ``ha_entity:<user>:<role>``: whether the entity selected for ``role``
  (``person`` / ``car``) exists in their Home Assistant (``ok``), its id in
  ``detail``;
- ``ha_event:<user>:<role>``: when that entity last changed (``last_at``).
"""

import datetime
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from core.db import get_db_connection

logger = logging.getLogger(__name__)

ROLES = ("person", "car")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _put(key: str, ok: bool, detail: str = "") -> None:
    try:
        now = _now_iso()
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (key, now, 1 if ok else 0, now if ok else None, None if ok else now, (detail or "")[:300]))
            conn.commit()
    except Exception as e:
        logger.debug(f"HA watcher status {key} not written: {e}")


def set_connected(user_id: str, connected: bool, detail: str = "") -> None:
    _put(f"ha_watcher:{user_id}", connected, detail)


def set_entity(user_id: str, role: str, entity_id: str, exists: bool) -> None:
    _put(f"ha_entity:{user_id}:{role}", exists, entity_id)


def mark_event(user_id: str, role: str, entity_id: str) -> None:
    _put(f"ha_event:{user_id}:{role}", True, entity_id)


def forget(user_id: str) -> None:
    """Drop every mark of ``user_id``'s watcher (Home Assistant disconnected)."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM system_marks WHERE key = ? OR key LIKE ? OR key LIKE ?",
                     (f"ha_watcher:{user_id}", f"ha_entity:{user_id}:%", f"ha_event:{user_id}:%"))
        conn.commit()


@dataclass(frozen=True)
class Entity:
    entity_id: str
    exists: bool
    checked_at: str


@dataclass(frozen=True)
class WatchStatus:
    """``connected``: None when the watcher never reported; ``since``: when it
    became what it is (UTC ISO)."""
    connected: Optional[bool] = None
    since: Optional[str] = None
    detail: str = ""
    entities: Dict[str, Entity] = field(default_factory=dict)
    last_event: Dict[str, str] = field(default_factory=dict)


def read(user_id: str) -> WatchStatus:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM system_marks WHERE key = ? OR key LIKE ? OR key LIKE ?",
                            (f"ha_watcher:{user_id}", f"ha_entity:{user_id}:%", f"ha_event:{user_id}:%")).fetchall()
    connected, since, detail = None, None, ""
    entities: Dict[str, Entity] = {}
    events: Dict[str, str] = {}
    for r in rows:
        key = r["key"]
        if key == f"ha_watcher:{user_id}":
            connected, since, detail = bool(r["ok"]), r["last_at"], r["detail"] or ""
        elif key.startswith("ha_entity:"):
            entities[key.rsplit(":", 1)[1]] = Entity(r["detail"] or "", bool(r["ok"]), r["last_at"])
        else:
            events[key.rsplit(":", 1)[1]] = r["last_at"]
    return WatchStatus(connected, since, detail, entities, events)
