"""A device's push registration names its transport: ``phone_push`` gets
``transport`` (``fcm`` | ``unifiedpush``) and ``address`` (the FCM token or
the UnifiedPush endpoint) in place of ``fcm_token``; every existing row is an
``fcm`` one. The table is rebuilt (nothing references it)."""

import sqlite3

VERSION = 3
NAME = "push_transports"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE phone_push_new (
        device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        transport TEXT NOT NULL,
        address TEXT NOT NULL,
        app_version TEXT,
        updated_at TEXT NOT NULL,
        last_fetch_at TEXT,
        last_seq INTEGER
    )""")
    conn.execute("INSERT INTO phone_push_new (device_id, user_id, transport, address, app_version, updated_at, "
                 "last_fetch_at, last_seq) SELECT device_id, user_id, 'fcm', fcm_token, app_version, updated_at, "
                 "last_fetch_at, last_seq FROM phone_push")
    conn.execute("DROP TABLE phone_push")
    conn.execute("ALTER TABLE phone_push_new RENAME TO phone_push")
    conn.execute("CREATE INDEX idx_phone_push_user ON phone_push(user_id)")
    conn.execute("CREATE INDEX idx_phone_push_address ON phone_push(transport, address)")
