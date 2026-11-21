"""``agent_sessions``: the user's coding-agent sessions (Claude Code, Codex)
as their hooks last reported them, shown live on the phone
(integrations/phone/agent_sessions.py, docs/notifications.md "Coding-agent
sessions"). One row per ``(user_id, id)``; ``id`` is ``<agent>:<session id>``.
"""

import sqlite3

VERSION = 21
NAME = "agent_sessions"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE agent_sessions (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        id TEXT NOT NULL,
        agent TEXT NOT NULL,
        host TEXT NOT NULL DEFAULT '',
        project TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        activity TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        steps_done INTEGER NOT NULL DEFAULT 0,
        steps_total INTEGER NOT NULL DEFAULT 0,
        tools INTEGER NOT NULL DEFAULT 0,
        rev INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, id)
    )""")
