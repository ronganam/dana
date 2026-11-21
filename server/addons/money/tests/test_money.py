"""Money: ingest of scraper results, scrubbing, categories, rules with Undo and the sync slots (docs/money.md).
Run: bin/test test_money

Synthetic fixtures in money's ingest format (the ``israeli-bank-scrapers``
accounts money_il's Hapoalim and Cal hand over), every value made up.
Covers: transaction keys (Cal identifier + installment, Hapoalim's hash with
an occurrence count so identical rows stay two) and re-ingest idempotency;
pending rows replaced on every pull (provisional ones left alone); changed
rows updated without touching a user's category / note; local dates and
exact times from the scrapers' UTC instants; an installment's purchase
date; currencies; bank balance and the card's next bill; scrubbing of ID,
card and account numbers (text and raw); merchant keys; rules applied at
ingest; per-user accounts; the category taxonomy and ``resolve``;
``set_category`` / ``set_note`` with Undo, scoped to the user; transaction
reads; the slots (jitter, MONEY_SYNC_TIMES, quiet hours), ``request_soon``'s
daily limit and ``sync_now``'s refusals. Sync flows with the fake worker and
the API are in addons/money_il/tests/test_money_il_sync.py.
"""

import asyncio
import datetime
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake logins
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Robin"

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from core import clock, undo  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import categories, config, ingest, rules, schedule, slots, sources, store  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


TZ = clock.get_timezone()
TODAY = datetime.datetime(2026, 11, 18, 9, 30, tzinfo=TZ)
ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob")
connections.connect(ADMIN, "money")
connections.connect(ADMIN, "money_il")
connections.connect(BOB, "money")
connections.connect(BOB, "money_il")

# --- Fixtures (made-up values, real shapes) ------------------------------------------------------

SALARY_MEMO = "עבור: לוי דנה מזהה 123456782."  # 123456782 passes the ID checksum


def poalim_txn(date: str, amount: float, description: str, memo: str = "", status: str = "completed",
               identifier: int = 4410, serial: int = 1, **raw: Any) -> Dict[str, Any]:
    return {"type": "normal", "identifier": identifier, "date": date, "processedDate": date, "originalAmount": amount,
            "originalCurrency": "ILS", "chargedAmount": amount, "description": description, "status": status,
            "memo": memo, "rawTransaction": {"eventDate": int(date[:10].replace("-", "")), "serialNumber": serial,
                                             "eventActivityTypeCode": 2, "activityTypeCode": 491,
                                             "referenceNumber": identifier, "eventAmount": abs(amount),
                                             "currentBalance": 17710.51, **raw}}


def hapoalim_accounts(pending: bool = True) -> List[Dict[str, Any]]:
    txns = [
        {"type": "normal", "identifier": 8763, "date": "2026-11-14T22:00:00.000Z",
         "processedDate": "2026-11-14T22:00:00.000Z", "originalAmount": -8455.9, "originalCurrency": "ILS",
         "chargedAmount": -8455.9, "description": "כאל", "status": "completed", "memo": "",
         "rawTransaction": {"eventDate": 20261115, "valueDate": 20261115, "serialNumber": 2,
                            "eventActivityTypeCode": 2, "activityTypeCode": 491, "referenceNumber": 8763,
                            "eventAmount": 8455.9, "currentBalance": 17710.51, "englishActionDesc": "CAL"}},
        # Two identical transfers on the same day (Hapoalim repeats identifier; only the row position differs).
        poalim_txn("2026-11-09T22:00:00.000Z", -250, "העברה לחשבון", "לדני כהן", serial=3),
        poalim_txn("2026-11-09T22:00:00.000Z", -250, "העברה לחשבון", "לדני כהן", serial=4),
        poalim_txn("2026-11-09T22:00:00.000Z", 15000.0, "משכורת אופק בע\"מ", SALARY_MEMO, identifier=9001,
                   beneficiaryId="123456782", partyAccount="12-600-123456789"),
        poalim_txn("2026-11-11T22:00:00.000Z", -120, "העברה מכרטיס", "כרטיס 4580123412341234 חשבון 12-600-123456789 "
                   "אסמכתא 123456789"),
    ]
    if pending:
        txns.append(poalim_txn("2026-11-17T22:00:00.000Z", -40, "ביט העברה", status="pending", identifier=0))
    return [{"accountNumber": "12-345-678901", "balance": 1234.56, "txns": txns},
            {"accountNumber": "12-345-99862", "txns": []}]


