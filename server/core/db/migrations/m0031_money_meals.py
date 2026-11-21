"""Money: meal cards (Pluxee / Cibus) and their charges.

- ``money_accounts.kind`` may be ``meal`` too (the table is rebuilt: SQLite
  cannot change a CHECK in place; every row and column is kept). A meal
  card's ``balance`` is what is left of the current budget, its ``details``
  the budget cycle as JSON (``amount``, ``left``, ``start``, ``end``).
- ``money_meals``: a meal card's charges, unique per ``(account_id,
  ext_key)`` (``pluxee:<deal id>``). ``date`` / ``occurred_at`` (local),
  ``place`` (the site's name for the restaurant or shop, scrubbed),
  ``place_id`` (the site's id), ``order_type`` (``delivery`` | ``restaurant``),
  in agorot: ``total`` (the whole meal), ``budget_paid`` (from the employer's
  budget) and ``card_paid`` (the overflow the site charged to the user's own
  card); ``discount``, ``active`` (0 = cancelled), ``budget_name``, ``raw``
  (the site's row, scrubbed JSON). A meal never makes a ``money_transactions``
  row: the employer's budget is not the user's spending, and the overflow
  reaches the user's card as one monthly line of its own.
"""

import sqlite3

VERSION = 31
NAME = "money_meals"
FOREIGN_KEYS_OFF = True

_USER = "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE"
_ACCOUNT = "account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE"

_COLUMNS = ("id, user_id, source_id, company, kind, number, label, balance, balance_at, next_charge, "
            "next_charge_date, billed_from_account_id, hidden, details, created_at, updated_at")

STATEMENTS = [
    f"""CREATE TABLE money_accounts_new (
        id TEXT PRIMARY KEY,
        {_USER},
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
    f"INSERT INTO money_accounts_new ({_COLUMNS}) SELECT {_COLUMNS} FROM money_accounts",
    "DROP TABLE money_accounts",
    "ALTER TABLE money_accounts_new RENAME TO money_accounts",
    "CREATE INDEX idx_money_accounts_source ON money_accounts(source_id)",
    f"""CREATE TABLE money_meals (
        id TEXT PRIMARY KEY,
        {_USER},
        {_ACCOUNT},
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
    "CREATE INDEX idx_money_meals_date ON money_meals(user_id, date)",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
