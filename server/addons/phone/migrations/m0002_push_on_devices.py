"""Folds ``phone_devices`` into core's ``devices`` (core migration
``m0005_devices``): each row becomes a core device (kind ``phone``, its name,
created then, last seen at its last fetch; no token of its own until it is
paired) plus a row of ``phone_push`` keyed by that device, holding only what
push needs (the FCM token, app version, last fetch, last event number). Then
``phone_devices`` is dropped."""

import sqlite3
import uuid

VERSION = 2
NAME = "push_on_devices"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE phone_push (
        device_id TEXT PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        fcm_token TEXT NOT NULL,
        app_version TEXT,
        updated_at TEXT NOT NULL,
        last_fetch_at TEXT,
        last_seq INTEGER
    )""")
    conn.execute("CREATE INDEX idx_phone_push_user ON phone_push(user_id)")
    conn.execute("CREATE INDEX idx_phone_push_token ON phone_push(fcm_token)")
    rows = conn.execute("SELECT user_id, token, name, app_version, created_at, updated_at, last_fetch_at, last_seq "
                        "FROM phone_devices ORDER BY created_at").fetchall()
    for user_id, token, name, app_version, created_at, updated_at, last_fetch_at, last_seq in rows:
        device_id = uuid.uuid4().hex[:12]
        conn.execute("INSERT INTO devices (id, user_id, name, kind, room, role, token_id, created_at, last_seen_at) "
                     "VALUES (?, ?, ?, 'phone', '', '', NULL, ?, ?)",
                     (device_id, user_id, " ".join((name or "").split())[:60] or "Phone", created_at,
                      last_fetch_at or updated_at))
        conn.execute("INSERT INTO phone_push (device_id, user_id, fcm_token, app_version, updated_at, last_fetch_at, "
                     "last_seq) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (device_id, user_id, token, app_version, updated_at, last_fetch_at, last_seq))
    conn.execute("DROP TABLE phone_devices")