def cal_txn(identifier: Any, date: str, processed: str, charged: float, description: str, *,
            original: Any = None, currency: str = "₪", status: str = "completed", category: str = "מזון ומשקאות",
            purchase: str = "", installments: Any = None) -> Dict[str, Any]:
    t = {"identifier": identifier, "type": "installments" if installments else "normal", "status": status,
         "date": date, "processedDate": processed, "originalAmount": charged if original is None else original,
         "originalCurrency": currency, "chargedAmount": charged, "chargedCurrency": "₪", "description": description,
         "memo": "", "category": category,
         "rawTransaction": {"trnPurchaseDate": purchase or date[:19], "debCrdDate": processed[:10] + "T00:00:00",
                            "branchCodeDesc": category, "isAbroadTransaction": currency != "₪",
                            "walletProviderCode": 5}}
    if installments:  # money_il's convert adds purchaseDate (Cal's trnPurchaseDate) to an installment
        t["installments"] = installments
        t["purchaseDate"] = purchase
    return t


def cal_accounts(foreign_charged: float = -15.94, pending_desc: str = "קפה קפה") -> List[Dict[str, Any]]:
    return [{"accountNumber": "8763", "balance": -3450.81, "balanceDate": "2026-12-14T22:00:00.000Z",
             "cardType": "Mastercard", "txns": [
                 cal_txn("32580593492", "2026-11-03T15:07:18.000Z", "2026-11-14T22:00:00.000Z", foreign_charged,
                         "ONLINE COURSE CO", original=-5, currency="$", category="חינוך",
                         purchase="2026-11-03T17:07:18"),
                 cal_txn("40000000001", "2026-11-24T13:00:00.000Z", "2026-12-14T22:00:00.000Z", -709,
                         "ביטוח רכב ישיר", original=-2836, purchase="2026-08-24T15:00:00", category="ביטוח",
                         installments={"number": 3, "total": 4}),
                 cal_txn("40000000001", "2026-10-24T13:00:00.000Z", "2026-11-14T22:00:00.000Z", -709,
                         "ביטוח רכב ישיר", original=-2836, purchase="2026-08-24T15:00:00", category="ביטוח",
                         installments={"number": 2, "total": 4}),
                 cal_txn("32580593500", "2026-11-17T10:58:17.000Z", "2026-12-14T22:00:00.000Z", -350.3,
                         "רמי לוי - יבנה"),
                 cal_txn("32580593511", "2026-11-12T09:00:00.000Z", "2026-12-14T22:00:00.000Z", -136.85,
                         "MUSEUM SHOP PARIS", original=-35, currency="€", category="פנאי"),
                 cal_txn("32580593522", "2026-11-12T10:00:00.000Z", "2026-12-14T22:00:00.000Z", -88.2,
                         "LONDON BOOKS", original=-18.4, currency="GBP", category="ספרים"),
                 cal_txn(None, "2026-11-18T08:00:00.000Z", "2026-12-14T22:00:00.000Z", -42, pending_desc,
                         status="pending"),
             ]}]


def rows(user, account_id: str = "") -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        sql, args = "SELECT * FROM money_transactions WHERE user_id = ?", [user.id]
        if account_id:
            sql += " AND account_id = ?"
            args.append(account_id)
        return [dict(r) for r in conn.execute(sql + " ORDER BY ext_key", args)]


