"""Money: investment accounts (Psagot Trade) and their portfolio.

- ``money_accounts.kind`` may be ``investment`` too (the table is rebuilt:
  SQLite cannot change a CHECK in place), and gains ``details``: the
  account's latest summary as JSON (cash, holdings value, profit, the
  scrubbed site figures).
- ``money_sources.device``: a login's device key (Psagot's view-only "quick
  view" device), a Fernet token like ``credentials``; NULL when it has none.
- ``money_holdings``: what an investment account holds now, one row per
  security (``security`` = the exchange's number), replaced on each sync:
  ``quantity``, ``price`` (per unit, in ``currency``), ``value`` and
  ``cost`` / ``profit`` in agorot (shekels), ``profit_pct``, ``weight``
  (% of the account), ``raw`` (the site's row, JSON).
- ``money_account_values``: an account's value per local day (agorot).
- ``money_trades``: an investment account's activity (buys, sells,
  dividends, interest, taxes…), unique per ``(account_id, ext_key)``:
  ``action`` (the site's words), ``kind`` (buy / sell / dividend /
  interest / tax / split / redemption / other), ``quantity``, ``price``,
  ``amount`` / ``commission`` / ``tax`` in agorot (shekels), ``raw``.
"""

import sqlite3

VERSION = 28
NAME = "money_portfolio"
FOREIGN_KEYS_OFF = True

_USER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"
_ACCOUNT = "account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE"

_COLUMNS = ("id, user_id, source_id, company, kind, number, label, balance, balance_at, next_charge, "
            "next_charge_date, billed_from_account_id, hidden, created_at, updated_at")

STATEMENTS = [
    f"""CREATE TABLE money_accounts_new (
        id TEXT PRIMARY KEY,
        {_USER},
        source_id TEXT REFERENCES money_sources(id) ON DELETE SET NULL,
        company TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('bank', 'card', 'investment')),
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
    f"INSERT INTO money_accounts_new ({_COLUMNS}) SELECT {_COLUMNS} FROM money_accounts",
    "DROP TABLE money_accounts",
    "ALTER TABLE money_accounts_new RENAME TO money_accounts",
    "CREATE INDEX idx_money_accounts_source ON money_accounts(source_id)",
    "ALTER TABLE money_sources ADD COLUMN device TEXT",
    f"""CREATE TABLE money_holdings (
        id TEXT PRIMARY KEY,
        {_USER},
        {_ACCOUNT},
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
    "CREATE INDEX idx_money_holdings_user ON money_holdings(user_id)",
    f"""CREATE TABLE money_account_values (
        {_USER},
        {_ACCOUNT},
        date TEXT NOT NULL,
        value INTEGER NOT NULL,
        PRIMARY KEY (account_id, date)
    )""",
    "CREATE INDEX idx_money_account_values_user ON money_account_values(user_id, date)",
    f"""CREATE TABLE money_trades (
        id TEXT PRIMARY KEY,
        {_USER},
        {_ACCOUNT},
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
    "CREATE INDEX idx_money_trades_date ON money_trades(user_id, date)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
