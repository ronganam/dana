-- Dana 9.0 database: schema + synthetic sample rows (no personal data), at migration 33.
-- Made from db_8_1_0.sql (the 8.1.0 fixture) by Dana 9.0's migrations 2-33 (removed since:
-- addon migrations squashed into baselines), first admin from testenv.OWNER_ENV. Represents a
-- production DB of 9.0 before the per-addon migrations. Load with sqlite3.Connection.executescript().
-- Do not regenerate: the code that produced it no longer exists.
PRAGMA foreign_keys=OFF;
BEGIN;
CREATE TABLE memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                priority TEXT DEFAULT 'medium',
                due_date TEXT,
                status TEXT DEFAULT 'needs_action',
                triggers TEXT DEFAULT '{}',
                group_id TEXT REFERENCES task_groups(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , snooze_count INTEGER NOT NULL DEFAULT 0, project_id TEXT REFERENCES projects(id), user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, created_by TEXT NOT NULL DEFAULT '');
CREATE TABLE phone_calls (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                caller TEXT DEFAULT 'Unknown',
                receiver TEXT DEFAULT 'Me',
                call_timestamp TEXT NOT NULL,
                transcript TEXT,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , title TEXT, duration_seconds INTEGER DEFAULT 0, labels TEXT NOT NULL DEFAULT '[]', analysis TEXT, importance TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'ready', error TEXT, audio_sha256 TEXT, source_filename TEXT, direction TEXT, phone_number TEXT, project_id TEXT, mime_type TEXT, instructions TEXT, audio_deleted_at TEXT, person_id TEXT, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '');
CREATE TABLE call_suggestions (
                id TEXT PRIMARY KEY,
                call_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'task',
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                result_ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            , source_type TEXT NOT NULL DEFAULT 'call', user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '');
CREATE VIRTUAL TABLE notifications_fts USING fts5(
                id UNINDEXED, app_name, title, text, sender, conversation, tokenize='unicode61'
            );
CREATE TABLE notification_action_requests (
                id TEXT PRIMARY KEY,
                notification_id TEXT NOT NULL,
                device_key TEXT,
                action TEXT NOT NULL,
                text TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TEXT NOT NULL,
                done_at TEXT
            , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '');
CREATE TABLE phone_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '');
CREATE TABLE task_links (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                ref_type TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'origin',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, ref_type, ref_id)
            );
CREATE TABLE people (
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
            , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, emails TEXT NOT NULL DEFAULT '[]', contact_resource TEXT NOT NULL DEFAULT '');
CREATE TABLE weekly_reviews (
                id TEXT PRIMARY KEY,
                week TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                headline TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                result TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            , user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE DEFAULT '');
CREATE VIRTUAL TABLE phone_calls_fts USING fts5(
                id UNINDEXED,
                caller,
                receiver,
                title,
                summary,
                transcript,
                tokenize='unicode61'
            );
CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                icon TEXT DEFAULT '📁',
                color TEXT DEFAULT '#6366f1',
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, target_date TEXT, budget REAL);
CREATE TABLE entries (
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
            , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE, created_by TEXT NOT NULL DEFAULT '', url_set_by TEXT, section_id TEXT REFERENCES project_sections(id) ON DELETE SET NULL, decision TEXT NOT NULL DEFAULT '');
CREATE VIRTUAL TABLE entries_fts USING fts5(
                id UNINDEXED,
                project_id UNINDEXED,
                title,
                body,
                summary,
                transcript,
                tags,
                fields,
                tokenize='unicode61'
            );
CREATE TABLE about_facts (
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
            , user_id TEXT NOT NULL DEFAULT '' REFERENCES users(id) ON DELETE CASCADE);
CREATE TABLE usage_events (
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
                , user_id TEXT NOT NULL DEFAULT '');
CREATE TABLE system_marks (
        key TEXT PRIMARY KEY, last_at TEXT, ok INTEGER, last_ok_at TEXT, last_fail_at TEXT, detail TEXT);
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        access TEXT NOT NULL,
        telegram_chat_id INTEGER,
        created_at TEXT NOT NULL
    , context TEXT NOT NULL DEFAULT '', pronoun TEXT NOT NULL DEFAULT 'they' CHECK (pronoun IN ('he', 'she', 'they')), other_names TEXT NOT NULL DEFAULT '', prompt_name TEXT NOT NULL DEFAULT '');
CREATE TABLE user_tokens (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        label TEXT NOT NULL DEFAULT '',
        token_hash TEXT NOT NULL UNIQUE,
        session_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_used_at TEXT
    );
CREATE TABLE user_telegram (
        telegram_user_id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    );