def account(user, number: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM money_accounts WHERE user_id = ? AND number = ?", (user.id, number)).fetchone()
    return dict(r) if r else {}


def by_desc(user, text: str) -> List[Dict[str, Any]]:
    return [r for r in rows(user) if r["description"] == text]


def apply(user, source, accounts) -> Dict[str, List[str]]:
    with patch.object(clock, "now", return_value=TODAY):
        return ingest.apply(user, source, accounts)


# Sources (no event loop runs here, so adding one starts no sync).
POALIM = sources.add(ADMIN, "hapoalim", {"userCode": "AB12345", "password": "pw-poalim-1"})
CAL = sources.add(ADMIN, "visaCal", {"username": "robin.cal", "password": "pw-cal-1"}, "My Cal")
BOB_CAL = sources.add(BOB, "visaCal", {"username": "bob.cal", "password": "pw-cal-bob"})


# --- 1. Keys, idempotency, dates -----------------------------------------------------------------

def test_keys_and_dates() -> None:
    out = apply(ADMIN, POALIM, hapoalim_accounts())
    poalim = rows(ADMIN, account(ADMIN, "12-345-678901")["id"])
    check("ingest: every Hapoalim row stored (the two identical transfers stay two)", len(poalim) == 6
          and len(out["new"]) == 6 and not out["changed"], [r["description"] for r in poalim])
    transfers = by_desc(ADMIN, "העברה לחשבון")
    keys = sorted(t["ext_key"] for t in transfers)
    check("keys: identical Hapoalim rows get occurrence 0 and 1 of one hash",
          len(keys) == 2 and keys[0].startswith("poalim:") and keys[0].endswith(":0") and keys[1].endswith(":1")
          and keys[0].rsplit(":", 1)[0] == keys[1].rsplit(":", 1)[0], keys)
    bill = by_desc(ADMIN, "כאל")[0]
    check("dates: a UTC instant of local midnight → the local date, no exact time",
          bill["purchase_date"] == "2026-11-15" and bill["charge_date"] == "2026-11-15" and bill["occurred_at"] is None,
          bill)
    check("amounts: signed agorot; ILS kept as the original currency",
          bill["amount"] == -845590 and bill["orig_amount"] == -845590 and bill["orig_currency"] == "ILS", bill)
    check("ingest: accounts are returned, the empty one included", len(out["accounts"]) == 2)
    empty = account(ADMIN, "12-345-99862")
    check("accounts: an account with no transactions and no balance is still stored",
          empty.get("kind") == "bank" and empty.get("balance") is None and empty.get("source_id") == POALIM["id"]
          and not rows(ADMIN, empty["id"]), empty)
    bank = account(ADMIN, "12-345-678901")
    check("accounts: the bank balance in agorot, with its time", bank["balance"] == 123456 and bank["balance_at"], bank)

    again = apply(ADMIN, POALIM, hapoalim_accounts())
    check("re-ingest: the same pull adds and changes nothing", again["new"] == [] and again["changed"] == []
          and len(rows(ADMIN)) == 6, again)

    out = apply(ADMIN, CAL, cal_accounts())
    card = account(ADMIN, "8763")
    cal = {r["description"]: r for r in rows(ADMIN, card["id"])}
    check("Cal: all 7 rows stored", len(out["new"]) == 7 and len(cal) == 6, sorted(cal))
    keys = sorted(r["ext_key"] for r in rows(ADMIN, card["id"]))
    check("keys: Cal identifier + installment number (two payments of one purchase are two rows)",
          {"cal:32580593492:0", "cal:40000000001:3", "cal:40000000001:2", "cal:32580593500:0"} <= set(keys), keys)
    check("keys: a Cal row without an identifier gets a hash key", any(k.startswith("x:") for k in keys), keys)
    foreign = cal["ONLINE COURSE CO"]
    check("Cal: a real time in date → occurred_at (local, aware) and its local purchase date",
          foreign["occurred_at"] == "2026-11-03T17:07:18+02:00" and foreign["purchase_date"] == "2026-11-03"
          and foreign["charge_date"] == "2026-11-15", foreign)
    check("currencies: $ → USD with the original amount; the charge in shekels",
          foreign["orig_currency"] == "USD" and foreign["orig_amount"] == -500 and foreign["amount"] == -1594, foreign)
    check("currencies: € → EUR, ₪ → ILS, others kept",
          cal["MUSEUM SHOP PARIS"]["orig_currency"] == "EUR" and cal["רמי לוי - יבנה"]["orig_currency"] == "ILS"
          and cal["LONDON BOOKS"]["orig_currency"] == "GBP")
    rami = cal["רמי לוי - יבנה"]
    check("Cal: 10:58:17Z is 12:58:17 local", rami["occurred_at"] == "2026-11-17T12:58:17+02:00"
          and rami["purchase_date"] == "2026-11-17", rami)
    inst = [r for r in rows(ADMIN, card["id"]) if r["installment_number"] == 3][0]
    check("installments: the purchase date from purchaseDate (not the payment month), n/total kept",
          inst["purchase_date"] == "2026-08-24" and inst["occurred_at"] == "2026-08-24T15:00:00+03:00"
          and inst["installment_total"] == 4 and inst["charge_date"] == "2026-12-15", inst)
    check("Cal: the next bill is the balance (positive agorot) on the first charge date from today",
          card["next_charge"] == 345081 and card["next_charge_date"] == "2026-12-15" and card["balance"] is None
          and card["kind"] == "card", card)
    check("categories: none from the bank at ingest (Cal's own category stays in raw)",
          all(r["category"] is None and r["category_source"] is None for r in cal.values())
          and json.loads(rami["raw"])["category"] == "מזון ומשקאות"
          and json.loads(rami["raw"])["rawTransaction"]["branchCodeDesc"] == "מזון ומשקאות")
    check("merchant: the description until classification", rami["merchant"] == "רמי לוי - יבנה"
          and rami["merchant_key"] == "רמי לוי יבנה", rami["merchant_key"])
    check("re-ingest: Cal's pull again changes nothing", apply(ADMIN, CAL, cal_accounts()) ==
          {"new": [], "changed": [], "accounts": [card["id"]]})


# --- 2. Pending, changed rows --------------------------------------------------------------------

def test_pending_and_changes() -> None:
    card = account(ADMIN, "8763")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, purchase_date, "
                     "amount, description, created_at, updated_at) VALUES (?, ?, ?, 'notif:1', 'notification', "
                     "'provisional', '2026-11-18', -2000, 'WALLET', 'x', 'x')", (uuid.uuid4().hex, ADMIN.id, card["id"]))
        conn.commit()
    old_pending = [r for r in rows(ADMIN, card["id"]) if r["status"] == "pending"]
    out = apply(ADMIN, CAL, cal_accounts(pending_desc="קפה אחר"))
    pending = [r for r in rows(ADMIN, card["id"]) if r["status"] == "pending"]
    check("pending: a pending row not in the new pull is deleted, the new one added",
          len(old_pending) == 1 and len(pending) == 1 and pending[0]["description"] == "קפה אחר"
          and pending[0]["id"] != old_pending[0]["id"] and out["new"] == [pending[0]["id"]], pending)
    check("pending: a provisional (notification) row is left alone",
          any(r["ext_key"] == "notif:1" for r in rows(ADMIN, card["id"])))
    apply(ADMIN, POALIM, hapoalim_accounts(pending=False))
    check("pending: Hapoalim's pending row goes when the pull no longer has it",
          not [r for r in rows(ADMIN, account(ADMIN, "12-345-678901")["id"]) if r["status"] == "pending"])

    foreign = by_desc(ADMIN, "ONLINE COURSE CO")[0]
    with users.acting_as(ADMIN):
        rules.set_category(ADMIN, foreign["id"], "education", for_merchant=False)
        rules.set_note(ADMIN, foreign["id"], "course for work")
    out = apply(ADMIN, CAL, cal_accounts(foreign_charged=-15.95, pending_desc="קפה אחר"))
    after = by_desc(ADMIN, "ONLINE COURSE CO")[0]
    check("changed: a completed row whose amount changed is updated and reported",
          out["changed"] == [foreign["id"]] and after["amount"] == -1595 and not out["new"], out)
    check("changed: the user's category, note and merchant are never overwritten",
          after["category"] == "education" and after["category_source"] == "user" and after["note"] == "course for work"
          and after["merchant"] == "ONLINE COURSE CO", after)


