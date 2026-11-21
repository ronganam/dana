-- Dana 8.1.0 database: schema + synthetic sample rows (no personal data).
-- Made from a fresh temp DB by the 8.1.0 code (skills/memory.py::init_db, the skills,
-- and every module's own tables); represents the production DB before migration.
-- Load with sqlite3.Connection.executescript(). Do not regenerate: the code that
-- produced it no longer exists after the multi-user phase 1.
PRAGMA foreign_keys=OFF;
BEGIN;
CREATE TABLE memories (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE task_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                emoji TEXT DEFAULT '📁',
                color TEXT DEFAULT '#6366f1',
                description TEXT DEFAULT '',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
            , snooze_count INTEGER NOT NULL DEFAULT 0, project_id TEXT REFERENCES projects(id));
CREATE TABLE phone_calls (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                caller TEXT DEFAULT 'Unknown',
                receiver TEXT DEFAULT 'Me',
                call_timestamp TEXT NOT NULL,
                transcript TEXT,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , title TEXT, duration_seconds INTEGER DEFAULT 0, labels TEXT NOT NULL DEFAULT '[]', analysis TEXT, importance TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'ready', error TEXT, audio_sha256 TEXT, source_filename TEXT, direction TEXT, phone_number TEXT, project_id TEXT, mime_type TEXT, instructions TEXT, audio_deleted_at TEXT, person_id TEXT);
CREATE TABLE call_suggestions (
                id TEXT PRIMARY KEY,
                call_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'task',
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                result_ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            , source_type TEXT NOT NULL DEFAULT 'call');
CREATE TABLE notifications (
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            , snoozed_until TEXT, seen_at TEXT, nudges INTEGER DEFAULT 0, actions TEXT, person_id TEXT);
CREATE VIRTUAL TABLE notifications_fts USING fts5(
                id UNINDEXED, app_name, title, text, sender, conversation, tokenize='unicode61'
            );
CREATE TABLE notification_rules (
                id TEXT PRIMARY KEY,
                package TEXT NOT NULL,
                sender TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(package, sender)
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
            );
CREATE TABLE phone_devices (
                device_id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                name TEXT,
                app_version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_fetch_at TEXT,
                last_seq INTEGER
            );
CREATE TABLE phone_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
CREATE TABLE notification_summaries (
                scope TEXT PRIMARY KEY,
                signature TEXT NOT NULL,
                summary TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
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
            );
CREATE TABLE person_phones (
                phone TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
CREATE TABLE briefings (
                date TEXT PRIMARY KEY,
                headline TEXT NOT NULL,
                points TEXT NOT NULL DEFAULT '[]',
                data TEXT NOT NULL DEFAULT '{}',
                trigger TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                delivered_at TEXT
            );
CREATE TABLE weekly_reviews (
                id TEXT PRIMARY KEY,
                week TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                headline TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                result TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
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
            );
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
            );
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
CREATE TABLE shopping_items (
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
            );
CREATE TABLE shopping_operations (
                id TEXT PRIMARY KEY,
                op_type TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                source TEXT DEFAULT '',
                actor TEXT DEFAULT '',
                chat_id TEXT DEFAULT '',
                message_id TEXT DEFAULT '',
                undone INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE catalog_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT DEFAULT 'xml',
                config_json TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE catalog_stores (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES catalog_sources(id),
                chain_id TEXT DEFAULT '',
                sub_chain_id TEXT DEFAULT '',
                store_id TEXT DEFAULT '',
                name TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE catalog_imports (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES catalog_sources(id),
                filename TEXT DEFAULT '',
                checksum TEXT DEFAULT '',
                status TEXT DEFAULT 'done',
                item_count INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
CREATE TABLE catalog_products (
                barcode TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                manufacturer TEXT DEFAULT '',
                description TEXT DEFAULT '',
                images_json TEXT DEFAULT '{}',
                metadata_json TEXT DEFAULT '{}',
                enriched_at TIMESTAMP
            );
CREATE TABLE catalog_offers (
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
            );
CREATE VIRTUAL TABLE catalog_fts USING fts5(
                barcode UNINDEXED,
                name,
                manufacturer,
                description,
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
            );
CREATE TABLE about_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    history TEXT DEFAULT '[]',
                    pending_approval TEXT DEFAULT NULL,
                    status TEXT DEFAULT 'idle',
                    title TEXT DEFAULT '',
                    debug_info TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                , context TEXT DEFAULT NULL);
CREATE TABLE thread_messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                ts REAL NOT NULL,
                device TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL
            );
CREATE TABLE shortcuts (
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
            );
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
                );
CREATE TABLE usage_daily (
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
                );
CREATE TABLE system_marks (
        key TEXT PRIMARY KEY, last_at TEXT, ok INTEGER, last_ok_at TEXT, last_fail_at TEXT, detail TEXT);
CREATE TABLE wake_log (
        date TEXT PRIMARY KEY, at TEXT NOT NULL, why TEXT);
CREATE TABLE now_hidden (
        item_id TEXT PRIMARY KEY, until TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);
CREATE TABLE notification_rule_declines (package TEXT PRIMARY KEY, declined_at TEXT NOT NULL);
INSERT INTO memories (key, value, category, updated_at) VALUES ('last_location_zone', 'home', 'system', '2026-11-01 08:00:00');
INSERT INTO memories (key, value, category, updated_at) VALUES ('favorite_color', 'blue', 'preferences', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('general', 'General', '📁', '#6366f1', 'General tasks', 0, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('home', 'Home', '🏠', '#10b981', 'Home and family tasks', 1, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('work', 'Work', '💼', '#3b82f6', 'Work and office tasks', 2, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('shopping', 'Shopping', '🛒', '#f59e0b', 'Shopping and errands', 3, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('health', 'Health', '❤️', '#ef4444', 'Health and wellness', 4, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO task_groups (id, name, emoji, color, description, sort_order, created_at, updated_at) VALUES ('personal', 'Personal', '👤', '#a855f7', 'Personal tasks', 5, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id) VALUES ('t1', 'Water the plants', '', 'medium', '2026-11-02', 'needs_action', '{"time": "2026-11-02T07:00:00+02:00"}', 'home', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 0, NULL);
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id) VALUES ('t2', 'Buy seeds', 'From the garden shop', 'high', NULL, 'completed', '{}', 'general', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 2, 'p1');
INSERT INTO tasks (id, title, description, priority, due_date, status, triggers, group_id, created_at, updated_at, snooze_count, project_id) VALUES ('t3', 'Call the plumber', NULL, 'medium', NULL, 'needs_action', '{"location_arrive": "zone.home"}', 'home', '2026-11-01 08:00:00', '2026-11-01 08:00:00', 0, NULL);
INSERT INTO phone_calls (id, filename, caller, receiver, call_timestamp, transcript, summary, created_at, title, duration_seconds, labels, analysis, importance, status, error, audio_sha256, source_filename, direction, phone_number, project_id, mime_type, instructions, audio_deleted_at, person_id) VALUES ('c1', '11111111-2222-3333-4444-555555555555_call.m4a', 'Sam Example', 'Me', '2026-10-31T17:30:00+02:00', 'Sam: can you bring the ladder on Friday?', 'Sam asked to borrow the ladder on Friday.', '2026-11-01 08:00:00', 'Call with Sam Example', 95, '["personal"]', '{"summary": "Ladder on Friday", "action_items": ["Bring the ladder"]}', 'normal', 'ready', NULL, 'deadbeef', 'call.m4a', 'incoming', '+15550100001', NULL, 'audio/mp4', NULL, NULL, 'pe1');
INSERT INTO call_suggestions (id, call_id, kind, payload, status, result_ref, created_at, resolved_at, source_type) VALUES ('cs1', 'c1', 'task', '{"title": "Bring the ladder to Sam", "confidence": 0.9}', 'pending', NULL, '2026-11-01 08:00:00', NULL, 'call');
INSERT INTO call_suggestions (id, call_id, kind, payload, status, result_ref, created_at, resolved_at, source_type) VALUES ('cs2', 'n1', 'reply', '{"text": "Yes, see you Friday"}', 'pending', NULL, '2026-11-01 08:00:00', NULL, 'notification');
INSERT INTO notifications (id, device_key, package, app_name, title, text, sub_text, sender, conversation, is_group, android_category, channel, posted_at, content_hash, status, removed_reason, removed_at, triage_status, category, priority, priority_score, bucket, triage, action_status, escalated_at, created_at, updated_at, snoozed_until, seen_at, nudges, actions, person_id) VALUES ('n1', '0|com.example.chat|1|null|10001', 'com.example.chat', 'Chat', 'Sam Example', 'Are we still on for Friday?', NULL, 'Sam Example', NULL, 0, NULL, NULL, '2026-11-01T08:00:00+00:00', 'h1', 'active', NULL, NULL, 'done', 'personal', 'high', NULL, 'needs_you', '{"reason": "question from a friend"}', 'none', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, NULL, 0, '[{"title": "Reply", "reply": true}]', 'pe1');
INSERT INTO notifications (id, device_key, package, app_name, title, text, sub_text, sender, conversation, is_group, android_category, channel, posted_at, content_hash, status, removed_reason, removed_at, triage_status, category, priority, priority_score, bucket, triage, action_status, escalated_at, created_at, updated_at, snoozed_until, seen_at, nudges, actions, person_id) VALUES ('n2', '0|com.example.shop|2|null|10001', 'com.example.shop', 'Shop', 'Sale', '20% off today', NULL, NULL, NULL, 0, NULL, NULL, '2026-11-01T08:00:00+00:00', 'h2', 'removed', 'dismissed', '2026-11-01T08:00:00+00:00', 'done', NULL, NULL, NULL, 'quiet', NULL, 'none', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL, NULL, 0, NULL, NULL);
INSERT INTO notifications_fts (id, app_name, title, text, sender, conversation) VALUES ('n1', 'Chat', 'Sam Example', 'Are we still on for Friday?', 'Sam Example', NULL);
INSERT INTO notifications_fts (id, app_name, title, text, sender, conversation) VALUES ('n2', 'Shop', 'Sale', '20% off today', NULL, NULL);
INSERT INTO notification_rules (id, package, sender, action, created_at) VALUES ('r1', 'com.example.shop', '', 'mute', '2026-11-01 08:00:00');
INSERT INTO notification_action_requests (id, notification_id, device_key, action, text, status, error, created_at, done_at) VALUES ('ar1', 'n1', '0|com.example.chat|1|null|10001', 'reply', 'Yes', 'done', NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00');
INSERT INTO phone_devices (device_id, token, name, app_version, created_at, updated_at, last_fetch_at, last_seq) VALUES ('dev1', 'fcm-token-sample', 'Test phone', '8.1.0', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', 1);
INSERT INTO phone_events (seq, type, data, created_at, expires_at) VALUES (1, 'notification_sync', '{"ids": ["n1"]}', '2026-11-01T08:00:00+00:00', '2026-11-02T08:00:00+00:00');
INSERT INTO notification_summaries (scope, signature, summary, count, updated_at) VALUES ('com.example.chat', 'sig1', 'Sam asked about Friday.', 1, '2026-11-01T08:00:00+00:00');
INSERT INTO task_links (id, task_id, ref_type, ref_id, role, created_at) VALUES ('tl1', 't3', 'call', 'c1', 'origin', '2026-11-01 08:00:00');
INSERT INTO people (id, name, aliases, relation, notes, birthday, pinned, hidden, source, brief, brief_signature, brief_updated_at, created_at, updated_at) VALUES ('pe1', 'Sam Example', '["Sammy"]', 'friend', 'Likes hiking', '03-14', 1, 0, 'manual', NULL, NULL, NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00');
INSERT INTO person_phones (phone, person_id, created_at) VALUES ('15550100001', 'pe1', '2026-11-01T08:00:00+00:00');
INSERT INTO briefings (date, headline, points, data, trigger, created_at, delivered_at) VALUES ('2026-11-01', 'Quiet morning', '["Water the plants"]', '{}', 'wake', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00');
INSERT INTO weekly_reviews (id, week, status, headline, data, result, created_at, resolved_at) VALUES ('wr1', '2026-W44', 'applied', 'Two things to tidy up', '{"proposals": []}', '{"applied": 0}', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00');
INSERT INTO phone_calls_fts (id, caller, receiver, title, summary, transcript) VALUES ('c1', 'Sam Example', 'Me', 'Call with Sam Example', 'Sam asked to borrow the ladder on Friday.', 'Sam: can you bring the ladder on Friday?');
INSERT INTO projects (id, title, description, icon, color, tags, status, created_at, updated_at) VALUES ('p1', 'Garden plan', 'Spring garden ideas', '🌱', '#10b981', '["garden"]', 'active', '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO entries (id, project_id, kind, title, body, fields, status, priority, due_at, tags, url, media_path, transcript, summary, comments, source_author, created_at, updated_at) VALUES ('e1', 'p1', 'note', 'Tomato varieties', '## Options
- Cherry
- Roma', '[]', 'open', 'medium', NULL, '["plants"]', NULL, NULL, NULL, NULL, '[]', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO entries (id, project_id, kind, title, body, fields, status, priority, due_at, tags, url, media_path, transcript, summary, comments, source_author, created_at, updated_at) VALUES ('e2', 'p1', 'link', 'Raised beds video', '', '[{"name": "Cost", "type": "text", "value": "200"}]', 'open', 'low', NULL, '[]', 'https://example.com/v/1', NULL, NULL, 'How to build a raised bed', '[]', NULL, '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO entries_fts (id, project_id, title, body, summary, transcript, tags, fields) VALUES ('e1', 'p1', 'Tomato varieties', '## Options
- Cherry
- Roma', NULL, NULL, '["plants"]', '[]');
INSERT INTO entries_fts (id, project_id, title, body, summary, transcript, tags, fields) VALUES ('e2', 'p1', 'Raised beds video', '', 'How to build a raised bed', NULL, '[]', '[{"name": "Cost", "type": "text", "value": "200"}]');
INSERT INTO shopping_items (id, name, checked, position, metadata_json, enrichment_json, selected_offer, source, added_by, created_at, updated_at) VALUES ('s1', 'Milk', 0, 0, '{}', '{}', '', 'telegram', 'Sam', '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO shopping_items (id, name, checked, position, metadata_json, enrichment_json, selected_offer, source, added_by, created_at, updated_at) VALUES ('s2', 'Bread', 1, 1, '{"quantity": 2}', '{}', '', 'web', '', '2026-11-01 08:00:00', '2026-11-01 08:00:00');
INSERT INTO shopping_operations (id, op_type, payload_json, source, actor, chat_id, message_id, undone, created_at) VALUES ('o1', 'add', '{"items": ["Milk"]}', 'telegram', 'Sam', '-100123', '42', 0, '2026-11-01 08:00:00');
INSERT INTO catalog_sources (id, name, kind, config_json, updated_at) VALUES ('src1', 'Sample store', 'xml', '{}', '2026-11-01 08:00:00');
INSERT INTO catalog_stores (id, source_id, chain_id, sub_chain_id, store_id, name, updated_at) VALUES ('st1', 'src1', '1', '0', '7', 'Sample branch', '2026-11-01 08:00:00');
INSERT INTO catalog_imports (id, source_id, filename, checksum, status, item_count, error, created_at) VALUES ('imp1', 'src1', 'prices.xml', 'abc', 'done', 1, '', '2026-11-01 08:00:00');
INSERT INTO catalog_products (barcode, name, manufacturer, description, images_json, metadata_json, enriched_at) VALUES ('7290000000001', 'Milk 3%', 'Sample Dairy', '', '{}', '{}', NULL);
INSERT INTO catalog_offers (id, source_id, store_id, barcode, price, unit_price, unit, quantity, available, price_updated_at, metadata_json, updated_at) VALUES ('of1', 'src1', '7', '7290000000001', 6.9, 6.9, 'l', 1.0, 1, '2026-11-01 08:00:00', '{}', '2026-11-01 08:00:00');
INSERT INTO catalog_fts (barcode, name, manufacturer, description) VALUES ('7290000000001', 'Milk 3%', 'Sample Dairy', '');
INSERT INTO about_facts (id, section, text, status, source_kind, source_ref, source_label, legacy_key, created_at, updated_at, confirmed_at, inactive_at) VALUES ('f1', 'people', 'Sam is a friend from the hiking club', 'active', 'owner', NULL, NULL, NULL, '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', NULL);
INSERT INTO about_facts (id, section, text, status, source_kind, source_ref, source_label, legacy_key, created_at, updated_at, confirmed_at, inactive_at) VALUES ('f2', 'other', 'Favorite color: blue', 'active', 'imported', 'favorite_color', NULL, 'favorite_color', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00', NULL);
INSERT INTO about_state (key, value) VALUES ('learn_bookmark', '{"calls": "2026-11-01T08:00:00+00:00"}');
INSERT INTO sessions (session_id, history, pending_approval, status, title, debug_info, created_at, updated_at, context) VALUES ('s_web_1', '[{"role": "user", "content": "What''s on my list?"}, {"role": "assistant", "content": "Water the plants."}]', NULL, 'idle', 'My list', '{}', '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL);
INSERT INTO sessions (session_id, history, pending_approval, status, title, debug_info, created_at, updated_at, context) VALUES ('main', '[]', NULL, 'idle', 'Dana', '{}', '2026-11-01 08:00:00', '2026-11-01 08:00:00', NULL);
INSERT INTO thread_messages (seq, thread_id, ts, device, message) VALUES (1, 'main', 1761984000.0, 'phone', '{"role": "user", "content": "Open the gate"}');
INSERT INTO thread_messages (seq, thread_id, ts, device, message) VALUES (2, 'main', 1761984002.0, '', '{"role": "assistant", "content": "Opened."}');
INSERT INTO shortcuts (id, phrase_norm, phrase, tool, args, reply, streak, active, uses, last_used, learned_at, created_at, updated_at) VALUES ('sc1', 'open the gate', 'Open the gate', 'call_ha_service', '{"domain": "cover", "service": "open_cover", "entity_id": "cover.gate"}', 'Opened.', 3, 1, 4, 1761984000.0, 1761980000.0, 1761970000.0, 1761984000.0);
INSERT INTO usage_events (id, ts, day, kind, purpose, source, ref, provider, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error) VALUES (1, 1761984000.0, '2026-11-01', 'chat', 'chat', 'web', 'session:s_web_1', 'gemini', 'gemini/gemini-flash-lite-latest', 1200, 40, 800, 0, NULL, 0.00021, 900, 1, '');
INSERT INTO usage_events (id, ts, day, kind, purpose, source, ref, provider, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error) VALUES (2, 1761984100.0, '2026-11-01', 'transcribe', 'call_transcribe', 'phone', 'call:c1', 'openai', 'whisper-1', 0, 0, 0, 0, 95.0, 0.0095, 4000, 1, '');
INSERT INTO usage_daily (day, kind, purpose, source, model, calls, failures, input_tokens, output_tokens, cached_tokens, cost, unpriced, ms) VALUES ('2026-08-01', 'chat', 'chat', 'web', 'gemini/gemini-flash-lite-latest', 10, 0, 12000, 400, 8000, 0.0021, 0, 9000);
INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES ('push', '2026-11-01T08:00:00+00:00', 1, '2026-11-01T08:00:00+00:00', NULL, '');
INSERT INTO wake_log (date, at, why) VALUES ('2026-11-01', '2026-11-01T08:00:00+00:00', 'phone alarm');
INSERT INTO now_hidden (item_id, until, reason, created_at) VALUES ('check:calls', '2026-11-03T08:00:00+00:00', 'snooze', '2026-11-01T08:00:00+00:00');
INSERT INTO notification_rule_declines (package, declined_at) VALUES ('com.example.news', '2026-11-01T08:00:00+00:00');
CREATE INDEX idx_phone_calls_sha ON phone_calls(audio_sha256);
CREATE INDEX idx_phone_calls_source ON phone_calls(source_filename);
CREATE INDEX idx_call_suggestions_status ON call_suggestions(status, created_at);
CREATE INDEX idx_call_suggestions_call ON call_suggestions(call_id);
CREATE INDEX idx_call_suggestions_source ON call_suggestions(source_type, status);
CREATE INDEX idx_notifications_status ON notifications(status, posted_at);
CREATE INDEX idx_notifications_key ON notifications(device_key);
CREATE INDEX idx_notifications_package ON notifications(package, posted_at);
CREATE INDEX idx_notifications_triage ON notifications(triage_status);
CREATE TRIGGER notifications_fts_ai AFTER INSERT ON notifications BEGIN
                INSERT INTO notifications_fts(id, app_name, title, text, sender, conversation)
                VALUES (new.id, new.app_name, new.title, new.text, new.sender, new.conversation);
            END;
CREATE TRIGGER notifications_fts_ad AFTER DELETE ON notifications BEGIN
                DELETE FROM notifications_fts WHERE id = old.id;
            END;
CREATE INDEX idx_task_links_ref ON task_links(ref_type, ref_id);
CREATE INDEX idx_person_phones_person ON person_phones(person_id);
CREATE INDEX idx_phone_calls_person ON phone_calls(person_id);
CREATE INDEX idx_notifications_person ON notifications(person_id);
CREATE INDEX idx_weekly_reviews_week ON weekly_reviews(week);
CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
                VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
            END;
CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
                DELETE FROM entries_fts WHERE id = old.id;
            END;
CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
                DELETE FROM entries_fts WHERE id = old.id;
                INSERT INTO entries_fts(id, project_id, title, body, summary, transcript, tags, fields)
                VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields);
            END;
CREATE INDEX idx_shopping_items_checked_pos ON shopping_items(checked, position);
CREATE INDEX idx_shopping_ops_undone_created ON shopping_operations(undone, created_at);
CREATE INDEX idx_catalog_offers_barcode ON catalog_offers(barcode);
CREATE INDEX idx_catalog_offers_store ON catalog_offers(source_id, store_id);
CREATE TRIGGER catalog_products_ai AFTER INSERT ON catalog_products BEGIN
                INSERT INTO catalog_fts(barcode, name, manufacturer, description)
                VALUES (new.barcode, new.name, new.manufacturer, new.description);
            END;
CREATE TRIGGER catalog_products_ad AFTER DELETE ON catalog_products BEGIN
                DELETE FROM catalog_fts WHERE barcode = old.barcode;
            END;
CREATE TRIGGER catalog_products_au AFTER UPDATE ON catalog_products BEGIN
                DELETE FROM catalog_fts WHERE barcode = old.barcode;
                INSERT INTO catalog_fts(barcode, name, manufacturer, description)
                VALUES (new.barcode, new.name, new.manufacturer, new.description);
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
CREATE INDEX idx_thread_messages_thread ON thread_messages(thread_id, seq);
CREATE INDEX idx_usage_events_day ON usage_events(day);
CREATE INDEX idx_usage_events_ref ON usage_events(ref) WHERE ref != '';
COMMIT;
