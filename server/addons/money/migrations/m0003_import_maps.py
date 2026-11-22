"""``money_import_maps``: the CSV column mapping a user last imported a statement file with, per
account (``mapping`` JSON: which column is the date, the description, the amount or debit and
credit, the balance; the date format, the decimal mark, the sign), offered again the next time a
file is imported into that account (addons/money/imports.py)."""

import sqlite3

VERSION = 3
NAME = "import_maps"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE money_import_maps (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL REFERENCES money_accounts(id) ON DELETE CASCADE,
        mapping TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, account_id)
    )""")
