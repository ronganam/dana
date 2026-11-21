"""A WhatsApp account belongs to at most one connection (docs/whatsapp.md,
docs/access.md "Security rules").

``integrations.account_conflict``: the account a connection's service
reported while another user's connection already held it (the connection
then has no ``account``: it is not routed to and its tools refuse until it
reports a different account). Existing rows: when several ``whatsapp``
connections hold the same account (the number before ``@`` / ``:``), the
first admin's (then an admin's, then the oldest connection) keeps it and the
others move to ``account_conflict``. Every other row is unchanged.
"""

import sqlite3
from typing import Dict

VERSION = 19
NAME = "whatsapp_account_claims"


def _key(jid: str) -> str:
    return str(jid or "").strip().split("@", 1)[0].split(":", 1)[0]


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE integrations ADD COLUMN account_conflict TEXT")
    rows = conn.execute(
        "SELECT i.user_id, i.account FROM integrations i LEFT JOIN users u ON u.id = i.user_id "
        "WHERE i.kind = 'whatsapp' AND COALESCE(i.account, '') != '' "
        "ORDER BY (u.access = 'admin') DESC, u.created_at, i.created_at, i.user_id").fetchall()
    holder: Dict[str, str] = {}
    for user_id, account in rows:
        key = _key(account)
        if not key:
            continue
        if key not in holder:
            holder[key] = user_id
            continue
        conn.execute("UPDATE integrations SET account = NULL, account_conflict = ? "
                     "WHERE user_id = ? AND kind = 'whatsapp'", (account, user_id))