# --- 3. Scrub, merchant keys, rules at ingest, per user --------------------------------------------

def test_scrub_and_rules() -> None:
    salary = by_desc(ADMIN, "משכורת אופק בע\"מ")[0]
    check("scrub: the ID number in the salary memo → [id], the name stays",
          salary["memo"] == "עבור: לוי דנה מזהה [id].", salary["memo"])
    check("scrub: no ID number anywhere in the stored raw row", "123456782" not in salary["raw"]
          and json.loads(salary["raw"])["rawTransaction"]["beneficiaryId"] == "[id]", salary["raw"])
    check("scrub: an account number in raw keeps its last 3 digits",
          json.loads(salary["raw"])["rawTransaction"]["partyAccount"] == "••••789")
    card_memo = by_desc(ADMIN, "העברה מכרטיס")[0]["memo"]
    check("scrub: card and account numbers in a memo keep their last 3; a 9-digit non-ID stays",
          card_memo == "כרטיס ••••234 חשבון ••••789 אסמכתא 123456789", card_memo)
    check("scrub: the checksum", ingest.israeli_id_valid("123456782") and not ingest.israeli_id_valid("123456789")
          and ingest.scrub("id 000000018 here") == "id [id] here")
    check("scrub: integers that are an ID become [id]", ingest.scrub_value({"a": 123456782, "b": 5, "c": True}) ==
          {"a": "[id]", "b": 5, "c": True})
    check("merchant_key: lower case, punctuation and branch numbers out, quotes dropped",
          ingest.merchant_key("SHUFERSAL DEAL #123 - Tel-Aviv") == "shufersal deal tel aviv"
          and ingest.merchant_key("א.נ בראנץ בע\"מ - FRESH BE") == "א נ בראנץ בעמ fresh be"
          and ingest.merchant_key("WOLT*  4412") == "wolt", ingest.merchant_key("SHUFERSAL DEAL #123 - Tel-Aviv"))

    with get_db_connection() as conn:
        conn.execute("INSERT INTO money_rules (id, user_id, merchant_key, category, merchant, flow, created_at) "
                     "VALUES (?, ?, 'bookshop tlv', 'education', 'Bookshop', 'purchase', 'x')", (uuid.uuid4().hex, BOB.id))
        conn.commit()
    accounts = cal_accounts()
    accounts[0]["txns"].append(cal_txn("55500000001", "2026-11-16T10:00:00.000Z", "2026-12-14T22:00:00.000Z", -99,
                                       "BOOKSHOP TLV 12"))
    out = apply(BOB, BOB_CAL, accounts)
    bob_card = account(BOB, "8763")
    shop = [r for r in rows(BOB) if r["description"] == "BOOKSHOP TLV 12"][0]
    check("rules: a rule of the user applies at ingest (category, display name, flow; source 'rule')",
          shop["category"] == "education" and shop["category_source"] == "rule" and shop["merchant"] == "Bookshop"
          and shop["flow"] == "purchase", shop)
    check("per user: Bob's card with the same number is his own account and rows",
          bob_card["id"] != account(ADMIN, "8763")["id"] and len(out["new"]) == 8
          and all(r["user_id"] == BOB.id for r in rows(BOB)))
    check("per user: the admin's rows did not take Bob's rule",
          not [r for r in rows(ADMIN) if r["category_source"] == "rule"])


