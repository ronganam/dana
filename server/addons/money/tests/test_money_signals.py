"""Money signals: phone payment notifications → provisional transactions, reconciliation with the scraped rows, the signals API.
Run: bin/test test_money_signals

Made-up merchants, amounts and cards. The first admin and Bob have
``money`` (a Cal card each); Carl has not. Covers: the Google Wallet parser
(₪ / $ / € / the Hebrew-locale form with bidi marks / thousands, a refund, a
non-payment) and the generic card-app parser (Hebrew and English, a bill
notice, nothing to go on); the phone addon's notification observers called
from ``processing.schedule`` for every user, ``signals.observe`` acting only
for users with ``money`` and only on their own notifications; a Wallet
payment → a provisional row on the card with those digits (time, date,
merchant, notification) and ``request_soon`` for that card's source; a
foreign amount, an unknown card, a refund or a week-old notification →
only the signal; ``POST /api/money/signals`` per user (two users with
``money``, 403 ``not_connected`` without), its validation, dedupe by key +
text, unparsed pushes and other apps' pushes only stored, texts redacted and scrubbed; Wallet and the
card's app announcing one payment → one provisional row; reconciliation
after a sync (the merge keeps the time and the notification, points the
signals at the scraped row, learns the alias; the next payment at that
merchant is named and categorised at once; no double counting after a
second sync; a provisional row survives a sync it is not in); a signal
arriving after its row was scraped; a pending row replaced by its completed
one (the signal follows); ``unmatched`` 5 days after the date; signals kept
60 days and removed with the user's money data.
"""

import asyncio
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_signals_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake logins
os.environ["API_TOKEN"] = "money-signals-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Robin"

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import clock, llm  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import api as money_api, classify, hooks, ingest, schedule, signals, sources, store  # noqa: E402
from addons.phone.notifications import ingest as notification_ingest, observers, processing  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ADMIN, "money")
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, CARL)}

# Sources are added before the event loop runs, so no sync starts.
CAL = sources.add(ADMIN, "visaCal", {"username": "robin.cal", "password": "pw-cal-1"})
BOB_CAL = sources.add(BOB, "visaCal", {"username": "bob.cal", "password": "pw-cal-bob"})

TZ = clock.get_timezone()
NOW = clock.now()
TODAY = NOW.date()
SOON: List[Tuple[str, str, float, str]] = []


def fake_request_soon(user, source_id: str, minutes: float = 30, reason: str = "signal") -> Optional[str]:
    SOON.append((user.id, source_id, minutes, reason))
    return None


def ms(dt: datetime.datetime) -> int:
    return int(dt.timestamp() * 1000)


def cal(identifier: Optional[str], day: datetime.date, amount: float, description: str, category: str = "",
        at: Optional[datetime.time] = None, status: str = "completed") -> Dict[str, Any]:
    """A Cal row bought on ``day`` (at ``at`` local, else no time), charged next month."""
    when = datetime.datetime.combine(day, at or datetime.time(0), TZ)
    charge = datetime.datetime.combine(day + datetime.timedelta(days=30), datetime.time(0), TZ)
    return {"identifier": identifier, "type": "normal", "status": status, "date": when.isoformat(),
            "processedDate": charge.isoformat(), "originalAmount": amount, "originalCurrency": "₪",
            "chargedAmount": amount, "chargedCurrency": "₪", "description": description, "memo": "",
            "category": category, "rawTransaction": {"trnPurchaseDate": when.replace(tzinfo=None).isoformat(),
                                                     "branchCodeDesc": category}}


def pull(number: str, txns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"accountNumber": number, "balance": -100.0, "txns": txns}]


# The cards exist from an earlier sync (an old row each).
ingest.apply(ADMIN, CAL, pull("8763", [cal("90000000001", TODAY - datetime.timedelta(days=40), -10.0, "דמי כרטיס")]))
ingest.apply(BOB, BOB_CAL, pull("4411", [cal("91000000001", TODAY - datetime.timedelta(days=40), -10.0, "דמי כרטיס")]))


