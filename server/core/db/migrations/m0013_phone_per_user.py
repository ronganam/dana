"""The phone's devices, push events and notifications belong to a user
(docs/notifications.md, docs/access.md "Kinds").

- ``phone_devices.user_id``: whose phone it is (it registered with that
  user's API token); push reaches only that user's devices.
- ``phone_events.user_id``: whose phone fetches the event. ``seq`` stays one
  numbering for the server (AUTOINCREMENT, kept as it was): a phone that
  already holds a number keeps fetching after it, nothing is renumbered or
  replayed.
- ``notifications``: rebuilt with ``user_id`` and ``UNIQUE (user_id,
  content_hash)``: the same notification on two people's phones (a shared
  group chat) is stored once for each. The FTS index ``notifications_fts`` is
  left exactly as it was (its rows are keyed by the notification id, which is
  kept); the insert / delete triggers are put back.
- ``notification_rules`` (unique per user, app and sender),
  ``notification_summaries`` (key: user and scope),
  ``notification_rule_declines`` (key: user and app): rebuilt with
  ``user_id``. ``notification_action_requests.user_id``: who asked.
- ``notification_digest_state``: when each user's digest slots last fired and
  what the last digest covered, until now the ``notif_digest_*`` rows of the
  old ``memories`` table (not per user). Those rows move here, to the first
  admin, and leave ``memories``.
- The self-check marks ``push`` and ``notification_sync`` become the first
  admin's (``push:<user id>``, ``notification_sync:<user id>``): the marks are
  per user now.

Every existing row goes to the first admin (created from env when the
database has rows but no user yet), ids kept. Adding a column with a
``REFERENCES`` clause and a non-NULL default needs foreign keys off
(``FOREIGN_KEYS_OFF``); '' never names a user, so an insert that forgets the
owner fails its foreign key afterwards.
"""

import sqlite3
from typing import Optional, Sequence

from core import user_rows

VERSION = 13
NAME = "phone_per_user"
FOREIGN_KEYS_OFF = True

OWNER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"

NOTIFICATIONS = f"""CREATE TABLE notifications_new (
    id TEXT PRIMARY KEY,
    {OWNER},
    device_key TEXT,
    package TEXT NOT NULL,
    app_name TEXT,
    title TEXT,
    text TEXT,
    sub_text TEXT,
    sender TEXT,
    conversation TEXT,
    is_group INTEGER NOT NULL DEFAULT 0,
    android_category TEXT,
    channel TEXT,
    posted_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    removed_reason TEXT,
    removed_at TEXT,
    triage_status TEXT NOT NULL DEFAULT 'pending',
    category TEXT,
    priority TEXT,
    priority_score REAL,
    bucket TEXT NOT NULL DEFAULT 'normal',
    triage TEXT,
    action_status TEXT NOT NULL DEFAULT 'none',
    escalated_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    snoozed_until TEXT,
    seen_at TEXT,
    nudges INTEGER DEFAULT 0,
    actions TEXT,
    person_id TEXT,
    UNIQUE (user_id, content_hash)
)"""
NOTIFICATION_COLUMNS = (
    "id", "device_key", "package", "app_name", "title", "text", "sub_text", "sender", "conversation", "is_group",
    "android_category", "channel", "posted_at", "content_hash", "status", "removed_reason", "removed_at",
    "triage_status", "category", "priority", "priority_score", "bucket", "triage", "action_status", "escalated_at",
    "created_at", "updated_at", "snoozed_until", "seen_at", "nudges", "actions", "person_id")
NOTIFICATION_INDEXES = (
    "CREATE INDEX idx_notifications_status ON notifications(status, posted_at)",
    "CREATE INDEX idx_notifications_key ON notifications(device_key)",
    "CREATE INDEX idx_notifications_package ON notifications(package, posted_at)",
    "CREATE INDEX idx_notifications_triage ON notifications(triage_status)",
    "CREATE INDEX idx_notifications_person ON notifications(person_id)",
    "CREATE INDEX idx_notifications_user ON notifications(user_id, status, posted_at)",
    """CREATE TRIGGER notifications_fts_ai AFTER INSERT ON notifications BEGIN
        INSERT INTO notifications_fts(id, app_name, title, text, sender, conversation)
        VALUES (new.id, new.app_name, new.title, new.text, new.sender, new.conversation);
    END""",
    """CREATE TRIGGER notifications_fts_ad AFTER DELETE ON notifications BEGIN
        DELETE FROM notifications_fts WHERE id = old.id;
    END""",
)

