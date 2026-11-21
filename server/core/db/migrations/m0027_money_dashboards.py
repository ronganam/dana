"""Money dashboards: two override columns and a second classification pass.

- ``money_transactions.one_off`` / ``yearly``: NULL = Dana decides; 1 / 0 =
  the user's choice — a one-off is left out of averages, "typical" and the
  balance forecast; a yearly charge (an insurance renewal, the car licence)
  is expected again a year later.
- Outgoing bank transfers whose note names a payee ("לטובת: <payee>. עבור:
  <purpose>.") take the payee as their merchant and merchant key, and lose
  the generic transfer category and flow the bank's activity code gave them,
  so the classifier sorts them again (a shop, an investment or a person).
- Card rows whose category came from Cal's own category or that Dana filed
  under an income category (a refund takes what was refunded; the
  remembered merchant categories lose those too), and a card
  company's move of charges to a replacement card, are classified again
  (the model now decides with Cal's category as a hint; the move is an own
  transfer). A category the user chose or a rule gave is never touched.
"""

import re
import sqlite3
import unicodedata
from typing import Optional, Tuple

VERSION = 27
NAME = "money_dashboards"

_LOCKED = "COALESCE(t.category_source, '') NOT IN ('user', 'rule')"
# integrations/money/categories.py's income categories, and ingest.py's merchant key and transfer
# note, as they are when this migration was written (a migration never follows later changes).
_INCOME = ("salary", "refunds", "benefits", "interest", "transfers_in", "other_income")
_QUOTES = re.compile(r"[\"'`׳״]")
_PUNCT = re.compile(r"[^\w\s]")
_NOTE = re.compile(r"^\s*לטובת:\s*(?P<payee>.+?)\s*(?:\.\s*עבור:\s*(?P<purpose>.*?))?\s*\.?\s*$", re.S)


def _key(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").casefold()
    s = _PUNCT.sub(" ", _QUOTES.sub("", s)).replace("_", " ")
    return " ".join(t for t in s.split() if not t.isdigit())


def _payee(memo: Optional[str]) -> Optional[Tuple[str, str]]:
    """(payee, display name) of a transfer note, None when the memo is not one."""
    m = _NOTE.match(memo or "")
    payee = " ".join(m.group("payee").split())[:60].strip() if m else ""
    if not payee or not _key(payee):
        return None
    name = re.sub(r"\s*\b(בע(?:[\"'״]?מ)?|בעמ|ltd\.?|inc\.?)\s*$", "", payee, flags=re.I).strip(" .,-")
    return payee, name or payee


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE money_transactions ADD COLUMN one_off INTEGER")
    conn.execute("ALTER TABLE money_transactions ADD COLUMN yearly INTEGER")
    rows = conn.execute(
        "SELECT t.id, t.description, t.merchant, t.memo, t.category_source FROM money_transactions t "
        "JOIN money_accounts a ON a.id = t.account_id WHERE a.kind = 'bank' AND t.amount < 0 "
        "AND t.memo LIKE 'לטובת:%'").fetchall()
    for row in rows:
        found = _payee(row[3])
        if found is None:
            continue
        merchant = found[1] if not row[2] or row[2] == row[1] else row[2]
        conn.execute("UPDATE money_transactions SET merchant_key = ?, merchant = ? WHERE id = ?",
                     (_key(found[0]), merchant, row[0]))
        if (row[4] or "") not in ("user", "rule"):
            conn.execute("UPDATE money_transactions SET category = NULL, category_source = NULL, flow = NULL "
                         "WHERE id = ?", (row[0],))
    marks = ",".join("?" * len(_INCOME))
    conn.execute("UPDATE money_transactions SET category = NULL, category_source = NULL WHERE id IN ("
                 "SELECT t.id FROM money_transactions t JOIN money_accounts a ON a.id = t.account_id "
                 "WHERE a.kind = 'card' AND (t.category_source = 'bank' OR (t.category_source IN ('model', 'memory') "
                 f"AND t.category IN ({marks}))))", _INCOME)
    conn.execute(f"UPDATE money_aliases SET category = NULL WHERE category IN ({marks})", _INCOME)
    conn.execute("UPDATE money_transactions SET category = NULL, category_source = NULL, flow = NULL WHERE id IN ("
                 "SELECT t.id FROM money_transactions t JOIN money_accounts a ON a.id = t.account_id "
                 f"WHERE a.kind = 'card' AND t.description LIKE '%כרטיס תחליפי%' AND {_LOCKED})")