def rows(user, number: str = "8763") -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT t.* FROM money_transactions t JOIN money_accounts a ON a.id = t.account_id "
            "WHERE t.user_id = ? AND a.number = ? ORDER BY t.purchase_date, t.id", (user.id, number))]


def by_amount(user, amount: int, number: str = "8763") -> List[Dict[str, Any]]:
    return [r for r in rows(user, number) if r["amount"] == amount]


def sigs(user) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM money_signals WHERE user_id = ? ORDER BY created_at, rowid",
                                              (user.id,))]


def wallet(user, title: str, text: str, when: datetime.datetime, key: str) -> List[str]:
    """Store a Google Wallet notification of ``user`` the way the phone's batch does; returns the new ids."""
    out = notification_ingest.ingest(user, [{"key": key, "package": signals.WALLET_PACKAGE, "app_name": "Google Wallet",
                                             "title": title, "text": text, "posted_at": ms(when)}])
    return out["ids"]


class FakeModel:
    """``llm.complete_json`` naming merchants from a table (category "other" otherwise)."""

    NAMES = {"רמי לוי - יבנה": "רמי לוי"}

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, prompt: str, schema: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        items = json.loads(prompt.rsplit("Rows (JSON):\n", 1)[1])
        return {"rows": [{"id": i["id"], "merchant": self.NAMES.get(i["description"], i["description"]),
                          "category": "other", "flow": None} for i in items]}


# --- Parsers -------------------------------------------------------------------------------------

def test_parsers() -> None:
    P = signals.Payment
    shapes = {
        ("RAMI LEVI", "₪350.30 with Mastercard Gold business ••8763"): P(-35030, "ILS", "8763", "RAMI LEVI"),
        ("Corner Shop", "$12.00 with Visa ••1234"): P(-1200, "USD", "1234", "Corner Shop"),
        ("Musée Shop", "€35.00 with Mastercard ••8763"): P(-3500, "EUR", "8763", "Musée Shop"),
        ("רמי לוי", "\u200f350.30 \u200f₪ באמצעות Mastercard ••8763"): P(-35030, "ILS", "8763", "רמי לוי"),
        ("Big Store", "₪1,234.50 with Mastercard ••8763"): P(-123450, "ILS", "8763", "Big Store"),
        ("Book Shop", "1\u00a0234,50\u00a0€ with Visa ••1111"): P(-123450, "EUR", "1111", "Book Shop"),
        ("Corner Shop", "Refund of ₪20.00 to Mastercard ••8763"): P(2000, "ILS", "8763", "Corner Shop"),
    }
    for (title, text), expected in shapes.items():
        got = signals.parse_wallet(title, text)
        check(f"wallet parser: {text!r}", got == expected, got)
    check("wallet parser: a notification without an amount is not a payment",
          signals.parse_wallet("Google Wallet", "Your card is ready to use") is None)

    pushes = {
        ("Cal", "בוצעה עסקה ב-קפה נואר בסך 42.00 ש\"ח בכרטיס 8763"): P(-4200, "ILS", "8763", "קפה נואר"),
        ("Cal", "Purchase of ₪42.00 at CAFE NOIR on card ending in 8763"): P(-4200, "ILS", "8763", "CAFE NOIR"),
        ("Max", "עסקה בבית העסק שופרסל דיל, סכום 99.90 NIS"): P(-9990, "ILS", None, "שופרסל דיל"),
        ("Cal", "קוד אימות [code] לעסקה בסך 120.00 ש״ח בכרטיס המסתיים ב-8763"): P(-12000, "ILS", "8763", None),
    }
    for (title, text), expected in pushes.items():
        got = signals.parse_push(title, text)
        check(f"push parser: {text!r}", got == expected, got)
    check("push parser: a bill notice is not a payment",
          signals.parse_push("Cal", "החיוב החודשי בכרטיס 8763 יעמוד על 3,450.81 ש\"ח") is None)
    check("push parser: an amount with neither a card nor a merchant is not read",
          signals.parse_push("Hapoalim", "יתרה: 1,000.00 ש\"ח") is None)
    check("push parser: no amount, nothing", signals.parse_push("Cal", "Your new card is on its way") is None)