# --- 4. Categories ---------------------------------------------------------------------------------

def test_categories() -> None:
    check("resolve: a group → its categories", categories.resolve("food") == {"groceries", "restaurants", "delivery"})
    check("resolve: a category key, English or Hebrew label, any case",
          categories.resolve("restaurants") == {"restaurants"} and categories.resolve("Restaurants & Cafés") ==
          {"restaurants"} and categories.resolve("מסעדות ובתי קפה") == {"restaurants"}
          and categories.resolve("  FUEL ") == {"fuel"} and categories.resolve("אוכל") == categories.resolve("food"))
    check("resolve: a group wins over its same-named category",
          categories.resolve("shopping") == {"clothing", "electronics", "shopping"}
          and categories.resolve("housing") == {"housing", "utilities", "home"})
    check("resolve: income group, unknown words", categories.resolve("income") >= {"salary", "refunds"}
          and categories.resolve("nonsense") == set() and categories.resolve("") == set())
    check("taxonomy: every category's group exists; keys unique",
          all(c.group in categories.GROUP_KEYS for c in categories.CATEGORIES.values())
          and len(categories.CATEGORIES) == 35)
    check("flows: what counts as spending", categories.SPENDING_FLOWS ==
          {"purchase", "refund", "transfer_out", "fee", "cash", "loan"}
          and not categories.SPENDING_FLOWS & {"card_bill", "own_transfer", "investment", "income", "transfer_in"}
          and categories.SPENDING_FLOWS <= set(categories.FLOWS))


