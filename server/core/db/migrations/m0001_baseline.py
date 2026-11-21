"""The core's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``memories``, ``weekly_reviews``, ``usage_events``, ``system_marks``, ``users``, ``user_tokens``, ``user_telegram``, ``sessions``, ``thread_messages``, ``shortcuts``, ``usage_daily``, ``connections``, ``connection_imports``, ``item_access``, ``uploaded_images``, ``connection_import_settings``, ``now_hidden``, ``briefings``, ``wake_log``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE memories (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE weekly_reviews (
        id TEXT PRIMARY KEY,
        week TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        headline TEXT NOT NULL DEFAULT '',
        data TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '')""",
    """CREATE INDEX idx_weekly_reviews_week ON weekly_reviews(week)""",
    """CREATE TABLE usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        purpose TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        ref TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        units REAL,
        cost REAL,
        ms INTEGER NOT NULL DEFAULT 0,
        ok INTEGER NOT NULL DEFAULT 1,
        error TEXT NOT NULL DEFAULT ''
    , user_id TEXT NOT NULL DEFAULT '')""",
    """CREATE INDEX idx_usage_events_day ON usage_events(day)""",
    """CREATE INDEX idx_usage_events_ref ON usage_events(ref) WHERE ref != ''""",
    """CREATE TABLE system_marks (
        key TEXT PRIMARY KEY,
        last_at TEXT,
        ok INTEGER,
        last_ok_at TEXT,
        last_fail_at TEXT,
        detail TEXT
    )""",
    """CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        access TEXT NOT NULL,
        telegram_chat_id INTEGER,
        created_at TEXT NOT NULL
    , context TEXT NOT NULL DEFAULT '', pronoun TEXT NOT NULL DEFAULT 'they' CHECK (pronoun IN ('he', 'she', 'they')), other_names TEXT NOT NULL DEFAULT '', prompt_name TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE user_tokens (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        label TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        session_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT
    )""",
    """CREATE INDEX idx_user_tokens_user ON user_tokens(user_id)""",
    """CREATE TABLE user_telegram (
        telegram_user_id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    )""",
    """CREATE INDEX idx_user_telegram_user ON user_telegram(user_id)""",
    """CREATE TABLE "sessions" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    history TEXT DEFAULT '[]',
    pending_approval TEXT DEFAULT NULL,
    status TEXT DEFAULT 'idle',
    title TEXT DEFAULT '',
    debug_info TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    context TEXT DEFAULT NULL, cleared_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, session_id)
)""",
    """CREATE TABLE "thread_messages" (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    ts REAL NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL
)""",
    """CREATE TABLE "shortcuts" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phrase_norm TEXT NOT NULL,
    phrase TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,
    reply TEXT NOT NULL DEFAULT '',
    streak INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    last_used REAL,
    learned_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (user_id, phrase_norm)
)""",
    """CREATE TABLE "usage_daily" (
    day TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    purpose TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    calls INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    unpriced INTEGER NOT NULL DEFAULT 0,
    ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, kind, purpose, source, model)
)""",
    """CREATE INDEX idx_thread_messages_user_thread ON thread_messages(user_id, thread_id, seq)""",
    """CREATE INDEX idx_usage_events_user_day ON usage_events(user_id, day)""",
    """CREATE TABLE "connections" (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        addon TEXT NOT NULL,
        credentials TEXT,
        settings TEXT NOT NULL DEFAULT '{}',
        account TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, account_conflict TEXT,
        PRIMARY KEY (user_id, addon)
    )""",
    """CREATE TABLE "connection_imports" (
        addon TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        imported_at TEXT NOT NULL
    )""",
    """CREATE TABLE item_access (
        item_type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        level TEXT NOT NULL CHECK (level IN ('read', 'edit')),
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        PRIMARY KEY (item_type, item_id, user_id)
    )""",
    """CREATE INDEX idx_item_access_user ON item_access(user_id, item_type)""",
    """CREATE TABLE uploaded_images (
        name TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    )""",
    """CREATE INDEX idx_uploaded_images_user ON uploaded_images(user_id)""",
    """CREATE TABLE "connection_import_settings" (
        addon TEXT NOT NULL,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (addon, name)
    )""",
    """CREATE INDEX idx_weekly_reviews_user ON weekly_reviews(user_id, week)""",
    """CREATE TABLE "now_hidden" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    until TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_id)
)""",
    """CREATE TABLE "briefings" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    headline TEXT NOT NULL,
    points TEXT NOT NULL DEFAULT '[]',
    data TEXT NOT NULL DEFAULT '{}',
    trigger TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    PRIMARY KEY (user_id, date)
)""",
    """CREATE TABLE "wake_log" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    at TEXT NOT NULL,
    why TEXT,
    PRIMARY KEY (user_id, date)
)""",
    """CREATE INDEX idx_connections_addon_account ON connections(addon, account)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