RULES = f"""CREATE TABLE notification_rules_new (
    id TEXT PRIMARY KEY,
    {OWNER},
    package TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, package, sender)
)"""
SUMMARIES = f"""CREATE TABLE notification_summaries_new (
    {OWNER},
    scope TEXT NOT NULL,
    signature TEXT NOT NULL,
    summary TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scope)
)"""
DECLINES = f"""CREATE TABLE notification_rule_declines_new (
    {OWNER},
    package TEXT NOT NULL,
    declined_at TEXT NOT NULL,
    PRIMARY KEY (user_id, package)
)"""

STATEMENTS = [
    f"ALTER TABLE phone_devices ADD COLUMN {OWNER} DEFAULT ''",
    "CREATE INDEX idx_phone_devices_user ON phone_devices(user_id)",
    f"ALTER TABLE phone_events ADD COLUMN {OWNER} DEFAULT ''",
    "CREATE INDEX idx_phone_events_user ON phone_events(user_id, seq)",
    f"ALTER TABLE notification_action_requests ADD COLUMN {OWNER} DEFAULT ''",
    f"""CREATE TABLE notification_digest_state (
        {OWNER},
        slot TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, slot)
    )""",
]

# The digest's rows in the old memories table: ``notif_digest_HHMM`` (the date
# that slot last fired) and ``notif_digest_last`` (until when the last one covered).
DIGEST_PREFIX = "notif_digest_"
MARKS = ("push", "notification_sync")


def _rebuild(conn: sqlite3.Connection, table: str, create: str, columns: Sequence[str], owner: Optional[str]) -> None:
    """``table`` again from ``create`` (which makes ``<table>_new``), its rows
    copied with ``owner`` as their user."""
    conn.execute(create)
    if owner is not None:
        cols = ", ".join(columns)
        conn.execute(f"INSERT INTO {table}_new (user_id, {cols}) SELECT ?, {cols} FROM {table}", (owner,))
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")


def up(conn: sqlite3.Connection) -> None:
    owner = user_rows.owner_of_existing_rows(conn, (
        "phone_devices", "phone_events", "notifications", "notification_rules", "notification_summaries",
        "notification_rule_declines", "notification_action_requests"))
    for sql in STATEMENTS:
        conn.execute(sql)
    if owner is not None:
        for table in ("phone_devices", "phone_events", "notification_action_requests"):
            conn.execute(f"UPDATE {table} SET user_id = ?", (owner,))
    _rebuild(conn, "notifications", NOTIFICATIONS, NOTIFICATION_COLUMNS, owner)
    for sql in NOTIFICATION_INDEXES:
        conn.execute(sql)
    _rebuild(conn, "notification_rules", RULES, ("id", "package", "sender", "action", "created_at"), owner)
    _rebuild(conn, "notification_summaries", SUMMARIES, ("scope", "signature", "summary", "count", "updated_at"),
             owner)
    _rebuild(conn, "notification_rule_declines", DECLINES, ("package", "declined_at"), owner)

    rows = [r for r in conn.execute("SELECT key, value FROM memories ORDER BY key").fetchall()
            if str(r[0]).startswith(DIGEST_PREFIX)]
    if owner is not None:
        for key, value in rows:
            conn.execute("INSERT INTO notification_digest_state (user_id, slot, value) VALUES (?, ?, ?)",
                         (owner, key[len(DIGEST_PREFIX):], value))
    for key, _ in rows:
        conn.execute("DELETE FROM memories WHERE key = ?", (key,))

    for mark in MARKS:
        if owner is not None:
            conn.execute("UPDATE system_marks SET key = ? WHERE key = ?", (f"{mark}:{owner}", mark))
        else:
            conn.execute("DELETE FROM system_marks WHERE key = ?", (mark,))
