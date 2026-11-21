"""A phone's device row belongs to its user: one row per (user, device id)
(docs/access.md, "Security rules"; integrations/phone/push.py).

Before, ``device_id`` alone was the key, so registering a device id another
user had (the id is not secret) moved that user's row — and their push token
— to the caller. Now the key is ``(user_id, device_id)``: the same device id
can be registered by two users, each getting their own row, and a row moves
to another user only when that user presents the stored FCM token (the same
installed app signing in as someone else). Every existing row is kept as it
is (same user, device id, token, timestamps).
"""

import sqlite3

VERSION = 17
NAME = "phone_devices_per_user"

STATEMENTS = [
    """CREATE TABLE phone_devices_new (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        device_id TEXT NOT NULL,
        token TEXT NOT NULL,
        name TEXT,
        app_version TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_fetch_at TEXT,
        last_seq INTEGER,
        PRIMARY KEY (user_id, device_id)
    )""",
    "INSERT INTO phone_devices_new (user_id, device_id, token, name, app_version, created_at, updated_at, "
    "last_fetch_at, last_seq) SELECT user_id, device_id, token, name, app_version, created_at, updated_at, "
    "last_fetch_at, last_seq FROM phone_devices",
    "DROP TABLE phone_devices",
    "ALTER TABLE phone_devices_new RENAME TO phone_devices",
    "CREATE INDEX idx_phone_devices_user ON phone_devices(user_id)",
    "CREATE INDEX idx_phone_devices_token ON phone_devices(token)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
