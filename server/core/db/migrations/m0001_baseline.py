"""Baseline: the complete schema of Dana 8.1.0.

On a fresh database this creates everything. On a database made by 8.1.0 it
changes nothing: every statement is ``IF NOT EXISTS``, and the only objects it
can add there are the tables 8.1.0 created lazily on first use
(:data:`LAZY_TABLES`) and their indexes. The runner refuses a database older
than 8.1.0 before this runs (:func:`missing_from`).

Column order matches a fresh 8.1.0 database (columns that 8.1.0 added with
``ALTER TABLE`` come last), so ``SELECT *`` and positional inserts behave the
same on fresh and upgraded databases.
"""

import sqlite3
from typing import Dict, List

VERSION = 1
NAME = "baseline"

# Tables 8.1.0 created on first use rather than at startup: an 8.1.0 database
# that never used the feature has no such table yet, which is not "older".
LAZY_TABLES = frozenset({
    "thread_messages", "shortcuts", "usage_events", "usage_daily",
    "system_marks", "wake_log", "now_hidden", "notification_rule_declines",
})

# Columns an 8.1.0 database may lack: 8.1.0 tried to add sessions.created_at
# with ALTER TABLE ... DEFAULT CURRENT_TIMESTAMP, which SQLite always refuses
# (the error was swallowed), so databases whose sessions table predates that
# column never got it. Nothing reads it.
OPTIONAL_COLUMNS = frozenset({("sessions", "created_at")})