CREATE TABLE "sessions" (
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
);
CREATE TABLE "thread_messages" (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    ts REAL NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL
);
CREATE TABLE "shortcuts" (
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
);
CREATE TABLE "usage_daily" (
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
);
CREATE TABLE integrations (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        credentials TEXT,
        settings TEXT NOT NULL DEFAULT '{}',
        account TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, account_conflict TEXT,
        PRIMARY KEY (user_id, kind)
    );
CREATE TABLE integration_imports (
        kind TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        imported_at TEXT NOT NULL
    );
CREATE TABLE "task_groups" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '📁',
    color TEXT DEFAULT '#6366f1',
    description TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, name)
);
CREATE TABLE task_trigger_marks (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    );
CREATE TABLE item_access (
        item_type TEXT NOT NULL,
        item_id TEXT NOT NULL,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        level TEXT NOT NULL CHECK (level IN ('read', 'edit')),
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        PRIMARY KEY (item_type, item_id, user_id)
    );
CREATE TABLE shopping_lists (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, version INTEGER NOT NULL DEFAULT 0,
        UNIQUE (user_id, name)
    );
CREATE TABLE "about_state" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE "person_phones" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    phone TEXT NOT NULL,
    person_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, phone)
);
CREATE TABLE whatsapp_contacts (
        id INTEGER PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        category TEXT,
        updated_at TEXT,
        UNIQUE (user_id, key)
    );
CREATE TABLE uploaded_images (
        name TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL
    );
CREATE TABLE integration_import_settings (
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (kind, name)
    );
CREATE TABLE notification_digest_state (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        slot TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (user_id, slot)
    );
CREATE TABLE "notifications" (
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
);
CREATE TABLE "notification_rules" (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, package, sender)
);
CREATE TABLE "notification_summaries" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    signature TEXT NOT NULL,
    summary TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scope)
);
CREATE TABLE "notification_rule_declines" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    declined_at TEXT NOT NULL,
    PRIMARY KEY (user_id, package)
);
CREATE TABLE "now_hidden" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,
    until TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
CREATE TABLE "briefings" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    headline TEXT NOT NULL,
    points TEXT NOT NULL DEFAULT '[]',
    data TEXT NOT NULL DEFAULT '{}',
    trigger TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE "wake_log" (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    at TEXT NOT NULL,
    why TEXT,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE "phone_devices" (
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
    );
CREATE TABLE "shopping_items" (
        id TEXT PRIMARY KEY,
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL,
        quantity TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'other',
        checked INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT,
        checked_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        added_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        seq INTEGER NOT NULL DEFAULT 0,
        rev INTEGER NOT NULL DEFAULT 0
    );
CREATE TABLE shopping_history (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name_key TEXT NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other',
        times INTEGER NOT NULL DEFAULT 0,
        last_at TEXT NOT NULL,
        PRIMARY KEY (list_id, name_key)
    );
CREATE TABLE shopping_aisle_choices (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name_key TEXT NOT NULL,
        category TEXT NOT NULL,
        PRIMARY KEY (list_id, name_key)
    );
CREATE TABLE shopping_aisles (
        name_key TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
CREATE TABLE shopping_invite_answers (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        answer TEXT NOT NULL,
        answered_at TEXT NOT NULL,
        PRIMARY KEY (list_id, user_id)
    );
CREATE TABLE agent_sessions (
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
    );
CREATE TABLE google_contacts (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        resource_name TEXT NOT NULL,
        etag TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        other_names TEXT NOT NULL DEFAULT '[]',
        phones TEXT NOT NULL DEFAULT '[]',
        emails TEXT NOT NULL DEFAULT '[]',
        birthday TEXT NOT NULL DEFAULT '',
        organisation TEXT NOT NULL DEFAULT '',
        job_title TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, resource_name)
    );
CREATE TABLE google_contact_phones (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        phone TEXT NOT NULL,
        resource_name TEXT NOT NULL,
        PRIMARY KEY (user_id, phone, resource_name)
    );
CREATE TABLE google_contacts_sync (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        sync_token TEXT NOT NULL DEFAULT '',
        synced_at TEXT
    );
CREATE TABLE money_sources (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        company TEXT NOT NULL,
        label TEXT,
        credentials TEXT,
        state TEXT NOT NULL DEFAULT 'new',
        state_reason TEXT,
        error_type TEXT,
        failures INTEGER NOT NULL DEFAULT 0,
        last_sync_at TEXT,
        last_ok_at TEXT,
        next_sync_at TEXT,
        signal_syncs_day TEXT,
        signal_syncs INTEGER NOT NULL DEFAULT 0,
        last_manual_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    , device TEXT);
CREATE TABLE money_transactions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT REFERENCES money_accounts(id) ON DELETE CASCADE,
        ext_key TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('scrape', 'notification')),
        status TEXT NOT NULL CHECK (status IN ('provisional', 'pending', 'completed', 'unmatched')),
        occurred_at TEXT,
        purchase_date TEXT NOT NULL,
        charge_date TEXT,
        amount INTEGER NOT NULL,
        orig_amount INTEGER,
        orig_currency TEXT,
        description TEXT,
        merchant TEXT,
        merchant_key TEXT,
        memo TEXT,
        category TEXT,
        category_source TEXT CHECK (category_source IN ('bank', 'memory', 'model', 'rule', 'user')),
        flow TEXT,
        installment_number INTEGER,
        installment_total INTEGER,
        notification_id TEXT,
        note TEXT,
        raw TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, one_off INTEGER, yearly INTEGER,
        UNIQUE (account_id, ext_key)
    );