# --- 5. set_category / set_note with Undo -------------------------------------------------------------

async def test_rules_and_undo() -> None:
    card = account(ADMIN, "8763")
    rami = by_desc(ADMIN, "רמי לוי - יבנה")[0]
    accounts = cal_accounts(pending_desc="קפה אחר", foreign_charged=-15.95)
    accounts[0]["txns"].append(cal_txn("32580593600", "2026-11-10T10:00:00.000Z", "2026-12-14T22:00:00.000Z", -51.7,
                                       "רמי לוי - יבנה 2"))
    accounts[0]["txns"].append(cal_txn("32580593601", "2026-11-11T10:00:00.000Z", "2026-12-14T22:00:00.000Z", -12,
                                       "רמי לוי - יבנה"))
    apply(ADMIN, CAL, accounts)
    same = [r for r in rows(ADMIN, card["id"]) if r["merchant_key"] == "רמי לוי יבנה"]
    by_hand = [r for r in same if r["id"] != rami["id"]][0]
    with users.acting_as(ADMIN):
        rules.set_category(ADMIN, by_hand["id"], "restaurants", for_merchant=False)
        out = rules.set_category(ADMIN, rami["id"], "groceries")
    after = {r["id"]: r for r in rows(ADMIN, card["id"]) if r["merchant_key"] == "רמי לוי יבנה"}
    with get_db_connection() as conn:
        rule = conn.execute("SELECT * FROM money_rules WHERE user_id = ? AND merchant_key = ?",
                            (ADMIN.id, "רמי לוי יבנה")).fetchone()
    check("set_category: three rows of the merchant (branch number ignored)", len(same) == 3, len(same))
    check("set_category: the row is the user's, the others follow the new rule, a hand-set one is kept",
          after[rami["id"]]["category"] == "groceries" and after[rami["id"]]["category_source"] == "user"
          and after[by_hand["id"]]["category"] == "restaurants" and after[by_hand["id"]]["category_source"] == "user"
          and [r["category_source"] for i, r in after.items() if i not in (rami["id"], by_hand["id"])] == ["rule"],
          after)
    check("set_category: the rule is saved and the count says what changed",
          rule is not None and rule["category"] == "groceries" and out["changed"] == 2, out)
    with users.acting_as(ADMIN):
        label = undo.describe(out["undo_id"], ADMIN.id)
        await undo.run(out["undo_id"])
    restored = {r["id"]: r for r in rows(ADMIN, card["id"]) if r["merchant_key"] == "רמי לוי יבנה"}
    with get_db_connection() as conn:
        gone = conn.execute("SELECT 1 FROM money_rules WHERE user_id = ? AND merchant_key = ?",
                            (ADMIN.id, "רמי לוי יבנה")).fetchone()
    check("undo: label names the merchant", label and "רמי לוי - יבנה" in label["label"], label)
    check("undo: the rows and the rule are as before",
          gone is None and restored[rami["id"]]["category"] is None and restored[rami["id"]]["category_source"] is None
          and restored[by_hand["id"]]["category"] == "restaurants", restored)
    try:
        rules.set_category(ADMIN, rami["id"], "pizza")
        check("set_category: an unknown category is refused", False)
    except ValueError:
        check("set_category: an unknown category is refused", True)
    for who, txn in ((BOB, rami["id"]), (ADMIN, "nope")):
        try:
            rules.set_category(who, txn, "groceries")
            check(f"set_category: another user's / a missing row is unknown ({who.name})", False)
        except LookupError:
            check(f"set_category: another user's / a missing row is unknown ({who.name})", True)
    check("set_category: Bob's attempt changed nothing", by_desc(ADMIN, "רמי לוי - יבנה")[0]["category"] is None)
    with users.acting_as(ADMIN):
        note = rules.set_note(ADMIN, rami["id"], "  weekly shop  ")
    check("set_note: stored trimmed", by_desc(ADMIN, "רמי לוי - יבנה")[0]["note"] == "weekly shop")
    with users.acting_as(BOB):
        try:
            await undo.run(note["undo_id"])
            check("undo: another user's undo id does not exist", False)
        except KeyError:
            check("undo: another user's undo id does not exist", True)
    with users.acting_as(ADMIN):
        await undo.run(note["undo_id"])
    check("set_note: undo restores the previous note", by_desc(ADMIN, "רמי לוי - יבנה")[0]["note"] is None)
    try:
        rules.set_note(BOB, rami["id"], "x")
        check("set_note: another user's row is unknown", False)
    except LookupError:
        check("set_note: another user's row is unknown", True)


