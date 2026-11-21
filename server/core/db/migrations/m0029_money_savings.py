"""``money_savings``: the user's choices about Dana's saving ideas (integrations/money/savings.py).

One row per idea Dana has found for the user, unique per ``(user_id, key)``
(``key`` such as ``bill:<merchant key>``, ``cash:<account id>``,
``creep:eating_out``; ``type`` its detector). Rows are written when an idea is
first detected and kept: the user's choice outlives the detector.

- ``status``: ``open`` (Dana decides), ``doing`` / ``done`` / ``dismissed`` /
  ``snoozed`` (ways to save), ``accepted`` / ``watching`` (spending to watch);
  ``status_at`` (ISO UTC) when it was last set, ``snooze_until`` (a local
  date) for ``snoozed``, ``done_at`` (ISO UTC) for ``done``.
- ``first_seen_at`` (ISO UTC): when Dana first found it.
- ``answers``: JSON, the user's answers to its questions (``{"service":
  "mobile", "lines": 2}``) and subscription choices (``"sub:<merchant key>"``).
- ``target``: a monthly spending target in agorot (watching), NULL without one.
- ``baseline``: JSON, the numbers when it was marked doing / done / watching
  (the amount and cadence, the merchant keys, the old monthly pace…), which
  the outcome is measured against.
- ``snapshot``: JSON, the last detected payload, to show the item after its
  detector stops firing.
"""

import sqlite3

VERSION = 29
NAME = "money_savings"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE money_savings (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'doing', 'done', 'dismissed', 'snoozed', 'accepted', 'watching')),
        status_at TEXT,
        snooze_until TEXT,
        first_seen_at TEXT NOT NULL,
        answers TEXT NOT NULL DEFAULT '{}',
        target INTEGER,
        baseline TEXT,
        snapshot TEXT,
        done_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (user_id, key)
    )""")