CREATE VIRTUAL TABLE money_transactions_fts USING fts5(
        id UNINDEXED, merchant, description, memo, note, tokenize='unicode61'
    );
CREATE TABLE money_rules (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        merchant_key TEXT NOT NULL,
        category TEXT,
        merchant TEXT,
        flow TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (user_id, merchant_key)
    );
CREATE TABLE money_aliases (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        alias_key TEXT NOT NULL,
        merchant_key TEXT NOT NULL,
        merchant TEXT,
        category TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, alias_key)
    );
CREATE TABLE money_syncs (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_id TEXT REFERENCES money_sources(id) ON DELETE CASCADE,
        trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'signal', 'manual', 'connect', 'agent')),
        state TEXT NOT NULL CHECK (state IN ('running', 'needs_code', 'done', 'failed', 'cancelled')),
        error_type TEXT,
        error TEXT,
        added INTEGER,
        updated INTEGER,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );
CREATE TABLE money_code_requests (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_id TEXT,
        sync_id TEXT,
        company TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'expired', 'cancelled')),
        answered_by TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        answered_at TEXT
    );
CREATE TABLE money_signals (
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
    );
CREATE TABLE money_holdings (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        security TEXT NOT NULL,
        name TEXT,
        quantity REAL,
        price REAL,
        currency TEXT,
        value INTEGER,
        cost INTEGER,
        profit INTEGER,
        profit_pct REAL,
        weight REAL,
        raw TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE (account_id, security)
    );
CREATE TABLE money_account_values (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        value INTEGER NOT NULL,
        PRIMARY KEY (account_id, date)
    );
CREATE TABLE money_trades (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        ext_key TEXT NOT NULL,
        date TEXT NOT NULL,
        action TEXT,
        kind TEXT NOT NULL,
        security TEXT,
        name TEXT,
        quantity REAL,
        price REAL,
        currency TEXT,
        amount INTEGER,
        commission INTEGER,
        tax INTEGER,
        raw TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (account_id, ext_key)
    );
CREATE TABLE money_savings (
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
    );
CREATE TABLE money_market_checks (
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
    );
CREATE TABLE "money_accounts" (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_id TEXT REFERENCES money_sources(id) ON DELETE SET NULL,
        company TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('bank', 'card', 'investment', 'meal')),
        number TEXT NOT NULL,
        label TEXT,
        balance INTEGER,
        balance_at TEXT,
        next_charge INTEGER,
        next_charge_date TEXT,
        billed_from_account_id TEXT,
        hidden INTEGER NOT NULL DEFAULT 0,
        details TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (user_id, company, number)
    );
CREATE TABLE money_meals (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        ext_key TEXT NOT NULL,
        date TEXT NOT NULL,
        occurred_at TEXT,
        place TEXT,
        place_id TEXT,
        order_type TEXT,
        total INTEGER NOT NULL,
        budget_paid INTEGER NOT NULL,
        card_paid INTEGER NOT NULL,
        discount INTEGER,
        active INTEGER NOT NULL DEFAULT 1,
        budget_name TEXT,
        raw TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (account_id, ext_key)
    );
