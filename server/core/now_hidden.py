"""Hidden Now cards, per user: snoozed checks / review / suggestions, ignored
checks, reminders dismissed from Now (table ``now_hidden``, key ``(user_id,
item_id)``; docs/now.md). Used by the Now screen (core/now.py) and the
self-checks (core/checks.py)."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from core import clock
from core.db import get_db_connection
from core.users import User


def utc_iso(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc).isoformat(timespec="seconds")


def hide(user: User, item_id: str, until: datetime.datetime, reason: str = "snooze") -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO now_hidden (user_id, item_id, until, reason, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (user.id, item_id, utc_iso(until), reason, utc_iso()))
        conn.commit()


def unhide(user: User, item_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM now_hidden WHERE user_id = ? AND item_id = ?", (user.id, item_id))
        conn.commit()


def row(user: User, item_id: str) -> Optional[Dict[str, Any]]:
    """The stored row of ``user``'s hidden card (without ``user_id``), or None."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT item_id, until, reason, created_at FROM now_hidden WHERE user_id = ? AND item_id = ?",
                         (user.id, item_id)).fetchone()
    return dict(r) if r else None


def restore(user: User, item_id: str, before: Optional[Dict[str, Any]]) -> None:
    """Undo of hide / unhide: put ``user``'s row back as it was (or remove it)."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM now_hidden WHERE user_id = ? AND item_id = ?", (user.id, item_id))
        if before:
            conn.execute("INSERT INTO now_hidden (user_id, item_id, until, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                         (user.id, item_id, before["until"], before.get("reason"), before["created_at"]))
        conn.commit()


def active(user: User) -> Dict[str, Dict[str, Any]]:
    """item id → row of the cards ``user`` has hidden right now (expired rows dropped)."""
    now = utc_iso()
    with get_db_connection() as conn:
        conn.execute("DELETE FROM now_hidden WHERE until <= ?", (now,))
        conn.commit()
        return {r["item_id"]: dict(r) for r in conn.execute(
            "SELECT item_id, until, reason, created_at FROM now_hidden WHERE user_id = ?", (user.id,)).fetchall()}


def until(user: User, item_id: str) -> Optional[str]:
    """Local ISO time ``user``'s card is hidden until, or None when it shows."""
    r = row(user, item_id)
    if not r or r["until"] <= utc_iso():
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(r["until"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(clock.get_timezone()).isoformat(timespec="seconds")
