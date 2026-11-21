"""The money addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``money_sources``, ``money_transactions``, ``money_transactions_fts``, ``money_rules``, ``money_aliases``, ``money_syncs``, ``money_code_requests``, ``money_signals``, ``money_holdings``, ``money_account_values``, ``money_trades``, ``money_savings``, ``money_market_checks``, ``money_accounts``, ``money_meals``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE money_sources (
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
    , device TEXT)""",
    """CREATE INDEX idx_money_sources_user ON money_sources(user_id)""",
    """CREATE TABLE money_transactions (
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
    )""",
    """CREATE INDEX idx_money_transactions_date ON money_transactions(user_id, purchase_date)""",
    """CREATE INDEX idx_money_transactions_merchant ON money_transactions(user_id, merchant_key)""",
    """CREATE INDEX idx_money_transactions_category ON money_transactions(user_id, category)""",
    """CREATE VIRTUAL TABLE money_transactions_fts USING fts5(
        id UNINDEXED, merchant, description, memo, note, tokenize='unicode61'
    )""",
    """CREATE TRIGGER money_transactions_fts_ai AFTER INSERT ON money_transactions BEGIN
        INSERT INTO money_transactions_fts(id, merchant, description, memo, note)
        VALUES (new.id, new.merchant, new.description, new.memo, new.note);
    END""",
    """CREATE TRIGGER money_transactions_fts_au AFTER UPDATE OF merchant, description, memo, note
        ON money_transactions BEGIN
        DELETE FROM money_transactions_fts WHERE id = old.id;
        INSERT INTO money_transactions_fts(id, merchant, description, memo, note)
        VALUES (new.id, new.merchant, new.description, new.memo, new.note);
    END""",
    """CREATE TRIGGER money_transactions_fts_ad AFTER DELETE ON money_transactions BEGIN
        DELETE FROM money_transactions_fts WHERE id = old.id;
    END""",
    """CREATE TABLE money_rules (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        merchant_key TEXT NOT NULL,
        category TEXT,
        merchant TEXT,
        flow TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (user_id, merchant_key)
    )""",
    """CREATE TABLE money_aliases (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        alias_key TEXT NOT NULL,
        merchant_key TEXT NOT NULL,
        merchant TEXT,
        category TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, alias_key)
    )""",
    """CREATE TABLE money_syncs (
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
    )""",
    """CREATE INDEX idx_money_syncs_source ON money_syncs(user_id, source_id, started_at)""",
    """CREATE TABLE money_code_requests (
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
    )""",
    """CREATE INDEX idx_money_code_requests_user ON money_code_requests(user_id, status)""",
    """CREATE TABLE money_signals (
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
    )""",
    """CREATE UNIQUE INDEX idx_money_signals_notification ON money_signals(user_id, notification_id) WHERE notification_id IS NOT NULL""",
    """CREATE UNIQUE INDEX idx_money_signals_push ON money_signals(user_id, key, text) WHERE source = 'phone_app'""",
    """CREATE INDEX idx_money_signals_created ON money_signals(user_id, created_at)""",
    """CREATE INDEX idx_money_signals_transaction ON money_signals(transaction_id)""",
    """CREATE TABLE money_holdings (
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
    )""",
    """CREATE INDEX idx_money_holdings_user ON money_holdings(user_id)""",
    """CREATE TABLE money_account_values (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        value INTEGER NOT NULL,
        PRIMARY KEY (account_id, date)
    )""",
    """CREATE INDEX idx_money_account_values_user ON money_account_values(user_id, date)""",
    """CREATE TABLE money_trades (
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
    )""",
    """CREATE INDEX idx_money_trades_date ON money_trades(user_id, date)""",
    """CREATE TABLE money_savings (
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
    )""",
    """CREATE TABLE money_market_checks (
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
    )""",
    """CREATE TABLE "money_accounts" (
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
    )""",
    """CREATE INDEX idx_money_accounts_source ON money_accounts(source_id)""",
    """CREATE TABLE money_meals (
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
    )""",
    """CREATE INDEX idx_money_meals_date ON money_meals(user_id, date)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
