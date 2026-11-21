"""The shopping addon's schema as of Dana 9.0: the old single-numbered
migrations 1-34 squashed into one (a database made by Dana 9.0 is converted in place:
core/db/convert.py). Tables: ``shopping_lists``, ``shopping_items``, ``shopping_history``, ``shopping_aisle_choices``, ``shopping_aisles``, ``shopping_invite_answers``.
"""

import sqlite3

VERSION = 1
NAME = "baseline"

STATEMENTS = [
    """CREATE TABLE shopping_lists (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, version INTEGER NOT NULL DEFAULT 0,
        UNIQUE (user_id, name)
    )""",
    """CREATE TABLE "shopping_items" (
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
    )""",
    """CREATE TABLE shopping_history (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name_key TEXT NOT NULL,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other',
        times INTEGER NOT NULL DEFAULT 0,
        last_at TEXT NOT NULL,
        PRIMARY KEY (list_id, name_key)
    )""",
    """CREATE TABLE shopping_aisle_choices (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        name_key TEXT NOT NULL,
        category TEXT NOT NULL,
        PRIMARY KEY (list_id, name_key)
    )""",
    """CREATE TABLE shopping_aisles (
        name_key TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE shopping_invite_answers (
        list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        answer TEXT NOT NULL,
        answered_at TEXT NOT NULL,
        PRIMARY KEY (list_id, user_id)
    )""",
    """CREATE INDEX idx_shopping_items_list ON shopping_items(list_id, checked, category, seq)""",
    """CREATE INDEX idx_shopping_items_key ON shopping_items(list_id, name_key)""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
