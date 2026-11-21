"""Money: the price checks of the Save view (``integrations/money/market.py``).

``money_market_checks``: the newest check per ``(user_id, topic, params_key)``
(one row, replaced by the next check). ``topic`` is ``telecom`` /
``electricity`` / ``fx_card`` / ``arnona``; ``params_key`` the canonical JSON
of ``params`` (the check's own fields, JSON); ``state`` ``checking`` / ``ok`` /
``failed``; ``items`` and ``sources`` (JSON lists) and ``checked_at`` are those
of the last check that succeeded (kept while a new one runs and when it
fails); ``model`` the search model that found them; ``error`` the plain-words
reason of a failure; ``started_at`` when the newest check began (ISO UTC).
"""

import sqlite3

VERSION = 30
NAME = "money_market"

STATEMENTS = [
    """CREATE TABLE money_market_checks (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        topic TEXT NOT NULL,
        params_key TEXT NOT NULL,
        params TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('checking', 'ok', 'failed')),
        items TEXT NOT NULL DEFAULT '[]',
        sources TEXT NOT NULL DEFAULT '[]',
        model TEXT,
        error TEXT,
        started_at TEXT NOT NULL,
        checked_at TEXT,
        UNIQUE (user_id, topic, params_key)
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
