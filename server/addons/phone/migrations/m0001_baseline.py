"""The phone addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``phone_calls``, ``phone_calls_fts``, ``call_suggestions``, ``notifications_fts``, ``notification_action_requests``, ``phone_events``, ``notification_digest_state``, ``notifications``, ``notification_rules``, ``notification_summaries``, ``notification_rule_declines``, ``phone_devices``, ``agent_sessions``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE phone_calls (
        id TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        caller TEXT DEFAULT 'Unknown',
        receiver TEXT DEFAULT 'Me',
        call_timestamp TEXT NOT NULL,
        transcript TEXT,
        summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        title TEXT,
        duration_seconds INTEGER DEFAULT 0,
        labels TEXT NOT NULL DEFAULT '[]',
        analysis TEXT,
        importance TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'ready',
        error TEXT,
        audio_sha256 TEXT,
        source_filename TEXT,
        direction TEXT,
        phone_number TEXT,
        project_id TEXT,
        mime_type TEXT,
        instructions TEXT,
        audio_deleted_at TEXT,
        person_id TEXT
    , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '')""",
    """CREATE INDEX idx_phone_calls_sha ON phone_calls(audio_sha256)""",
    """CREATE INDEX idx_phone_calls_source ON phone_calls(source_filename)""",
    """CREATE INDEX idx_phone_calls_person ON phone_calls(person_id)""",
    """CREATE VIRTUAL TABLE phone_calls_fts USING fts5(
        id UNINDEXED, caller, receiver, title, summary, transcript, tokenize='unicode61'
    )""",
    """CREATE TRIGGER phone_calls_ai AFTER INSERT ON phone_calls BEGIN
        INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
        VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
    END""",
    """CREATE TRIGGER phone_calls_ad AFTER DELETE ON phone_calls BEGIN
        DELETE FROM phone_calls_fts WHERE id = old.id;
    END""",
    """CREATE TRIGGER phone_calls_au AFTER UPDATE OF caller, receiver, title, summary, transcript
    ON phone_calls BEGIN
        DELETE FROM phone_calls_fts WHERE id = old.id;
        INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
        VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
    END""",
    """CREATE TABLE call_suggestions (
        id TEXT PRIMARY KEY,
        call_id TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'task',
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending',
        result_ref TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP,
        source_type TEXT NOT NULL DEFAULT 'call'
    , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '')""",
    """CREATE INDEX idx_call_suggestions_status ON call_suggestions(status, created_at)""",
    """CREATE INDEX idx_call_suggestions_call ON call_suggestions(call_id)""",
    """CREATE INDEX idx_call_suggestions_source ON call_suggestions(source_type, status)""",
    """CREATE VIRTUAL TABLE notifications_fts USING fts5(
        id UNINDEXED, app_name, title, text, sender, conversation, tokenize='unicode61'
    )""",
    """CREATE TABLE notification_action_requests (
        id TEXT PRIMARY KEY,
        notification_id TEXT NOT NULL,
        device_key TEXT,
        action TEXT NOT NULL,
        text TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        created_at TEXT NOT NULL,
        done_at TEXT
    , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '')""",
    """CREATE TABLE phone_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '')""",
    """CREATE INDEX idx_phone_events_user ON phone_events(user_id, seq)""",
    """CREATE TABLE notification_digest_state (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        slot TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, slot)
    )""",
    """CREATE TABLE "notifications" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
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
)""",
    """CREATE INDEX idx_notifications_status ON notifications(status, posted_at)""",
    """CREATE INDEX idx_notifications_key ON notifications(device_key)""",
    """CREATE INDEX idx_notifications_package ON notifications(package, posted_at)""",
    """CREATE INDEX idx_notifications_triage ON notifications(triage_status)""",
    """CREATE INDEX idx_notifications_person ON notifications(person_id)""",
    """CREATE INDEX idx_notifications_user ON notifications(user_id, status, posted_at)""",
    """CREATE TRIGGER notifications_fts_ai AFTER INSERT ON notifications BEGIN
        INSERT INTO notifications_fts(id, app_name, title, text, sender, conversation)
        VALUES (new.id, new.app_name, new.title, new.text, new.sender, new.conversation);
    END""",
    """CREATE TRIGGER notifications_fts_ad AFTER DELETE ON notifications BEGIN
        DELETE FROM notifications_fts WHERE id = old.id;
    END""",
    """CREATE TABLE "notification_rules" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, package, sender)
)""",
    """CREATE TABLE "notification_summaries" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    signature TEXT NOT NULL,
    summary TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scope)
)""",
    """CREATE TABLE "notification_rule_declines" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    declined_at TEXT NOT NULL,
    PRIMARY KEY (user_id, package)
)""",
    """CREATE INDEX idx_phone_calls_user ON phone_calls(user_id, call_timestamp)""",
    """CREATE INDEX idx_call_suggestions_user ON call_suggestions(user_id, status)""",
    """CREATE TABLE "phone_devices" (
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
    """CREATE INDEX idx_phone_devices_user ON phone_devices(user_id)""",
    """CREATE INDEX idx_phone_devices_token ON phone_devices(token)""",
    """CREATE TABLE agent_sessions (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        id TEXT NOT NULL,
        agent TEXT NOT NULL,
        host TEXT NOT NULL DEFAULT '',
        project TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        activity TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        steps_done INTEGER NOT NULL DEFAULT 0,
        steps_total INTEGER NOT NULL DEFAULT 0,
        tools INTEGER NOT NULL DEFAULT 0,
        rev INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, open_url TEXT NOT NULL DEFAULT '', thread TEXT NOT NULL DEFAULT '', parent TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (user_id, id)
    )""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
