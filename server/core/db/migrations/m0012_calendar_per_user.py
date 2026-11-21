"""The calendar choice belongs to the user's calendar integration
(docs/access.md "Kinds", docs/configuration.md).

- The calendars shown and searched were one ``memories`` row (key
  ``selected_calendars``, category ``preferences``, a JSON list of ids), not
  per user. The value becomes the setting ``selected_calendars`` of the first
  admin's ``calendar`` integration (who owns the old memories) and the row
  leaves ``memories``:
  - when that integration exists, it is written into its settings;
  - when it does not exist yet (the startup env import creates it after the
    migrations, for the first admin, who may not exist yet either), it waits
    in ``integration_import_settings`` and the import
    (``integrations/bootstrap.py``) adds it to the settings it connects with;
  - when the kind was imported before and the admin has since disconnected
    it, there is nothing to give it to and the value is dropped.
  A value that is not a JSON list of ids is dropped (the code ignored it).
- ``integration_import_settings``: settings of the first admin waiting for the
  env import of their kind (one row per kind and setting, the value as JSON);
  the import removes a kind's rows when it records the kind as imported.
- The calendar self-check's mark (``system_marks['calendar']``) becomes the
  first admin's (``calendar:<user id>``): the mark is per user now.
"""

import json
import sqlite3
from typing import List, Optional

from core import access, user_rows

VERSION = 12
NAME = "calendar_per_user"

KIND = "calendar"
SETTING = "selected_calendars"
MEMORY_ROW = "key = 'selected_calendars' AND category = 'preferences'"

STATEMENTS = [
    """CREATE TABLE integration_import_settings (
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (kind, name)
    )""",
]


def _selection(raw: Optional[str]) -> Optional[List[str]]:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None
    return value


def _first_admin(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT id FROM users WHERE access = ? ORDER BY created_at, rowid LIMIT 1",
                       (access.ADMIN,)).fetchone()
    return row[0] if row else None


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
    owner = _first_admin(conn)
    row = conn.execute(f"SELECT value FROM memories WHERE {MEMORY_ROW} ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is not None:
        conn.execute(f"DELETE FROM memories WHERE {MEMORY_ROW}")
        selection = _selection(row[0])
        integration = conn.execute("SELECT settings FROM integrations WHERE user_id = ? AND kind = ?",
                                   (owner, KIND)).fetchone() if owner is not None else None
        if selection is not None and integration is not None:
            settings = json.loads(integration[0] or "{}")
            settings[SETTING] = selection
            conn.execute("UPDATE integrations SET settings = ?, updated_at = ? WHERE user_id = ? AND kind = ?",
                         (json.dumps(settings, ensure_ascii=False), user_rows.now_iso(), owner, KIND))
        elif selection is not None and not conn.execute("SELECT 1 FROM integration_imports WHERE kind = ?",
                                                        (KIND,)).fetchone():
            conn.execute("INSERT INTO integration_import_settings (kind, name, value) VALUES (?, ?, ?)",
                         (KIND, SETTING, json.dumps(selection, ensure_ascii=False)))
    if owner is not None:
        conn.execute("UPDATE system_marks SET key = ? WHERE key = 'calendar'", (f"calendar:{owner}",))
    else:
        conn.execute("DELETE FROM system_marks WHERE key = 'calendar'")
