"""The shopping list remake (docs/shopping.md).

- ``shopping_items`` rebuilt: ``name_key`` (the normalised name items are
  compared by), ``quantity``, ``note``, ``category`` (an aisle key),
  ``checked_at`` / ``checked_by``, ``added_by`` as a user id, ``seq`` (the
  order of adding within a list) and ``rev`` (raised by every change, so an
  undo can tell the item was changed since). Every existing item keeps its
  id, list, name, checked state and order; its ``added_by`` becomes the list
  owner (the old free-text value named nobody), its aisle ``other``; an item
  bought before gets ``checked_at`` = now (it shows under Bought for 12 hours,
  then clears itself like any bought item).
- ``shopping_lists.version``: raised with every change of the list (the live
  updates).
- ``shopping_history``: what was bought on a list (suggestions).
- ``shopping_aisle_choices``: the aisle a list's people chose for a name.
- ``shopping_aisles``: the shared name → aisle table the model fills (no user data).
- ``shopping_invite_answers``: a user's answer (``use`` / ``dismiss``) to a
  list shared with them.
- Dropped: ``shopping_operations`` (undo belongs to each change now), the
  product catalog (``catalog_*`` and its FTS index), the item columns
  ``position``, ``metadata_json``, ``enrichment_json``, ``selected_offer``,
  ``source``, ``updated_at``, and the ``telegram_chat_id`` key of stored
  ``shopping`` settings (the Telegram shopping group is gone).
"""

import datetime
import json
import re
import sqlite3
import unicodedata

VERSION = 20
NAME = "shopping_remake"


def _name_key(name: str) -> str:
    """The comparison key of a name as of this migration (integrations/shopping/names.name_key)."""
    out = []
    for c in unicodedata.normalize("NFKC", name or "").casefold():
        cat = unicodedata.category(c)
        if cat.startswith("M") and "\u0591" <= c <= "\u05c7":
            continue  # Hebrew vowel and cantillation marks
        if cat == "Pd" or c == "/":
            out.append(" ")  # dashes and slashes separate words
        elif not cat.startswith("P"):
            out.append(c)
    return re.sub(r"\s+", " ", "".join(out)).strip()


STATEMENTS = [
    """CREATE TABLE shopping_items_new (
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
    "ALTER TABLE shopping_lists ADD COLUMN version INTEGER NOT NULL DEFAULT 0",
]

AFTER = [
    "DROP TABLE shopping_items",
    "ALTER TABLE shopping_items_new RENAME TO shopping_items",
    "CREATE INDEX idx_shopping_items_list ON shopping_items(list_id, checked, category, seq)",
    "CREATE INDEX idx_shopping_items_key ON shopping_items(list_id, name_key)",
    "DROP TABLE shopping_operations",
    "DROP TABLE catalog_fts",
    "DROP TABLE catalog_offers",
    "DROP TABLE catalog_products",
    "DROP TABLE catalog_imports",
    "DROP TABLE catalog_stores",
    "DROP TABLE catalog_sources",
]


def _iso(stamp: str) -> str:
    """A stored ``CURRENT_TIMESTAMP`` (UTC, ``YYYY-MM-DD HH:MM:SS``) as ISO 8601 with its zone."""
    try:
        dt = datetime.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        dt = datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat(timespec="seconds")


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT i.id, i.list_id, i.name, i.checked, i.created_at, l.user_id AS owner, "
        "ROW_NUMBER() OVER (PARTITION BY i.list_id ORDER BY i.position, i.created_at, i.rowid) AS seq "
        "FROM shopping_items i JOIN shopping_lists l ON l.id = i.list_id").fetchall()
    for r in rows:
        item_id, list_id, name, checked, created_at, owner, seq = tuple(r)
        conn.execute("INSERT INTO shopping_items_new (id, list_id, name, name_key, checked, checked_at, added_by, "
                     "created_at, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (item_id, list_id, name, _name_key(name), 1 if checked else 0, now if checked else None,
                      owner, _iso(created_at), seq))
    for sql in AFTER:
        conn.execute(sql)
    for user_id, settings in conn.execute("SELECT user_id, settings FROM integrations WHERE kind = 'shopping'").fetchall():
        values = json.loads(settings or "{}")
        if "telegram_chat_id" in values:
            values.pop("telegram_chat_id")
            conn.execute("UPDATE integrations SET settings = ? WHERE user_id = ? AND kind = 'shopping'",
                         (json.dumps(values, ensure_ascii=False), user_id))
