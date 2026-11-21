"""``agent_sessions.thread`` / ``parent``: subagents shown inside their parent's
notification (integrations/phone/agent_sessions.py).

``thread`` is the T3 Code thread the session runs in (empty outside T3 Code);
``parent`` is empty for a top-level session, ``t3:<root thread>`` for a T3
Code delegated child, or the parent row's id for one of Claude Code's own
subagents (row id ``<parent id>/<agent id>``).
"""

import sqlite3

VERSION = 24
NAME = "agent_session_parents"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE agent_sessions ADD COLUMN thread TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE agent_sessions ADD COLUMN parent TEXT NOT NULL DEFAULT ''")