STATEMENTS: List[str] = [
    # --- Old key/value memories (system trigger state; real ones moved to About) ---
    """CREATE TABLE IF NOT EXISTS memories (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",

    # --- Tasks --------------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS task_groups (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        emoji TEXT DEFAULT '📁',
        color TEXT DEFAULT '#6366f1',
        description TEXT DEFAULT '',
        sort_order INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'medium',
        due_date TEXT,
        status TEXT DEFAULT 'needs_action',
        triggers TEXT DEFAULT '{}',
        group_id TEXT REFERENCES task_groups(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        snooze_count INTEGER NOT NULL DEFAULT 0,
        project_id TEXT REFERENCES projects(id)
    )""",
    """CREATE TABLE IF NOT EXISTS task_links (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        ref_type TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'origin',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(task_id, ref_type, ref_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_task_links_ref ON task_links(ref_type, ref_id)",

    # --- Phone calls (core/call_pipeline.py) ------------------------------------------
    """CREATE TABLE IF NOT EXISTS phone_calls (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_phone_calls_sha ON phone_calls(audio_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_phone_calls_source ON phone_calls(source_filename)",
    "CREATE INDEX IF NOT EXISTS idx_phone_calls_person ON phone_calls(person_id)",
    # A regular (not external-content) FTS5 table: rows are removed with
    # DELETE ... WHERE id = (the FTS5 'delete' command only works on
    # external-content tables).
    """CREATE VIRTUAL TABLE IF NOT EXISTS phone_calls_fts USING fts5(
        id UNINDEXED, caller, receiver, title, summary, transcript, tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS phone_calls_ai AFTER INSERT ON phone_calls BEGIN
        INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
        VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
    END""",
    """CREATE TRIGGER IF NOT EXISTS phone_calls_ad AFTER DELETE ON phone_calls BEGIN
        DELETE FROM phone_calls_fts WHERE id = old.id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS phone_calls_au AFTER UPDATE OF caller, receiver, title, summary, transcript
    ON phone_calls BEGIN
        DELETE FROM phone_calls_fts WHERE id = old.id;
        INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
        VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
    END""",
    # Suggestions from calls and notifications: source_type says what call_id
    # points at ('call' → phone_calls.id, 'notification' → notifications.id).
    """CREATE TABLE IF NOT EXISTS call_suggestions (
        id TEXT PRIMARY KEY,
        call_id TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'task',
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending',
        result_ref TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resolved_at TIMESTAMP,
        source_type TEXT NOT NULL DEFAULT 'call'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_call_suggestions_status ON call_suggestions(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_call_suggestions_call ON call_suggestions(call_id)",
    "CREATE INDEX IF NOT EXISTS idx_call_suggestions_source ON call_suggestions(source_type, status)",

    # --- Phone notifications (core/notifications.py) ------------------------------------
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
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
        content_hash TEXT NOT NULL UNIQUE,
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
        person_id TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_notifications_status ON notifications(status, posted_at)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_key ON notifications(device_key)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_package ON notifications(package, posted_at)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_triage ON notifications(triage_status)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_person ON notifications(person_id)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS notifications_fts USING fts5(
        id UNINDEXED, app_name, title, text, sender, conversation, tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS notifications_fts_ai AFTER INSERT ON notifications BEGIN
        INSERT INTO notifications_fts(id, app_name, title, text, sender, conversation)
        VALUES (new.id, new.app_name, new.title, new.text, new.sender, new.conversation);
    END""",
    """CREATE TRIGGER IF NOT EXISTS notifications_fts_ad AFTER DELETE ON notifications BEGIN
        DELETE FROM notifications_fts WHERE id = old.id;
    END""",
    # Per-app / per-sender rules: mute, important, alert.
    """CREATE TABLE IF NOT EXISTS notification_rules (
        id TEXT PRIMARY KEY,
        package TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(package, sender)
    )""",
    # Button presses / opens / replies requested in the web Inbox, carried out by the phone.
    """CREATE TABLE IF NOT EXISTS notification_action_requests (
        id TEXT PRIMARY KEY,
        notification_id TEXT NOT NULL,
        device_key TEXT,
        action TEXT NOT NULL,
        text TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        created_at TEXT NOT NULL,
        done_at TEXT
    )""",
    # AI summary per app (scope = package) and the Inbox briefing (scope '_overall').
    """CREATE TABLE IF NOT EXISTS notification_summaries (
        scope TEXT PRIMARY KEY,
        signature TEXT NOT NULL,
        summary TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
    # Rule suggestions the owner declined (core/notification_overview.py).
    """CREATE TABLE IF NOT EXISTS notification_rule_declines (
        package TEXT PRIMARY KEY,
        declined_at TEXT NOT NULL
    )""",

    # --- Direct push to the phone (core/phone_push.py) ------------------------------------
    """CREATE TABLE IF NOT EXISTS phone_devices (
        device_id TEXT PRIMARY KEY,
        token TEXT NOT NULL,
        name TEXT,
        app_version TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_fetch_at TEXT,
        last_seq INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS phone_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",

    # --- People (core/people.py) -------------------------------------------------------
    # Calls and notifications carry person_id: NULL = not matched yet, '' = no match.
    """CREATE TABLE IF NOT EXISTS people (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]',
        relation TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        birthday TEXT NOT NULL DEFAULT '',
        pinned INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'manual',
        brief TEXT,
        brief_signature TEXT,
        brief_updated_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Normalised digits, e.g. 972501234567, for the ringing-phone lookup.
    """CREATE TABLE IF NOT EXISTS person_phones (
        phone TEXT PRIMARY KEY,
        person_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_person_phones_person ON person_phones(person_id)",
    # Wake briefing (core/briefing.py): one per local date.
    """CREATE TABLE IF NOT EXISTS briefings (
        date TEXT PRIMARY KEY,
        headline TEXT NOT NULL,
        points TEXT NOT NULL DEFAULT '[]',
        data TEXT NOT NULL DEFAULT '{}',
        trigger TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        delivered_at TEXT
    )""",
    # Weekly review (core/weekly_review.py): proposals, then what was applied.
    """CREATE TABLE IF NOT EXISTS weekly_reviews (
        id TEXT PRIMARY KEY,
        week TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        headline TEXT NOT NULL DEFAULT '',
        data TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_weekly_reviews_week ON weekly_reviews(week)",

    # --- Projects & knowledge (skills/knowledge.py) ----------------------------------------
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        icon TEXT DEFAULT '📁',
        color TEXT DEFAULT '#6366f1',
        tags TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # Entries: the only entity inside a project. kind is a rendering hint
    # ('note' | 'record' | 'link'); fields is a JSON array of {name, type, value}.
    """CREATE TABLE IF NOT EXISTS entries (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'note',
        title TEXT NOT NULL,
        body TEXT,
        fields TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'open',
        priority TEXT NOT NULL DEFAULT 'medium',
        due_at TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        url TEXT,
        media_path TEXT,
        transcript TEXT,
        summary TEXT,
        comments TEXT NOT NULL DEFAULT '[]',
        source_author TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
        id UNINDEXED, project_id UNINDEXED, title, body, summary, transcript, tags, fields,
        tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
        VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
    END""",
    """CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
        DELETE FROM entries_fts WHERE id = old.id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
        DELETE FROM entries_fts WHERE id = old.id;
        INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
        VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
    END""",

    # --- Shopping list (core/shopping.py) ---------------------------------------------------
    """CREATE TABLE IF NOT EXISTS shopping_items (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        checked INTEGER DEFAULT 0,
        position INTEGER DEFAULT 0,
        metadata_json TEXT DEFAULT '{}',
        enrichment_json TEXT DEFAULT '{}',
        selected_offer TEXT DEFAULT '',
        source TEXT DEFAULT '',
        added_by TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS shopping_operations (
        id TEXT PRIMARY KEY,
        op_type TEXT NOT NULL,
        payload_json TEXT DEFAULT '{}',
        source TEXT DEFAULT '',
        actor TEXT DEFAULT '',
        chat_id TEXT DEFAULT '',
        message_id TEXT DEFAULT '',
        undone INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_shopping_items_checked_pos ON shopping_items(checked, position)",
    "CREATE INDEX IF NOT EXISTS idx_shopping_ops_undone_created ON shopping_operations(undone, created_at)",

    # --- Product catalog (core/catalog.py) ------------------------------------------------
    """CREATE TABLE IF NOT EXISTS catalog_sources (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kind TEXT DEFAULT 'xml',
        config_json TEXT DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_stores (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES catalog_sources(id),
        chain_id TEXT DEFAULT '',
        sub_chain_id TEXT DEFAULT '',
        store_id TEXT DEFAULT '',
        name TEXT DEFAULT '',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_imports (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES catalog_sources(id),
        filename TEXT DEFAULT '',
        checksum TEXT DEFAULT '',
        status TEXT DEFAULT 'done',
        item_count INTEGER DEFAULT 0,
        error TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_products (
        barcode TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        manufacturer TEXT DEFAULT '',
        description TEXT DEFAULT '',
        images_json TEXT DEFAULT '{}',
        metadata_json TEXT DEFAULT '{}',
        enriched_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_offers (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES catalog_sources(id),
        store_id TEXT DEFAULT '',
        barcode TEXT NOT NULL REFERENCES catalog_products(barcode),
        price REAL DEFAULT 0,
        unit_price REAL DEFAULT 0,
        unit TEXT DEFAULT '',
        quantity REAL DEFAULT 0,
        available INTEGER DEFAULT 1,
        price_updated_at TEXT DEFAULT '',
        metadata_json TEXT DEFAULT '{}',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_catalog_offers_barcode ON catalog_offers(barcode)",
    "CREATE INDEX IF NOT EXISTS idx_catalog_offers_store ON catalog_offers(source_id, store_id)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
        barcode UNINDEXED, name, manufacturer, description, tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS catalog_products_ai AFTER INSERT ON catalog_products BEGIN
        INSERT INTO catalog_fts(barcode, name, manufacturer, description)
        VALUES (new.barcode, new.name, new.manufacturer, new.description);
    END""",
    """CREATE TRIGGER IF NOT EXISTS catalog_products_ad AFTER DELETE ON catalog_products BEGIN
        DELETE FROM catalog_fts WHERE barcode = old.barcode;
    END""",
    """CREATE TRIGGER IF NOT EXISTS catalog_products_au AFTER UPDATE ON catalog_products BEGIN
        DELETE FROM catalog_fts WHERE barcode = old.barcode;
        INSERT INTO catalog_fts(barcode, name, manufacturer, description)
        VALUES (new.barcode, new.name, new.manufacturer, new.description);
    END""",

    # --- About you (core/about.py) -----------------------------------------------------
    """CREATE TABLE IF NOT EXISTS about_facts (
        id TEXT PRIMARY KEY,
        section TEXT NOT NULL DEFAULT 'other',
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        source_kind TEXT NOT NULL DEFAULT 'owner',
        source_ref TEXT,
        source_label TEXT,
        legacy_key TEXT UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        confirmed_at TEXT,
        inactive_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_about_facts_status ON about_facts(status, section)",
    """CREATE TABLE IF NOT EXISTS about_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",

    # --- Conversations (core/session.py, core/thread.py, core/shortcuts.py) ---------------
    # context: JSON of what the chat was opened from (a call suggestion, a call, a task).
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        history TEXT DEFAULT '[]',
        pending_approval TEXT DEFAULT NULL,
        status TEXT DEFAULT 'idle',
        title TEXT DEFAULT '',
        debug_info TEXT DEFAULT '{}',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        context TEXT DEFAULT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS thread_messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT NOT NULL,
        ts REAL NOT NULL,
        device TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_thread_messages_thread ON thread_messages(thread_id, seq)",
    """CREATE TABLE IF NOT EXISTS shortcuts (
        id TEXT PRIMARY KEY,
        phrase_norm TEXT NOT NULL UNIQUE,
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
        updated_at REAL NOT NULL
    )""",

    # --- Usage and cost (core/usage.py) -------------------------------------------------
    """CREATE TABLE IF NOT EXISTS usage_events (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_day ON usage_events(day)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_ref ON usage_events(ref) WHERE ref != ''",
    """CREATE TABLE IF NOT EXISTS usage_daily (
        day TEXT NOT NULL,
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
        PRIMARY KEY (day, kind, purpose, source, model)
    )""",

    # --- Self-checks and Now (core/checks.py, core/now.py) -----------------------------------
    """CREATE TABLE IF NOT EXISTS system_marks (
        key TEXT PRIMARY KEY,
        last_at TEXT,
        ok INTEGER,
        last_ok_at TEXT,
        last_fail_at TEXT,
        detail TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS wake_log (
        date TEXT PRIMARY KEY,
        at TEXT NOT NULL,
        why TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS now_hidden (
        item_id TEXT PRIMARY KEY,
        until TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )""",
]

# One-time repairs 8.1.0 ran at every startup; no-ops on a consistent 8.1.0
# database, kept so an upgraded database ends up in the same state.
REPAIRS: List[str] = [
    # Calls stored before titles existed.
    "UPDATE phone_calls SET title = CASE WHEN caller != 'Me' THEN 'Call with ' || caller "
    "ELSE 'Call to ' || receiver END WHERE title IS NULL",
    # Stored files are "<uuid>_<original name>": recover the original name so
    # re-uploads of old recordings are recognised as duplicates.
    "UPDATE phone_calls SET source_filename = substr(filename, 38) "
    "WHERE source_filename IS NULL AND length(filename) > 37 AND substr(filename, 37, 1) = '_'",
    # Calls missing from the search index.
    "INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript) "
    "SELECT id, caller, receiver, title, summary, transcript FROM phone_calls "
    "WHERE id NOT IN (SELECT id FROM phone_calls_fts)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS + REPAIRS:
        conn.execute(sql)


def _columns(conn: sqlite3.Connection) -> Dict[str, List[str]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {t: [r[1] for r in conn.execute(f"PRAGMA table_info('{t}')")] for t in tables}


def missing_from(conn: sqlite3.Connection) -> List[str]:
    """What an existing database made before migrations lacks compared to
    8.1.0: a table 8.1.0 created at startup, or a column of any table (except
    :data:`OPTIONAL_COLUMNS`). Empty for an 8.1.0 database. Extra (legacy)
    tables and columns are ignored."""
    expected_db = sqlite3.connect(":memory:")
    try:
        for sql in STATEMENTS:
            expected_db.execute(sql)
        expected = _columns(expected_db)
    finally:
        expected_db.close()
    actual = _columns(conn)
    missing_tables: List[str] = []
    missing_columns: List[str] = []
    for table, cols in expected.items():
        if table not in actual:
            if table not in LAZY_TABLES and not _is_fts_shadow(table, expected):
                missing_tables.append(f"table {table}")
            continue
        missing_columns += [f"column {table}.{c}" for c in cols
                            if c not in actual[table] and (table, c) not in OPTIONAL_COLUMNS]
    return missing_tables + missing_columns


def _is_fts_shadow(table: str, tables: Dict[str, List[str]]) -> bool:
    base, _, suffix = table.rpartition("_")
    return suffix in ("data", "idx", "content", "docsize", "config") and base.endswith("_fts") and base in tables