# --- The observer and Wallet ---------------------------------------------------------------------

SEEN: List[Tuple[str, List[str]]] = []


def spy(user, ids: List[str]) -> None:
    SEEN.append((user.id, list(ids)))


async def test_wallet_observer() -> Tuple[str, str]:
    observers.register(spy)
    posted = NOW - datetime.timedelta(minutes=3)
    admin_ids = wallet(ADMIN, "RAMI LEVI", "₪350.30 with Mastercard Gold business ••8763", posted, "w-1")
    carl_ids = wallet(CARL, "RAMI LEVI", "₪350.30 with Mastercard Gold business ••8763", posted, "w-c")

    async def no_triage(user, nid) -> None:
        return None

    with patch.object(processing, "_run", no_triage):
        processing.schedule(ADMIN, admin_ids)
        processing.schedule(CARL, carl_ids)
        await observers.idle()
    check("observers: called from processing.schedule for every user with their new ids",
          (ADMIN.id, admin_ids) in SEEN and (CARL.id, carl_ids) in SEEN, SEEN)
    check("observer: nothing recorded for a user without money", not sigs(CARL))
    mine = sigs(ADMIN)
    check("observer: the Wallet notification recorded as a parsed signal",
          len(mine) == 1 and mine[0]["source"] == "notification" and mine[0]["notification_id"] == admin_ids[0]
          and (mine[0]["parsed"], mine[0]["amount"], mine[0]["currency"], mine[0]["card_last4"], mine[0]["merchant"])
          == (1, -35030, "ILS", "8763", "RAMI LEVI"), mine)
    prov = by_amount(ADMIN, -35030)
    p = prov[0] if prov else {}
    check("provisional: one row on the card with those digits",
          len(prov) == 1 and (p["source"], p["status"], p["ext_key"], p["flow"]) ==
          ("notification", "provisional", f"n:{mine[0]['id']}", "purchase"), prov)
    check("provisional: the posted time, its local date, the merchant and the notification",
          p.get("occurred_at") == posted.isoformat(timespec="seconds") and p.get("purchase_date") == posted.date().isoformat()
          and p.get("merchant") == "RAMI LEVI" and p.get("merchant_key") == "rami levi"
          and p.get("notification_id") == admin_ids[0], p)
    check("provisional: the signal points at it", mine[0]["transaction_id"] == p.get("id"))
    check("provisional: the card's sync is pulled to 30 minutes (reason signal)",
          SOON == [(ADMIN.id, CAL["id"], 30, "signal")], SOON)

    again = await asyncio.to_thread(signals.observe, ADMIN, admin_ids)
    check("observer: a notification already recorded is not recorded again", again == 0 and len(sigs(ADMIN)) == 1)
    check("observer: another user's notification ids are ignored",
          await asyncio.to_thread(signals.observe, ADMIN, carl_ids) == 0 and len(sigs(ADMIN)) == 1)

    SOON.clear()
    later = NOW - datetime.timedelta(minutes=2)
    foreign = wallet(ADMIN, "Corner Shop", "$12.00 with Mastercard ••8763", later, "w-2")
    other_card = wallet(ADMIN, "Lunch Place", "₪48.00 with Visa ••9999", later, "w-3")
    refund = wallet(ADMIN, "Corner Shop", "Refund of ₪20.00 to Mastercard ••8763", later, "w-4")
    old = wallet(ADMIN, "Old Shop", "₪66.00 with Mastercard ••8763", NOW - datetime.timedelta(days=6), "w-5")
    await asyncio.to_thread(signals.observe, ADMIN, foreign + other_card + refund + old)
    new = sigs(ADMIN)[1:]
    check("signal only: a foreign amount, another card, a refund, a week-old payment",
          len(new) == 4 and all(s["parsed"] == 1 and s["transaction_id"] is None for s in new)
          and not [r for r in rows(ADMIN) if r["amount"] in (-1200, -4800, 2000, -6600)] and not SOON, new)
    return admin_ids[0], mine[0]["id"]


