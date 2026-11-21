"""``money_signals``: payment notifications from the user's phone (integrations/money/signals.py).

One row per signal: ``source`` ``notification`` (a Google Wallet notification
of the Inbox, ``notification_id``) or ``phone_app`` (a card / bank app's push
the phone posts to ``/api/money/signals``: its shade ``key``). ``title`` /
``text`` as the phone gave them (codes redacted, ID and card numbers
scrubbed); ``posted_at`` ISO UTC. ``parsed`` = 1 when an amount was read:
``amount`` signed agorot in ``currency`` (a payment is negative),
``card_last4``, ``merchant``. ``transaction_id`` is the transaction it
became or was matched to. A notification is recorded once; a phone push
once per ``(key, text)``. Rows are kept 60 days.
"""

import sqlite3

VERSION = 26
NAME = "money_signals"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE money_signals (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source TEXT NOT NULL CHECK (source IN ('phone_app', 'notification')),
        notification_id TEXT,
        key TEXT NOT NULL DEFAULT '',
        package TEXT,
        title TEXT,
        text TEXT NOT NULL DEFAULT '',
        posted_at TEXT,
        parsed INTEGER NOT NULL DEFAULT 0,
        amount INTEGER,
        currency TEXT,
        card_last4 TEXT,
        merchant TEXT,
        transaction_id TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX idx_money_signals_notification ON money_signals(user_id, notification_id) "
                 "WHERE notification_id IS NOT NULL")
    conn.execute("CREATE UNIQUE INDEX idx_money_signals_push ON money_signals(user_id, key, text) "
                 "WHERE source = 'phone_app'")
    conn.execute("CREATE INDEX idx_money_signals_created ON money_signals(user_id, created_at)")
    conn.execute("CREATE INDEX idx_money_signals_transaction ON money_signals(transaction_id)")