# --- 6. Reads ------------------------------------------------------------------------------------

def test_reads() -> None:
    card = account(ADMIN, "8763")
    november = store.list_transactions(ADMIN, start="2026-11-01", end="2026-12-01")
    check("list_transactions: by purchase date, end exclusive, newest first, no raw / user_id",
          november and all("2026-11-01" <= t["purchase_date"] < "2026-12-01" for t in november)
          and [t["purchase_date"] for t in november] == sorted((t["purchase_date"] for t in november), reverse=True)
          and "raw" not in november[0] and "user_id" not in november[0])
    only_card = store.list_transactions(ADMIN, account_id=card["id"], limit=3)
    check("list_transactions: one account, limited", len(only_card) == 3
          and all(t["account_id"] == card["id"] for t in only_card))
    check("list_transactions: Bob's account id gives the admin nothing",
          store.list_transactions(ADMIN, account_id=account(BOB, "8763")["id"]) == [])
    txn = november[0]
    check("get_transaction: the user's own with raw; another user's is missing",
          store.get_transaction(ADMIN, txn["id"])["raw"] is not None and store.get_transaction(BOB, txn["id"]) is None)
    check("get_source: another user's source is missing", store.get_source(BOB, CAL["id"]) is None
          and store.get_source(ADMIN, CAL["id"])["label"] == "My Cal")
    payload = store.source_payload(ADMIN, store.get_source(ADMIN, POALIM["id"]))
    check("source payload: bank numbers masked to the last 3 digits, no credentials",
          sorted(a["number"] for a in payload["accounts"]) == ["••••862", "••••901"]
          and payload["credentials_set"] is True and "pw-poalim-1" not in json.dumps(payload)
          and "AB12345" not in json.dumps(payload), payload)
    with get_db_connection() as conn:
        hits = [r["id"] for r in conn.execute("SELECT id FROM money_transactions_fts WHERE money_transactions_fts "
                                              "MATCH ?", ('"ONLINE COURSE"',))]
        note_hits = [r["id"] for r in conn.execute("SELECT id FROM money_transactions_fts WHERE "
                                                   "money_transactions_fts MATCH 'work'")]
    check("fts: the triggers index merchant / description", by_desc(ADMIN, "ONLINE COURSE CO")[0]["id"] in hits)
    check("fts: an updated note is searchable", note_hits == [by_desc(ADMIN, "ONLINE COURSE CO")[0]["id"]], note_hits)


# --- 7. Slots, signals, manual limits ---------------------------------------------------------------

