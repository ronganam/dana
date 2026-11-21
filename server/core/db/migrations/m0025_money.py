"""The ``money`` kind: bank / card logins, accounts and transactions (docs/money.md).

- ``money_sources``: one row per bank / card login of a user: ``company``
  (``hapoalim``, ``visaCal``), ``label``, ``credentials`` (a Fernet token of
  the login fields, ``CREDENTIALS_KEY``), ``state`` (``new`` / ``syncing`` /
  ``needs_code`` / ``ok`` / ``failing`` / ``needs_attention``) with
  ``state_reason`` and ``error_type``, consecutive ``failures``, sync times
  (``last_sync_at``, ``last_ok_at``, ``next_sync_at``), the signal-sync count
  of a local day (``signal_syncs_day``, ``signal_syncs``) and the last manual
  sync (``last_manual_at``).
- ``money_accounts``: a bank account or a card, unique per
  ``(user_id, company, number)``; ``source_id`` is NULL once its login is
  removed. ``balance`` (bank) and ``next_charge`` (a card's next bill) in agorot.
- ``money_transactions``: one row per transaction, unique per
  ``(account_id, ext_key)``; ``amount`` in signed agorot; dates are local
  ``YYYY-MM-DD`` (``purchase_date``, ``charge_date``), ``occurred_at`` an
  aware ISO time when known; ``raw`` the scrubbed scraper row (JSON). Searched
  through ``money_transactions_fts`` (kept in step by triggers).
- ``money_rules``: the user's category rule per merchant key.
- ``money_aliases``: learned merchant names (another spelling → a merchant key).
- ``money_syncs``: one row per sync run of a source.
- ``money_code_requests``: an SMS sign-in code a sync waits for (the code
  itself is never stored).

Every time is ISO UTC unless said otherwise; ids are uuid4 hex.
"""

import sqlite3

VERSION = 25
NAME = "money"

_USER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"

STATEMENTS = [
    f"""CREATE TABLE money_sources (
        id TEXT PRIMARY KEY,
        {_USER},
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
    )""",
    "CREATE INDEX idx_money_sources_user ON money_sources(user_id)",
    f"""CREATE TABLE money_accounts (
        id TEXT PRIMARY KEY,
        {_USER},
        source_id TEXT REFERENCES money_sources(id) ON DELETE SET NULL,
        company TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('bank', 'card')),
        number TEXT NOT NULL,
        label TEXT,
        balance INTEGER,
        balance_at TEXT,
        next_charge INTEGER,
        next_charge_date TEXT,
        billed_from_account_id TEXT,
        hidden INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (user_id, company, number)
    )""",
    "CREATE INDEX idx_money_accounts_source ON money_accounts(source_id)",
    f"""CREATE TABLE money_transactions (
        id TEXT PRIMARY KEY,
        {_USER},
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
        updated_at TEXT NOT NULL,
        UNIQUE (account_id, ext_key)
    )""",
    "CREATE INDEX idx_money_transactions_date ON money_transactions(user_id, purchase_date)",
    "CREATE INDEX idx_money_transactions_merchant ON money_transactions(user_id, merchant_key)",
    "CREATE INDEX idx_money_transactions_category ON money_transactions(user_id, category)",
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
    f"""CREATE TABLE money_rules (
        id TEXT PRIMARY KEY,
        {_USER},
        merchant_key TEXT NOT NULL,
        category TEXT,
        merchant TEXT,
        flow TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (user_id, merchant_key)
    )""",
    f"""CREATE TABLE money_aliases (
        {_USER},
        alias_key TEXT NOT NULL,
        merchant_key TEXT NOT NULL,
        merchant TEXT,
        category TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, alias_key)
    )""",
    f"""CREATE TABLE money_syncs (
        id TEXT PRIMARY KEY,
        {_USER},
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
    "CREATE INDEX idx_money_syncs_source ON money_syncs(user_id, source_id, started_at)",
    f"""CREATE TABLE money_code_requests (
        id TEXT PRIMARY KEY,
        {_USER},
        source_id TEXT,
        sync_id TEXT,
        company TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'expired', 'cancelled')),
        answered_by TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        answered_at TEXT
    )""",
    "CREATE INDEX idx_money_code_requests_user ON money_code_requests(user_id, status)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