# --- The API -------------------------------------------------------------------------------------

async def post(client: TestClient, user, body: Any) -> Tuple[int, Any]:
    resp = await client.post("/api/money/signals", json=body, headers={"Authorization": f"Bearer {TOKENS[user.id]}"})
    try:
        return resp.status, await resp.json()
    except Exception:
        return resp.status, await resp.text()


async def test_api(client: TestClient) -> None:
    status, body = await post(client, CARL, {"notifications": []})
    check("api: 403 not_connected without money", status == 403 and body.get("error") == "not_connected", (status, body))
    status, body = await post(client, ADMIN, {"notifications": "nope"})
    check("api: notifications must be a list", status == 400 and body.get("error") == "invalid", (status, body))

    SOON.clear()
    at = NOW - datetime.timedelta(minutes=1)
    items = [
        {"key": "0|com.onoapps.cal4u|1|null|10", "package": "com.onoapps.cal4u", "app_name": "Cal", "title": "Cal",
         "text": "בוצעה עסקה ב-קפה נואר בסך 42.00 ש\"ח בכרטיס 8763", "posted_at": ms(at)},
        {"key": "0|com.onoapps.cal4u|2|null|10", "package": "com.onoapps.cal4u", "app_name": "Cal", "title": "Cal",
         "text": "דף הפירוט החודשי שלך מוכן לצפייה", "posted_at": ms(at)},
        {"key": "0|com.ideomobile.hapoalim|3|null|10", "package": "com.ideomobile.hapoalim", "title": "הפועלים",
         "text": "קוד אימות 482913 לכניסה לחשבון", "posted_at": ms(at)},
        {"key": "0|com.ideomobile.hapoalim|4|null|10", "package": "com.ideomobile.hapoalim", "title": "הפועלים",
         "text": "הועברו 500.00 ש\"ח מאת ת.ז. 123456782", "posted_at": ms(at)},
        {"key": "x", "title": "no package", "text": "₪5.00"},
        {"key": "y", "package": "com.max.android", "title": "", "text": ""},
        "not an object",
    ]
    status, body = await post(client, ADMIN, {"notifications": items})
    check("api: 200 {accepted, duplicates}; items without a package or any text dropped",
          status == 200 and body == {"accepted": 4, "duplicates": 0}, (status, body))
    stored = {s["key"]: s for s in sigs(ADMIN) if s["source"] == "phone_app"}
    cafe = by_amount(ADMIN, -4200)
    check("api: a parsed push on the user's card → a provisional row and a sooner sync",
          len(cafe) == 1 and cafe[0]["status"] == "provisional" and cafe[0]["merchant"] == "קפה נואר"
          and cafe[0]["notification_id"] is None and SOON == [(ADMIN.id, CAL["id"], 30, "signal")], (cafe, SOON))
    statement = stored.get("0|com.onoapps.cal4u|2|null|10") or {}
    check("api: a push that does not parse is only stored",
          statement.get("parsed") == 0 and statement.get("transaction_id") is None and statement.get("text"), statement)
    code = (stored.get("0|com.ideomobile.hapoalim|3|null|10") or {}).get("text", "")
    sender = (stored.get("0|com.ideomobile.hapoalim|4|null|10") or {}).get("text", "")
    check("api: texts stored with codes redacted and ID numbers scrubbed",
          "482913" not in code and "[code]" in code and "123456782" not in sender and "[id]" in sender, (code, sender))
    status, body = await post(client, ADMIN, {"notifications": items[:4]})
    check("api: the phone re-sending what is still in the shade → duplicates (same key and text)",
          status == 200 and body == {"accepted": 0, "duplicates": 4} and len(by_amount(ADMIN, -4200)) == 1, (status, body))
    status, body = await post(client, ADMIN, {"notifications": [
        {"key": "0|com.example.lookalike|1|null|10", "package": "com.example.lookalike", "title": "Cal",
         "text": "בוצעה עסקה ב-חנות מזויפת בסך 77.00 ש\"ח בכרטיס 8763", "posted_at": ms(at)}]})
    other = next((s for s in sigs(ADMIN) if s["key"] == "0|com.example.lookalike|1|null|10"), {})
    check("api: a push from any other app (even titled Cal) is only stored, never a transaction",
          body == {"accepted": 1, "duplicates": 0} and other.get("parsed") == 0
          and other.get("transaction_id") is None and not by_amount(ADMIN, -7700), (body, other))
    changed = dict(items[0], text="בוצעה עסקה ב-קפה נואר בסך 42.00 ש\"ח בכרטיס 8763.")
    status, body = await post(client, ADMIN, {"notifications": [changed]})
    check("api: the same key with another text is a new signal (one payment within minutes: still one row)",
          body == {"accepted": 1, "duplicates": 0} and len(by_amount(ADMIN, -4200)) == 1, body)

    # Wallet and Cal's app announcing one payment → one provisional row.
    status, body = await post(client, ADMIN, {"notifications": [
        {"key": "0|com.onoapps.cal4u|5|null|10", "package": "com.onoapps.cal4u", "title": "Cal",
         "text": "בוצעה עסקה ב-רמי לוי בסך 350.30 ש\"ח בכרטיס 8763", "posted_at": ms(NOW - datetime.timedelta(minutes=2))}]})
    rami = by_amount(ADMIN, -35030)
    linked = [s for s in sigs(ADMIN) if s["amount"] == -35030]
    check("one payment from two apps: a single provisional row, both signals on it",
          len(rami) == 1 and len(linked) == 2 and all(s["transaction_id"] == rami[0]["id"] for s in linked), (rami, linked))

    # Bob: another card number is only a signal; his own card gets his own row.
    SOON.clear()
    status, body = await post(client, BOB, {"notifications": [
        {"key": "b1", "package": "com.onoapps.cal4u", "title": "Cal", "text": "עסקה ב-קפה נואר בסך 42.00 ש\"ח בכרטיס 8763",
         "posted_at": ms(at)},
        {"key": "b2", "package": "com.onoapps.cal4u", "title": "Cal", "text": "עסקה ב-מאפייה בסך 18.00 ש\"ח בכרטיס 4411",
         "posted_at": ms(at)}]})
    check("api per user: Bob's pushes are his (a card he lacks → only a signal; his card → his row)",
          body == {"accepted": 2, "duplicates": 0} and len(sigs(BOB)) == 2 and len(by_amount(BOB, -1800, "4411")) == 1
          and len(by_amount(ADMIN, -4200)) == 1 and SOON == [(BOB.id, BOB_CAL["id"], 30, "signal")], (body, SOON))
    check("api per user: the same key under two users is two signals",
          sum(1 for s in sigs(ADMIN) if s["key"] == "b1") == 0 and sum(1 for s in sigs(BOB) if s["key"] == "b1") == 1)