def test_slots() -> None:
    sid = POALIM["id"]
    j = slots.jitter(sid)
    check("jitter: stable and within ±20 minutes", j == slots.jitter(sid) and -20 <= j <= 20
          and len({slots.jitter(uuid.uuid4().hex) for _ in range(60)}) > 5)
    nxt = slots.next_slot(sid, TODAY)
    check("next_slot: 19:00 local + the jitter after a morning time",
          nxt == datetime.datetime(2026, 11, 18, 19, 0, tzinfo=TZ) + datetime.timedelta(minutes=j), nxt)
    late = slots.next_slot(sid, datetime.datetime(2026, 11, 18, 21, 0, tzinfo=TZ))
    check("next_slot: after the evening slot → tomorrow 07:00 + jitter",
          late == datetime.datetime(2026, 11, 19, 7, 0, tzinfo=TZ) + datetime.timedelta(minutes=j), late)
    with patch.dict(os.environ, {"MONEY_SYNC_TIMES": "12:30"}):
        noon = slots.next_slot(sid, TODAY)
    with patch.dict(os.environ, {"MONEY_SYNC_TIMES": "garbage"}):
        fallback = config.sync_times()
    check("MONEY_SYNC_TIMES: its slots; a malformed value falls back to 07:00,19:00",
          noon == datetime.datetime(2026, 11, 18, 12, 30, tzinfo=TZ) + datetime.timedelta(minutes=j)
          and fallback == [datetime.time(7), datetime.time(19)], noon)
    night = slots.out_of_quiet_hours(sid, datetime.datetime(2026, 11, 18, 2, 10, tzinfo=TZ))
    check("quiet hours: 02:10 → 06:00 + |jitter|",
          night == datetime.datetime(2026, 11, 18, 6, 0, tzinfo=TZ) + datetime.timedelta(minutes=abs(j)), night)
    check("quiet hours: daytime stays", slots.out_of_quiet_hours(sid, TODAY) == TODAY)

    far = store.iso(store.utc_now() + datetime.timedelta(days=2))
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET next_sync_at = ?, state = 'ok' WHERE id = ?", (far, sid))
        conn.commit()
    with patch.object(slots, "out_of_quiet_hours", side_effect=lambda s, w: w):  # whatever hour the test runs at
        due = [schedule.request_soon(ADMIN, sid, minutes=30 + i) for i in range(5)]
        sooner = schedule.request_soon(ADMIN, sid, minutes=500)
        bob_try = schedule.request_soon(BOB, sid)
    check("request_soon: pulls the next sync to now + minutes", due[0] is not None
          and abs((store.parse(due[0]) - store.utc_now()).total_seconds() - 1800) < 60, due)
    check("request_soon: a later request keeps the sooner time and is not counted",
          due[1] == due[0] and sooner == due[0])
    source = store.get_source(ADMIN, sid)
    check("request_soon: counted per local day", source["signal_syncs"] == 1
          and source["signal_syncs_day"] == clock.now().date().isoformat(), source)
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET next_sync_at = ?, signal_syncs = 4 WHERE id = ?", (far, sid))
        conn.commit()
    check("request_soon: at most 4 pulls a day, then refused", schedule.request_soon(ADMIN, sid) is None
          and store.get_source(ADMIN, sid)["next_sync_at"] == far)
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET signal_syncs_day = '2026-01-01' WHERE id = ?", (sid,))
        conn.commit()
    check("request_soon: a new day starts the count again", schedule.request_soon(ADMIN, sid) is not None
          and store.get_source(ADMIN, sid)["signal_syncs"] == 1)
    check("request_soon: another user's source is refused", bob_try is None)
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET state = 'needs_attention', state_reason = 'fix it', "
                     "next_sync_at = NULL WHERE id = ?", (sid,))
        conn.commit()
    check("request_soon: refused while the login needs attention", schedule.request_soon(ADMIN, sid) is None)
    try:
        schedule.sync_now(ADMIN, sid)
        check("sync_now: refused for needs_attention", False)
    except schedule.NeedsAttention as e:
        check("sync_now: refused for needs_attention (with the reason)", e.reason == "fix it")
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET state = 'ok', last_manual_at = ? WHERE id = ?",
                     (store.iso(store.utc_now() - datetime.timedelta(minutes=4)), sid))
        conn.commit()
    try:
        schedule.sync_now(ADMIN, sid)
        check("sync_now: a second manual sync within 10 minutes is refused", False)
    except schedule.TooSoon as e:
        check("sync_now: a second manual sync within 10 minutes is refused (retry_after ≈ 6 min)",
              340 <= e.retry_after <= 361, e.retry_after)
    try:
        schedule.sync_now(BOB, sid)
        check("sync_now: another user's source is unknown", False)
    except LookupError:
        check("sync_now: another user's source is unknown", True)


async def main() -> None:
    test_keys_and_dates()
    test_pending_and_changes()
    test_scrub_and_rules()
    test_categories()
    await test_rules_and_undo()
    test_reads()
    test_slots()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
