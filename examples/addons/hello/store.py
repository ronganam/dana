"""The ``hello`` addon's rows: people a user still has to greet."""

import datetime
from typing import Any, Dict, List

from core.db import get_db_connection
from core.users import User


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def add(user: User, name: str) -> int:
    """Remember to greet ``name``; returns the row id."""
    with get_db_connection() as conn:
        cur = conn.execute("INSERT INTO hello_greetings (user_id, name, created_at) VALUES (?, ?, ?)",
                           (user.id, name, _now()))
        conn.commit()
        return int(cur.lastrowid or 0)


def pending(user: User) -> List[Dict[str, Any]]:
    """The user's people still to greet, oldest first."""
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, created_at FROM hello_greetings WHERE user_id = ? AND status = 'pending' ORDER BY id",
            (user.id,))]


def mark_done(user: User, greeting_id: int) -> bool:
    """Mark one of the user's greetings done; False when it is not theirs or not pending."""
    with get_db_connection() as conn:
        cur = conn.execute("UPDATE hello_greetings SET status = 'done' WHERE id = ? AND user_id = ? AND status = 'pending'",
                           (greeting_id, user.id))
        conn.commit()
        return cur.rowcount == 1


def purge_done(user: User, older_than: datetime.timedelta) -> int:
    """Delete the user's done greetings older than ``older_than``; returns how many."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - older_than).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM hello_greetings WHERE user_id = ? AND status = 'done' AND created_at < ?",
                           (user.id, cutoff))
        conn.commit()
        return cur.rowcount