# --- Reconciliation ------------------------------------------------------------------------------

async def run_sync(user, source, accounts) -> Dict[str, List[str]]:
    """What sync.py does after a pull: ingest, then the hooks; then wait for classification."""
    out = await asyncio.to_thread(ingest.apply, user, source, accounts)
    await hooks.run(user, source["id"], out["new"], out["changed"])
    await classify.idle()
    return out


async def test_reconcile(wallet_nid: str) -> None:
    cafe_time = (NOW - datetime.timedelta(minutes=1)).replace(second=0, microsecond=0)
    scraped = pull("8763", [
        cal("90000000010", TODAY, -350.30, "רמי לוי - יבנה", "מזון ומשקאות"),  # no exact time from Cal here
        cal("90000000011", cafe_time.date(), -42.0, "קפה נואר", "מסעדות", at=cafe_time.time()),
    ])
    model = FakeModel()
    with patch.object(llm, "configured", return_value=True), patch.object(llm, "complete_json", model):
        await run_sync(ADMIN, CAL, scraped)
    rami, cafe = by_amount(ADMIN, -35030), by_amount(ADMIN, -4200)
    check("reconcile: no double counting (one row per payment, the scraped one)",
          len(rami) == 1 and len(cafe) == 1 and rami[0]["source"] == cafe[0]["source"] == "scrape", (rami, cafe))
    r = rami[0] if rami else {}
    wallet_sig = next(s for s in sigs(ADMIN) if s["notification_id"] == wallet_nid)
    check("reconcile: the scraped row takes the notification's exact time and the notification",
          r.get("occurred_at") == (NOW - datetime.timedelta(minutes=3)).isoformat(timespec="seconds")
          and r.get("notification_id") == wallet_nid, r)
    check("reconcile: every signal of the payment points at the scraped row",
          all(s["transaction_id"] == r.get("id") for s in sigs(ADMIN) if s["amount"] == -35030))
    check("reconcile: a scraped row's own time is kept",
          cafe and cafe[0]["occurred_at"] == cafe_time.isoformat(timespec="seconds"), cafe)
    with get_db_connection() as conn:
        alias = conn.execute("SELECT * FROM money_aliases WHERE user_id = ? AND alias_key = 'rami levi'",
                             (ADMIN.id,)).fetchone()
    check("reconcile: Wallet's spelling learned as an alias of the scraped merchant",
          alias is not None and alias["merchant_key"] == "רמי לוי יבנה", dict(alias) if alias else None)
    check("reconcile: then classified (Cal's category, the model's display name)",
          (r.get("category"), r.get("merchant")) == ("groceries", "רמי לוי") and wallet_sig["transaction_id"] == r.get("id"),
          r)

    SOON.clear()
    nid = wallet(ADMIN, "RAMI LEVI", "₪120.00 with Mastercard Gold business ••8763", NOW, "w-6")
    await asyncio.to_thread(signals.observe, ADMIN, nid)
    nxt = by_amount(ADMIN, -12000)
    check("alias: the next payment at that merchant is named and categorised at once",
          len(nxt) == 1 and (nxt[0]["status"], nxt[0]["merchant"], nxt[0]["category"], nxt[0]["category_source"])
          == ("provisional", "רמי לוי", "groceries", "memory"), nxt)

    with patch.object(llm, "configured", return_value=False):
        await run_sync(ADMIN, CAL, scraped)
    check("second sync: still one row per payment; an unmatched provisional row survives the sync",
          len(by_amount(ADMIN, -35030)) == 1 and len(by_amount(ADMIN, -4200)) == 1
          and [x["status"] for x in by_amount(ADMIN, -12000)] == ["provisional"])

    # The sync was quicker than the notification: the signal joins the scraped row.
    at = NOW.replace(second=0, microsecond=0) - datetime.timedelta(minutes=30)
    with patch.object(llm, "configured", return_value=False):
        await run_sync(ADMIN, CAL, pull("8763", [cal("90000000020", at.date(), -77.70, "חנות ספרים", "", at=at.time())]))
    SOON.clear()
    late = wallet(ADMIN, "BOOK SHOP", "₪77.70 with Mastercard ••8763", at + datetime.timedelta(minutes=1), "w-7")
    await asyncio.to_thread(signals.observe, ADMIN, late)
    books = by_amount(ADMIN, -7770)
    check("late signal: no provisional row; the scraped row gets the notification",
          len(books) == 1 and books[0]["source"] == "scrape" and books[0]["notification_id"] == late[0] and not SOON
          and next(s for s in sigs(ADMIN) if s["notification_id"] == late[0])["transaction_id"] == books[0]["id"], books)

    # A pending row the signal joined comes back completed under another key: the signal follows.
    with patch.object(llm, "configured", return_value=False):
        await run_sync(ADMIN, CAL, pull("8763", [cal(None, TODAY, -64.0, "מסעדת הים", "", status="pending")]))
    sea = wallet(ADMIN, "SEA RESTAURANT", "₪64.00 with Mastercard ••8763", NOW, "w-8")
    await asyncio.to_thread(signals.observe, ADMIN, sea)
    pending = by_amount(ADMIN, -6400)
    with patch.object(llm, "configured", return_value=False):
        await run_sync(ADMIN, CAL, pull("8763", [cal("90000000030", TODAY, -64.0, "מסעדת הים", "")]))
    done = by_amount(ADMIN, -6400)
    sig = next(s for s in sigs(ADMIN) if s["notification_id"] == sea[0])
    check("pending → completed: one row, the signal and its time follow the completed row",
          len(pending) == 1 and pending[0]["status"] == "pending" and len(done) == 1 and done[0]["status"] == "completed"
          and sig["transaction_id"] == done[0]["id"] and done[0]["notification_id"] == sea[0]
          and done[0]["occurred_at"] == NOW.isoformat(timespec="seconds"), (pending, done, sig))


