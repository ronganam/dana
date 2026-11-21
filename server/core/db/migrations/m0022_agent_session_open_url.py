"""``agent_sessions.open_url``: where tapping the session's notification goes
(the T3 Code thread it runs in, ``https://app.t3.codes/threads/<environment>/<thread>``);
empty when the hook sent none (integrations/phone/agent_sessions.py).
"""

import sqlite3

VERSION = 22
NAME = "agent_session_open_url"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE agent_sessions ADD COLUMN open_url TEXT NOT NULL DEFAULT ''")