CREATE TABLE project_sections (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'notes',
        columns TEXT NOT NULL DEFAULT '[]',
        summary TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
CREATE TABLE entry_files (
        id TEXT PRIMARY KEY,
        entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        mime TEXT NOT NULL DEFAULT 'application/octet-stream',
        size INTEGER NOT NULL DEFAULT 0,
        stored TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        transcript TEXT,
        source TEXT NOT NULL DEFAULT 'upload',
        created_by TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
INSERT INTO memories (key, value, category, updated_at) VALUES ('last_location_zone', 'home', 'system', '2026-11-01 08:00:00');
INSERT INTO memories (key, value, category, updated_at) VALUES ('favorite_color', 'blue', 'preferences', '2026-11-01 08:00:00');
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id, user_id, created_by) VALUES ('t1', 'Water the plants', '', 'medium', '2026-11-02', 'needs_action', '{"time": "2026-11-02T07:00:00+02:00"}', 'home', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 0, NULL, 'fb327d6befa6', 'fb327d6befa6');
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id, user_id, created_by) VALUES ('t2', 'Buy seeds', 'From the garden shop', 'high', NULL, 'completed', '{}', 'general', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 2, 'p1', 'fb327d6befa6', 'fb327d6befa6');
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id, user_id, created_by) VALUES ('t3', 'Call the plumber', NULL, 'medium', NULL, 'needs_action', '{"location_arrive": "zone.home"}', 'home', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 0, NULL, 'fb327d6befa6', 'fb327d6befa6');
INSERT INTO phone_calls (id, filename, caller, receiver, call_timestamp, transcript, summary, created_at, title, duration_seconds, labels, analysis, importance, status, error, audio_sha256, source_filename, direction, phone_number, project_id, mime_type, instructions, audio_deleted_at, person_id, user_id) VALUES ('c1', '11111111-2222-3333-4444-555555555555_call.m4a', 'Sam Example', 'Me', '2026-10-31T17:30:00+02:00', 'Sam: can you bring the ladder on Friday?', 'Sam asked to borrow the ladder on Friday.', '2026-11-01 08:00:00', 'Call with Sam Example', 95, '["personal"]', '{"summary": "Ladder on Friday", "action_items": ["Bring the ladder"]}', 'normal', 'ready', NULL, 'deadbeef', 'call.m4a', 'incoming', '+15550100001', NULL, 'audio/mp4', NULL, NULL, 'pe1', 'fb327d6befa6');
INSERT INTO call_suggestions (id, call_id, kind, payload, status, result_ref, created_at, resolved_at, source_type, user_id) VALUES ('cs1', 'c1', 'task', '{"title": "Bring the ladder to Sam", "confidence": 0.9}', 'pending', NULL, '2026-11-01 08:00:00', NULL, 'call', 'fb327d6befa6');
INSERT INTO call_suggestions (id, call_id, kind, payload, status, result_ref, created_at, resolved_at, source_type, user_id) VALUES ('cs2', 'n1', 'reply', '{"text": "Yes, see you Friday"}', 'pending', NULL, '2026-11-01 08:00:00', NULL, 'notification', 'fb327d6befa6');
INSERT INTO notifications_fts (id, app_name, title, text, sender, conversation) VALUES ('n1', 'Chat', 'Sam Example', 'Are we still on for Friday?', 'Sam Example', NULL);
INSERT INTO notifications_fts (id, app_name, title, text, sender, conversation) VALUES ('n2', 'Shop', 'Sale', '20% off today', NULL, NULL);
INSERT INTO notification_action_requests (id, notification_id, device_key, action, text, status, error, created_at, done_at, user_id) VALUES ('ar1', 'n1', '0|com.example.chat|1|null|10001', 'reply', 'Yes', 'done', NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', 'fb327d6befa6');
INSERT INTO phone_events (seq, type, data, created_at, expires_at, user_id) VALUES (1, 'notification_sync', '{"ids": ["n1"]}', '2026-11-01T08:00:00+00:00', '2026-11-02T08:00:00+00:00', 'fb327d6befa6');
INSERT INTO task_links (id, task_id, ref_type, ref_id, role, created_at) VALUES ('tl1', 't3', 'call', 'c1', 'origin', '2026-11-01 08:00:00');
INSERT INTO people (id, name, aliases, relation, notes, birthday, pinned, hidden, source, brief, brief_signature, brief_updated_at, created_at, updated_at, user_id, emails, contact_resource) VALUES ('pe1', 'Sam Example', '["Sammy"]', 'friend', 'Likes hiking', '03-14', 1, 0, 'manual', NULL, NULL, NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', 'fb327d6befa6', '[]', '');
INSERT INTO weekly_reviews (id, week, status, headline, data, result, created_at, resolved_at, user_id) VALUES ('wr1', '2026-W44', 'applied', 'Two things to tidy up', '{"proposals": []}', '{"applied": 0}', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', 'fb327d6befa6');
INSERT INTO phone_calls_fts (id, caller, receiver, title, summary, transcript) VALUES ('c1', 'Sam Example', 'Me', 'Call with Sam Example', 'Sam asked to borrow the ladder on Friday.', 'Sam: can you bring the ladder on Friday?');
INSERT INTO projects (id, title, description, icon, color, tags, status, created_at, updated_at, user_id, target_date, budget) VALUES ('p1', 'Garden plan', 'Spring garden ideas', '🌱', '#10b981', '["garden"]', 'active', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 'fb327d6befa6', NULL, NULL);
INSERT INTO entries (id, project_id, kind, title, body, fields, status, priority, due_at, tags, url, media_path, transcript, summary, comments, source_author, created_at, updated_at, user_id, created_by, url_set_by, section_id, decision) VALUES ('e1', 'p1', 'note', 'Tomato varieties', '## Options
- Cherry
- Roma', '[]', 'open', 'medium', NULL, '["plants"]', NULL, NULL, NULL, NULL, '[]', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', 'fb327d6befa6', 'fb327d6befa6', 'fb327d6befa6', NULL, '');
INSERT INTO entries (id, project_id, kind, title, body, fields, status, priority, due_at, tags, url, media_path, transcript, summary, comments, source_author, created_at, updated_at, user_id, created_by, url_set_by, section_id, decision) VALUES ('e2', 'p1', 'link', 'Raised beds video', '', '[{"name": "Cost", "type": "text", "value": "200"}]', 'open', 'low', NULL, '[]', 'https://example.com/v/1', NULL, NULL, 'How to build a raised bed', '[]', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', 'fb327d6befa6', 'fb327d6befa6', 'fb327d6befa6', NULL, '');
INSERT INTO entries_fts (id, project_id, title, body, summary, transcript, tags, fields) VALUES ('e1', 'p1', 'Tomato varieties', '## Options
- Cherry
- Roma', NULL, NULL, '["plants"]', '[]');
INSERT INTO entries_fts (id, project_id, title, body, summary, transcript, tags, fields) VALUES ('e2', 'p1', 'Raised beds video', '', 'How to build a raised bed', NULL, '[]', '[{"name": "Cost", "type": "text", "value": "200"}]');
INSERT INTO about_facts (id, section, text, status, source_kind, source_ref, source_label, legacy_key, created_at, updated_at, confirmed_at, inactive_at, user_id) VALUES ('f1', 'people', 'Sam is a friend from the hiking club', 'active', 'owner', NULL, NULL, NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', NULL, 'fb327d6befa6');
INSERT INTO about_facts (id, section, text, status, source_kind, source_ref, source_label, legacy_key, created_at, updated_at, confirmed_at, inactive_at, user_id) VALUES ('f2', 'other', 'Favorite color: blue', 'active', 'imported', 'favorite_color', NULL, 'favorite_color', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', NULL, 'fb327d6befa6');
INSERT INTO usage_events (id, ts, day, kind, purpose, source, ref, provider, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error, user_id) VALUES (1, 1761984000.0, '2026-11-01', 'chat', 'chat', 'web', 'session:s_web_1', 'gemini', 'gemini/gemini-flash-lite-latest', 1200, 40, 800, 0, NULL, 0.00021, 900, 1, '', 'fb327d6befa6');
INSERT INTO usage_events (id, ts, day, kind, purpose, source, ref, provider, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error, user_id) VALUES (2, 1761984100.0, '2026-11-01', 'transcribe', 'call_transcribe', 'phone', 'call:c1', 'openai', 'whisper-1', 0, 0, 0, 0, 95.0, 0.0095, 4000, 1, '', 'fb327d6befa6');
INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES ('push:fb327d6befa6', '2026-11-01T08:00:00+00:00', 1, '2026-11-01T08:00:00+00:00', NULL, '');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'baseline', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (2, 'users', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (3, 'user_scope', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (4, 'integrations', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (5, 'tasks_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (6, 'item_access', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (7, 'shopping_lists', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (8, 'knowledge_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (9, 'about_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (10, 'people_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (11, 'whatsapp_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (12, 'calendar_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (13, 'phone_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (14, 'calls_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (15, 'composers_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (16, 'user_profile', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (17, 'phone_devices_per_user', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (18, 'entry_url_set_by', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (19, 'whatsapp_account_claims', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (20, 'shopping_remake', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (21, 'agent_sessions', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (22, 'agent_session_open_url', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (23, 'google_contacts', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (24, 'agent_session_parents', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (25, 'money', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (26, 'money_signals', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (27, 'money_dashboards', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (28, 'money_portfolio', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (29, 'money_savings', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (30, 'money_market', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (31, 'money_meals', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (32, 'knowledge_sections', '2026-11-21T12:28:20+00:00');
INSERT INTO schema_migrations (version, name, applied_at) VALUES (33, 'thread_cleared', '2026-11-21T12:28:20+00:00');
INSERT INTO users (id, name, access, telegram_chat_id, created_at, context, pronoun, other_names, prompt_name) VALUES ('fb327d6befa6', 'Robin Levi', 'admin', NULL, '2026-11-21T12:28:20.801344+00:00', 'Robin (רובין) lives in Israel and works in tech. Gal (גל, phone contact ''גל כהן 🚀❤️🔥'') is Robin''s partner; they are planning their wedding. ''אמא'' is Robin''s mother. נועה is Robin''s sibling. Pepper (פפר) is Robin and Gal''s dog.', 'he', 'רובין', 'the owner');
INSERT INTO sessions (user_id, session_id, history, pending_approval, status, title, debug_info, created_at, updated_at, context, cleared_seq) VALUES ('fb327d6befa6', 's_web_1', '[{"role": "user", "content": "What''s on my list?"}, {"role": "assistant", "content": "Water the plants."}]', NULL, 'idle', 'My list', '{}', '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, 0);
INSERT INTO sessions (user_id, session_id, history, pending_approval, status, title, debug_info, created_at, updated_at, context, cleared_seq) VALUES ('fb327d6befa6', 'main', '[]', NULL, 'idle', 'Dana', '{}', '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, 0);
INSERT INTO thread_messages (seq, user_id, thread_id, ts, device, message) VALUES (1, 'fb327d6befa6', 'main', 1761984000.0, 'phone', '{"role": "user", "content": "Open the gate"}');
INSERT INTO thread_messages (seq, user_id, thread_id, ts, device, message) VALUES (2, 'fb327d6befa6', 'main', 1761984002.0, '', '{"role": "assistant", "content": "Opened."}');
INSERT INTO shortcuts (id, user_id, phrase_norm, phrase, tool, args, reply, streak, active, uses, last_used, learned_at, created_at, updated_at) VALUES ('sc1', 'fb327d6befa6', 'open the gate', 'Open the gate', 'call_ha_service', '{"domain": "cover", "service": "open_cover", "entity_id": "cover.gate"}', 'Opened.', 3, 1, 4, 1761984000.0, 1761980000.0, 1761970000.0, 1761984000.0);
INSERT INTO usage_daily (day, user_id, kind, purpose, source, model, calls, failures, input_tokens, output_tokens, cached_tokens, cost, unpriced, ms) VALUES ('2026-08-01', 'fb327d6befa6', 'chat', 'chat', 'web', 'gemini/gemini-flash-lite-latest', 10, 0, 12000, 400, 8000, 0.0021, 0, 9000);
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('general', 'fb327d6befa6', 'General', '📁', '#6366f1', 'General tasks', 0, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('home', 'fb327d6befa6', 'Home', '🏠', '#10b981', 'Home and family tasks', 1, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('work', 'fb327d6befa6', 'Work', '💼', '#3b82f6', 'Work and office tasks', 2, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('shopping', 'fb327d6befa6', 'Shopping', '🛒', '#f59e0b', 'Shopping and errands', 3, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('health', 'fb327d6befa6', 'Health', '❤️', '#ef4444', 'Health and wellness', 4, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, user_id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('personal', 'fb327d6befa6', 'Personal', '👤', '#a855f7', 'Personal tasks', 5, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO shopping_lists (id, user_id, name, created_at, updated_at, version) VALUES ('2023b8bf4a88', 'fb327d6befa6', 'Shopping list', '2026-11-21 12:28:20', '2026-11-21 12:28:20', 0);
INSERT INTO about_state (user_id, key, value) VALUES ('fb327d6befa6', 'learn_bookmark', '{"calls": "2026-11-01T08:00:00+00:00"}');
INSERT INTO about_state (user_id, key, value) VALUES ('fb327d6befa6', 'legacy_memories', 'true');
INSERT INTO person_phones (user_id, phone, person_id, created_at) VALUES ('fb327d6befa6', '15550100001', 'pe1', '2026-11-01T08:00:00+00:00');
INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sub_text, sender, conversation, is_group, android_category, channel, posted_at, content_hash, status, removed_reason, removed_at, triage_status, category, priority, priority_score, bucket, triage, action_status, escalated_at, created_at, updated_at, snoozed_until, seen_at, nudges, actions, person_id) VALUES ('n1', 'fb327d6befa6', '0|com.example.chat|1|null|10001', 'com.example.chat', 'Chat', 'Sam Example', 'Are we still on for Friday?', NULL, 'Sam Example', NULL, 0, NULL, NULL, '2026-11-01T08:00:00+00:00', 'h1', 'active', NULL, NULL, 'done', 'personal', 'high', NULL, 'needs_you', '{"reason": "question from a friend"}', 'none', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, NULL, 0, '[{"title": "Reply", "reply": true}]', 'pe1');
INSERT INTO notifications (id, user_id, device_key, package, app_name, title, text, sub_text, sender, conversation, is_group, android_category, channel, posted_at, content_hash, status, removed_reason, removed_at, triage_status, category, priority, priority_score, bucket, triage, action_status, escalated_at, created_at, updated_at, snoozed_until, seen_at, nudges, actions, person_id) VALUES ('n2', 'fb327d6befa6', '0|com.example.shop|2|null|10001', 'com.example.shop', 'Shop', 'Sale', '20% off today', NULL, NULL, NULL, 0, NULL, NULL, '2026-11-01T08:00:00+00:00', 'h2', 'removed', 'dismissed', '2026-11-01T08:00:00+00:00', 'done', NULL, NULL, NULL, 'quiet', NULL, 'none', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, NULL, 0, NULL, NULL);
INSERT INTO notification_rules (id, user_id, package, sender, action, created_at) VALUES ('r1', 'fb327d6befa6', 'com.example.shop', '', 'mute', '2026-11-01 08:00:00');
INSERT INTO notification_summaries (user_id, scope, signature, summary, count, updated_at) VALUES ('fb327d6befa6', 'com.example.chat', 'sig1', 'Sam asked about Friday.', 1, '2026-11-01T08:00:00+00:00');
INSERT INTO notification_rule_declines (user_id, package, declined_at) VALUES ('fb327d6befa6', 'com.example.news', '2026-11-01T08:00:00+00:00');
INSERT INTO now_hidden (user_id, item_id, until, reason, created_at) VALUES ('fb327d6befa6', 'check:calls', '2026-11-03T08:00:00+00:00', 'snooze', '2026-11-01T08:00:00+00:00');
INSERT INTO briefings (user_id, date, headline, points, data, trigger, created_at, delivered_at) VALUES ('fb327d6befa6', '2026-11-01', 'Quiet morning', '["Water the plants"]', '{}', 'wake', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00');
INSERT INTO wake_log (user_id, date, at, why) VALUES ('fb327d6befa6', '2026-11-01', '2026-11-01T08:00:00+00:00', 'phone alarm');
INSERT INTO phone_devices (user_id, device_id, token, name, app_version, created_at, updated_at, last_fetch_at, last_seq) VALUES ('fb327d6befa6', 'dev1', 'fcm-token-sample', 'Test phone', '8.1.0', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', 1);
INSERT INTO shopping_items (id, list_id, name, name_key, quantity, note, category, checked, checked_at, checked_by, added_by, created_at, seq, rev) VALUES ('s1', '2023b8bf4a88', 'Milk', 'milk', '', '', 'other', 0, NULL, NULL, 'fb327d6befa6', '2026-11-01T08:00:00+00:00', 1, 0);
INSERT INTO shopping_items (id, list_id, name, name_key, quantity, note, category, checked, checked_at, checked_by, added_by, created_at, seq, rev) VALUES ('s2', '2023b8bf4a88', 'Bread', 'bread', '', '', 'other', 1, '2026-11-21T12:28:20+00:00', NULL, 'fb327d6befa6', '2026-11-01T08:00:00+00:00', 2, 0);
DELETE FROM sqlite_sequence;
INSERT INTO sqlite_sequence (name, seq) VALUES ('phone_events', 1);
INSERT INTO sqlite_sequence (name, seq) VALUES ('usage_events', 2);
INSERT INTO sqlite_sequence (name, seq) VALUES ('thread_messages', 2);
CREATE INDEX idx_phone_calls_sha ON phone_calls(audio_sha256);
CREATE INDEX idx_phone_calls_source ON phone_calls(source_filename);
CREATE INDEX idx_call_suggestions_status ON call_suggestions(status, created_at);
CREATE INDEX idx_call_suggestions_call ON call_suggestions(call_id);
CREATE INDEX idx_call_suggestions_source ON call_suggestions(source_type, status);
CREATE INDEX idx_task_links_ref ON task_links(ref_type, ref_id);
CREATE INDEX idx_phone_calls_person ON phone_calls(person_id);
CREATE INDEX idx_weekly_reviews_week ON weekly_reviews(week);
CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
                VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
            END;
CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
                DELETE FROM entries_fts WHERE id = old.id;
            END;
CREATE INDEX idx_about_facts_status ON about_facts(status, section);
CREATE TRIGGER phone_calls_ai AFTER INSERT ON phone_calls BEGIN
            INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
            VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
        END;
CREATE TRIGGER phone_calls_ad AFTER DELETE ON phone_calls BEGIN
            DELETE FROM phone_calls_fts WHERE id = old.id;
        END;
CREATE TRIGGER phone_calls_au AFTER UPDATE OF caller, receiver, title, summary, transcript ON phone_calls BEGIN
            DELETE FROM phone_calls_fts WHERE id = old.id;
            INSERT INTO phone_calls_fts(id, caller, receiver, title, summary, transcript)
            VALUES (new.id, new.caller, new.receiver, new.title, new.summary, new.transcript);
        END;
CREATE INDEX idx_usage_events_day ON usage_events(day);
CREATE INDEX idx_usage_events_ref ON usage_events(ref) WHERE ref != '';
CREATE INDEX idx_user_tokens_user ON user_tokens(user_id);
CREATE INDEX idx_user_telegram_user ON user_telegram(user_id);
CREATE INDEX idx_thread_messages_user_thread ON thread_messages(user_id, thread_id, seq);
CREATE INDEX idx_usage_events_user_day ON usage_events(user_id, day);
CREATE INDEX idx_integrations_kind_account ON integrations(kind, account);
CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_group ON tasks(group_id);
CREATE INDEX idx_tasks_created_by ON tasks(created_by, status);
CREATE INDEX idx_item_access_user ON item_access(user_id, item_type);
CREATE INDEX idx_projects_user ON projects(user_id, status);
CREATE INDEX idx_entries_user ON entries(user_id);
CREATE INDEX idx_entries_project ON entries(project_id);
CREATE INDEX idx_about_facts_user ON about_facts(user_id, status);
CREATE INDEX idx_people_user ON people(user_id);
CREATE INDEX idx_person_phones_person ON person_phones(person_id);
CREATE INDEX idx_uploaded_images_user ON uploaded_images(user_id);
CREATE INDEX idx_phone_events_user ON phone_events(user_id, seq);
CREATE INDEX idx_notifications_status ON notifications(status, posted_at);
CREATE INDEX idx_notifications_key ON notifications(device_key);
CREATE INDEX idx_notifications_package ON notifications(package, posted_at);
CREATE INDEX idx_notifications_triage ON notifications(triage_status);
CREATE INDEX idx_notifications_person ON notifications(person_id);
CREATE INDEX idx_notifications_user ON notifications(user_id, status, posted_at);
CREATE TRIGGER notifications_fts_ai AFTER INSERT ON notifications BEGIN
        INSERT INTO notifications_fts(id, app_name, title, text, sender, conversation)
        VALUES (new.id, new.app_name, new.title, new.text, new.sender, new.conversation);
    END;
CREATE TRIGGER notifications_fts_ad AFTER DELETE ON notifications BEGIN
        DELETE FROM notifications_fts WHERE id = old.id;
    END;
CREATE INDEX idx_phone_calls_user ON phone_calls(user_id, call_timestamp);
CREATE INDEX idx_call_suggestions_user ON call_suggestions(user_id, status);
CREATE INDEX idx_weekly_reviews_user ON weekly_reviews(user_id, week);
CREATE INDEX idx_phone_devices_user ON phone_devices(user_id);
CREATE INDEX idx_phone_devices_token ON phone_devices(token);
CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
                DELETE FROM entries_fts WHERE id = old.id;
                INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
                VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
            END;
CREATE INDEX idx_shopping_items_list ON shopping_items(list_id, checked, category, seq);
CREATE INDEX idx_shopping_items_key ON shopping_items(list_id, name_key);
CREATE INDEX idx_google_contact_phones_contact ON google_contact_phones(user_id, resource_name);
CREATE INDEX idx_money_sources_user ON money_sources(user_id);
CREATE INDEX idx_money_transactions_date ON money_transactions(user_id, purchase_date);
CREATE INDEX idx_money_transactions_merchant ON money_transactions(user_id, merchant_key);
CREATE INDEX idx_money_transactions_category ON money_transactions(user_id, category);
CREATE TRIGGER money_transactions_fts_ai AFTER INSERT ON money_transactions BEGIN
        INSERT INTO money_transactions_fts(id, merchant, description, memo, note)
        VALUES (new.id, new.merchant, new.description, new.memo, new.note);
    END;
CREATE TRIGGER money_transactions_fts_au AFTER UPDATE OF merchant, description, memo, note
        ON money_transactions BEGIN
        DELETE FROM money_transactions_fts WHERE id = old.id;
        INSERT INTO money_transactions_fts(id, merchant, description, memo, note)
        VALUES (new.id, new.merchant, new.description, new.memo, new.note);
    END;
CREATE TRIGGER money_transactions_fts_ad AFTER DELETE ON money_transactions BEGIN
        DELETE FROM money_transactions_fts WHERE id = old.id;
    END;
CREATE INDEX idx_money_syncs_source ON money_syncs(user_id, source_id, started_at);
CREATE INDEX idx_money_code_requests_user ON money_code_requests(user_id, status);
CREATE UNIQUE INDEX idx_money_signals_notification ON money_signals(user_id, notification_id) WHERE notification_id IS NOT NULL;
CREATE UNIQUE INDEX idx_money_signals_push ON money_signals(user_id, key, text) WHERE source = 'phone_app';
CREATE INDEX idx_money_signals_created ON money_signals(user_id, created_at);
CREATE INDEX idx_money_signals_transaction ON money_signals(transaction_id);
CREATE INDEX idx_money_holdings_user ON money_holdings(user_id);
CREATE INDEX idx_money_account_values_user ON money_account_values(user_id, date);
CREATE INDEX idx_money_trades_date ON money_trades(user_id, date);
CREATE INDEX idx_money_accounts_source ON money_accounts(source_id);
CREATE INDEX idx_money_meals_date ON money_meals(user_id, date);
CREATE INDEX idx_sections_project ON project_sections(project_id, position);
CREATE INDEX idx_entries_section ON entries(section_id);
CREATE INDEX idx_entry_files_entry ON entry_files(entry_id);
COMMIT;