async def test_unmatched_and_retention() -> None:
    later = TODAY + datetime.timedelta(days=signals.UNMATCHED_DAYS - 1)
    with patch.object(signals, "_today", return_value=later):
        await asyncio.to_thread(signals.reconcile, ADMIN, CAL["id"])
    check("unmatched: not before 5 days after the date", by_amount(ADMIN, -12000)[0]["status"] == "provisional")
    with patch.object(signals, "_today", return_value=TODAY + datetime.timedelta(days=signals.UNMATCHED_DAYS)):
        await asyncio.to_thread(signals.reconcile, ADMIN, CAL["id"])
    check("unmatched: a provisional row alone 5 days after its date → unmatched",
          by_amount(ADMIN, -12000)[0]["status"] == "unmatched", by_amount(ADMIN, -12000))
    check("unmatched: another user's provisional row is untouched",
          by_amount(BOB, -1800, "4411")[0]["status"] == "provisional")

    old = store.iso(store.utc_now() - datetime.timedelta(days=signals.KEEP_DAYS + 1))
    with get_db_connection() as conn:
        for user, sid in ((ADMIN, "old-admin"), (BOB, "old-bob")):
            conn.execute("INSERT INTO money_signals (id, user_id, source, key, package, title, text, created_at) "
                         "VALUES (?, ?, 'phone_app', ?, 'com.onoapps.cal4u', 'Cal', 'old', ?)", (sid, user.id, sid, old))
        conn.commit()
    await asyncio.to_thread(signals.record_push, ADMIN, [])
    left = {s["id"] for s in sigs(ADMIN) + sigs(BOB)}
    check("retention: the user's signals older than 60 days are dropped (only theirs)",
          "old-admin" not in left and "old-bob" in left)
    await asyncio.to_thread(signals.reconcile, BOB, BOB_CAL["id"])
    check("retention: and at each sync", "old-bob" not in {s["id"] for s in sigs(BOB)})
    before = len(sigs(ADMIN))
    await asyncio.to_thread(store.delete_all, BOB)
    check("disconnect: removing a user's money data removes their signals (only theirs)",
          not sigs(BOB) and len(sigs(ADMIN)) == before > 0)


async def main() -> None:
    test_parsers()
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    with patch.object(schedule, "request_soon", fake_request_soon):
        wallet_nid, _ = await test_wallet_observer()
        async with TestClient(TestServer(app)) as client:
            await test_api(client)
        await test_reconcile(wallet_nid)
        await test_unmatched_and_retention()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
