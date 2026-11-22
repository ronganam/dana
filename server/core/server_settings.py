"""Server-wide values chosen in the app rather than in env (table
``server_settings``, core migration ``m0007_server_settings``): the LLM
provider and its key (core/llm_provider.py) and the server's timezone
(core/clock.py). Each value is JSON; a secret one is encrypted with the
credentials key by its owner before it is stored here. Env stays the
override: whoever reads a value here reads its env variable first.
"""

import datetime
import json
import sqlite3
from typing import Any, Optional

from core.db import get_db_connection


def get(key: str, default: Any = None) -> Any:
    """The value stored under ``key`` (``default`` when none, and before the
    database is migrated: the clock may be asked that early)."""
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def put(key: str, value: Any, *, by: Optional[str] = None) -> None:
    """Store ``value`` (JSON-serialisable) under ``key``; ``by``: the user id who changed it."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO server_settings (key, value, changed_by, changed_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, changed_by = excluded.changed_by, "
                     "changed_at = excluded.changed_at", (key, json.dumps(value), by, now))
        conn.commit()


def delete(key: str) -> None:
    """Remove the value stored under ``key`` (nothing when there is none)."""
    with get_db_connection() as conn:
        conn.execute("DELETE FROM server_settings WHERE key = ?", (key,))
        conn.commit()
