"""Money savings: every detector with exact numbers, the lifecycle and Undo, outcomes, targets, price checks, the API, the tools, Now, the weekly review line, the insight card and the Bank of Israel data.
Run: bin/test test_money_savings   (WRITE_WEB_FIXTURE=1 writes tests/fixtures/money_savings_web.json)

A synthetic 13 months, every name and amount made up, with today fixed at Wed
18 Nov 2026 09:30 (Asia/Jerusalem). Robin (the first admin) has a bank account,
a card and an investment account: a phone bill ₪69 → ₪109, a bimonthly
חברת החשמל series, ₪25,000 idle cash and a "כספית" fund, AI subscriptions in
dollars and a trip in euros (a fake Bank of Israel table gives a known
markup), a monthly municipality, monthly and yearly insurance, other
subscriptions, a mortgage, bank fees, eating out creeping up, a 6-orders-in-4-
days spree, a small cloud bill rising, late-night purchases, a parking fine
and a late fee. Bob has money and one card (a phone bill ₪50 → ₪80); Carl has
no money. Price checks and the Bank of Israel fetcher are fakes: nothing
touches the network.

Covers: each detector's item and numbers; ranking; the lifecycle (each status,
Undo, an item kept after its detector stops firing, "open" giving it back to
Dana, snoozes ending); outcomes (confirmed / waiting / no_change, a cancelled
subscription's missing charges, only a bill's own charges, a big drop
confirmed by a second charge, a supplier's bill for electricity, money set
aside for the cushion); targets (set / edit / remove, progress, saved, a
past month judged by the target of its time); the check endpoint (the real
market.start on the event loop, needs_answer, 409 / 429 / 503, 400 without a
check or for refused params, the telecom params, offers costed for the
user's lines, a bundle not compared); ATM cash never a penalty; overlaps
from subscriptions only; every supported currency fetched or none; the
saving context chat; isolation (two users with money, 404 for the
other's keys and undo, 403 not_connected); the tools; Now cards and acting
on them; the weekly review line; the Overview insight card; Bank of Israel
parsing, caching and a failed fetch leaving its items out; delete_all.
"""

import asyncio
import dataclasses
import datetime
import json
import os
import re
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlparse

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_savings_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-savings-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Robin"
sys.path.insert(0, ROOT)

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import actions_api, clock, context_chats, now as now_screen, session_runner, undo, weekly_review  # noqa: E402
from core.cards import ActError  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.addons import store as connections  # noqa: E402
from core.addons import registry  # noqa: E402
from core.addons.store import NotConnected  # noqa: E402
from addons.money import api as money_api, dashboard, ingest, providers, queries, savings, store, tools  # noqa: E402
from addons.money_il import market, reference  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


TZ = clock.get_timezone()
NOW = datetime.datetime(2026, 11, 18, 9, 30, tzinfo=TZ)
TODAY = NOW.date()
D = datetime.date
_clock = patch.object(clock, "now", lambda: NOW)
_clock.start()
# Stored times (first_seen_at, status_at) come from the money store's UTC clock: the same moment, so the
# test does not depend on the day it runs.
_utc_clock = patch.object(store, "utc_now", lambda: NOW.astimezone(datetime.timezone.utc))
_utc_clock.start()

ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(ROBIN, "money_il")
connections.connect(BOB, "money")
connections.connect(BOB, "money_il")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
STAMP = "2026-11-01T00:00:00+00:00"
WEB_FIXTURE = os.path.join(testenv.FIXTURES, "money_savings_web.json")

# --- Fakes: the Bank of Israel and the price checks --------------------------------------------------

FETCHES: List[str] = []
FETCH_FAILS = {"on": False}


def _business_days(start: D, end: D) -> List[D]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def fake_fetch(url: str) -> str:
    """A made-up Bank of Israel table: policy rate 4.5% until 4 Jan 2026, then 4.25%; USD 3.70 until
    30 Jun 2026, then 3.60; EUR 4.00 (business days only, so a weekend uses Friday's)."""
    FETCHES.append(url)
    if FETCH_FAILS["on"]:
        raise OSError("network down (test)")
    q = parse_qs(urlparse(url).query)
    start, end = D.fromisoformat(q["startperiod"][0]), D.fromisoformat(q["endperiod"][0])
    lines = ["﻿DATAFLOW,SERIES_CODE,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE"]
    for d in _business_days(start, end):
        if "MNT_RIB_BOI_D" in url:
            value = 4.5 if d < D(2026, 1, 5) else 4.25
        elif "RER_USD_ILS" in url:
            value = 3.70 if d < D(2026, 7, 1) else 3.60
        elif "RER_EUR_ILS" in url:
            value = 4.00
        else:
            continue
        lines.append(f"BOI.STATISTICS:X(1.0),S,{d.isoformat()},{value},ILS")
    return "\n".join(lines) + "\n"


_fetch = patch.object(reference, "fetch_csv", fake_fetch)
_fetch.start()

CHECKS: Dict[Tuple[str, str], Dict[str, Any]] = {}   # (topic, params json) → the latest check
STARTS: List[Tuple[str, str, Dict[str, Any]]] = []
START_MODE = {"raise": None}


def _pkey(topic: str, params: Dict[str, Any]) -> Tuple[str, str]:
    return topic, json.dumps(params, sort_keys=True, ensure_ascii=False)


def fake_latest(user, topic: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return CHECKS.get((user.id,) + _pkey(topic, params))  # type: ignore[arg-type]


def fake_start(user, topic: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if START_MODE["raise"] is not None:
        raise START_MODE["raise"]
    STARTS.append((user.id, topic, dict(params)))
    return {"state": "checking"}


def set_check(user, topic: str, params: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    CHECKS[(user.id,) + _pkey(topic, params)] = {  # type: ignore[index]
        "topic": topic, "params": params, "state": "ok", "started_at": "2026-11-18T07:00:00+00:00",
        "checked_at": "2026-11-18T07:01:00+00:00", "stale": False, "items": items,
        "sources": [{"title": "השוואת מחירים לדוגמה", "url": "https://example.com/prices"}], "error": None}


# money_il's price checks and rates, as registered; the fakes replace them (the same addon re-registers).
IL_CHECKS, IL_RATES = providers.price_checks(ROBIN), providers.rates(ROBIN)
assert IL_CHECKS is not None and IL_RATES is not None


@contextmanager
def price_checks(latest: Any, start: Any) -> Iterator[None]:
    """money_il's price checks with ``latest`` / ``start`` in place of the fakes for a while."""
    providers.register_price_checks(dataclasses.replace(IL_CHECKS, latest=latest, start=start))
    try:
        yield
    finally:
        providers.register_price_checks(dataclasses.replace(IL_CHECKS, latest=fake_latest, start=fake_start))


providers.register_price_checks(dataclasses.replace(IL_CHECKS, latest=fake_latest, start=fake_start))

SOURCE = {"title": "דוגמה", "url": "https://example.com/offer"}
TELECOM = [
    {"provider": "סלולר המרכז", "plan": "גלישה 500", "price": 4_990, "promo_price": 2_990, "promo_months": 6,
     "avg_12": 3_990, "conditions": "ללא התחייבות", "source": SOURCE},
    {"provider": "תקשורת הדרום", "plan": "בסיסי", "price": 3_500, "promo_price": None, "promo_months": None,
     "avg_12": 3_500, "conditions": "", "source": SOURCE},
    {"provider": "רשת הגליל", "plan": "פלוס", "price": 5_500, "promo_price": 4_500, "promo_months": 12,
     "avg_12": 4_500, "conditions": "", "source": SOURCE},
    {"provider": "קו הים", "plan": "מיני", "price": 6_000, "promo_price": None, "promo_months": None,
     "avg_12": 6_000, "conditions": "", "source": SOURCE},
]
ELECTRICITY = [
    {"supplier": "חשמל הצפון", "plan": "כל היום", "discount_pct": 7.0, "hours": "24 שעות", "days": "כל השבוע",
     "smart_meter": False, "source": SOURCE},
    {"supplier": "אנרגיית הדרום", "plan": "לילה", "discount_pct": 20.0, "hours": "23:00–07:00", "days": "כל השבוע",
     "smart_meter": True, "source": SOURCE},
    {"supplier": "חשמל הגבעה", "plan": "יום", "discount_pct": 15.0, "hours": "07:00–17:00", "days": "א׳–ה׳",
     "smart_meter": True, "source": SOURCE},
]
FX_CARDS = [
    {"name": "כרטיס הצפון", "kind": "card", "fx_fee_pct": 0.0, "monthly_fee": 0, "conditions": "", "source": SOURCE},
    {"name": "ארנק הדרום", "kind": "wallet", "fx_fee_pct": 0.5, "monthly_fee": 0, "conditions": "", "source": SOURCE},
    {"name": "בנק הגבעה", "kind": "bank", "fx_fee_pct": 1.0, "monthly_fee": 990, "conditions": "", "source": SOURCE},
]

# --- The dataset ----------------------------------------------------------------------------------------


def account(user, kind: str, number: str, company: str, **kw: Any) -> str:
    aid = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
                     "next_charge, next_charge_date, billed_from_account_id, details, created_at, updated_at) "
                     "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)",
                     (aid, user.id, company, kind, number, kw.get("balance"), kw.get("balance_at"), kw.get("billed_from"),
                      json.dumps(kw["details"]) if kw.get("details") else None, STAMP, STAMP))
    return aid


BANK_ROWS: List[Tuple[D, int]] = []


def txn(user, aid: str, day: D, amount: int, desc: str, category: Optional[str], flow: str, *,
        orig: Optional[int] = None, currency: str = "ILS", at: Optional[datetime.datetime] = None) -> str:
    tid = uuid.uuid4().hex
    occurred = (at or datetime.datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ)).isoformat()
    with store.db() as conn:
        conn.execute(
            "INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, occurred_at, "
            "purchase_date, charge_date, amount, orig_amount, orig_currency, description, merchant, merchant_key, memo, "
            "category, category_source, flow, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'scrape', 'completed', ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, '{}', ?, ?)",
            (tid, user.id, aid, f"x:{tid}", occurred, day.isoformat(), amount, orig if orig is not None else amount,
             currency, desc, desc, ingest.merchant_key(desc), category, "model" if category else None, flow, STAMP, STAMP))
    if aid == BANK:
        BANK_ROWS.append((day, amount))
    return tid


def delete_rows(user, desc: str, after: Optional[str] = None) -> None:
    with store.db() as conn:
        conn.execute("DELETE FROM money_transactions WHERE user_id = ? AND description = ? AND purchase_date > ?",
                     (user.id, desc, after or "0000"))


def months(start: D, end: D) -> List[D]:
    out, m = [], start
    while m <= end:
        out.append(m)
        m = D(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


BANK = ""
BANK = account(ROBIN, "bank", "12-345-678901", "hapoalim", balance=4_500_000, balance_at="2026-11-18T05:00:00+00:00")
CARD = account(ROBIN, "card", "8763", "visaCal", billed_from=BANK)
INV = account(ROBIN, "investment", "77-123456", "psagot", balance=3_500_000, balance_at="2026-11-18T05:00:00+00:00",
              details={"value": 3_500_000, "cash": 2_500_000})
BOB_CARD = account(BOB, "card", "4580", "visaCal")
with store.db() as conn:
    conn.execute("INSERT INTO money_holdings (id, user_id, account_id, security, name, quantity, price, currency, value, "
                 "cost, profit, profit_pct, weight, raw, updated_at) VALUES (?, ?, ?, '5111111', 'קרן כספית הצפון', "
                 "1000, 10.0, 'ILS', 1000000, 990000, 10000, 1.0, 28.6, '{}', ?)", (uuid.uuid4().hex, ROBIN.id, INV, STAMP))
    for day, kind, action in (("2026-10-01", "buy", "קניה"), ("2026-09-15", "dividend", "דיבידנד"),
                              ("2026-06-01", "interest", "ריבית")):
        conn.execute("INSERT INTO money_trades (id, user_id, account_id, ext_key, date, action, kind, amount, raw, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1000, '{}', ?, ?)",
                     (uuid.uuid4().hex, ROBIN.id, INV, f"t:{day}", day, action, kind, STAMP, STAMP))

ALL = months(D(2025, 10, 1), D(2026, 11, 1))
for m in ALL:
    txn(ROBIN, BANK, m, 1_500_000, "משכורת חברת הגבעה", "salary", "income")
    txn(ROBIN, BANK, m, -500_000, "משכנתא הצפון", "housing", "loan")
    txn(ROBIN, BANK, m.replace(day=5), -60_000, "עיריית גבעת אורנים", "utilities", "purchase")
    txn(ROBIN, BANK, m.replace(day=15), -800_000, "כאל", None, "card_bill")
    txn(ROBIN, CARD, m.replace(day=10), -(6_900 if m < D(2026, 7, 1) else 10_900), "סלולר הצפון", "phone_internet", "purchase")
    txn(ROBIN, CARD, m.replace(day=7), -2_990, "סטרימינג הצפון", "subscriptions", "purchase")
CLOUD = [800, 800, 900, 900, 1_000, 1_000, 1_100, 1_200, 1_500, 2_000, 3_000, 4_000, 5_000, 6_000]
for m, amount in zip(ALL, CLOUD):
    txn(ROBIN, CARD, m.replace(day=3), -amount, "ענן הצפון", "subscriptions", "purchase")
for i, m in enumerate(ALL[:-1]):  # to October 2026
    txn(ROBIN, CARD, m.replace(day=20), -18_000, "ביטוח הדרום בריאות", "insurance", "purchase")
    txn(ROBIN, CARD, m.replace(day=20), -(28_000 if i % 2 == 0 else 32_000), "חנות הבית", "shopping", "purchase")
for d, amount in ((D(2025, 11, 12), 52_000), (D(2026, 1, 21), 61_000), (D(2026, 3, 19), 48_000),
                  (D(2026, 5, 20), 45_000), (D(2026, 7, 22), 69_000), (D(2026, 9, 21), 72_000), (D(2026, 11, 16), 54_000)):
    txn(ROBIN, CARD, d, -amount, "חברת החשמל לישראל", "utilities", "purchase")
txn(ROBIN, CARD, D(2025, 12, 28), -240_000, "ביטוח הדרום חובה", "insurance", "purchase")
txn(ROBIN, CARD, D(2025, 11, 28), -120_000, "ביטוח הדרום דירה", "insurance", "purchase")
txn(ROBIN, CARD, D(2026, 3, 15), -48_000, "תוכנת הדרום שנתי", "subscriptions", "purchase")
# Dollars: $20 a month each (two AI services), charged ₪76.30 at 3.70 and ₪74.20 at 3.60 (a ₪2.30 / ₪2.20 markup).
FX: List[Tuple[D, int, int, str]] = []   # (day, charged agorot, foreign cents, currency)
for m in months(D(2026, 3, 1), D(2026, 11, 1)):
    charged = -7_630 if m < D(2026, 7, 1) else -7_420
    txn(ROBIN, CARD, m.replace(day=5), charged, "CLAUDE PRO MONTHLY", "subscriptions", "purchase", orig=-2_000, currency="USD")
    FX.append((m.replace(day=5), -charged, 2_000, "USD"))
for m in months(D(2026, 6, 1), D(2026, 11, 1)):
    charged = -7_630 if m < D(2026, 7, 1) else -7_420
    txn(ROBIN, CARD, m.replace(day=12), charged, "CHATGPT PLUS PLAN", "subscriptions", "purchase", orig=-2_000, currency="USD")
    FX.append((m.replace(day=12), -charged, 2_000, "USD"))
for d, euros, desc in ((D(2026, 8, 3), 200, "HOTEL MARE BLU"), (D(2026, 8, 4), 150, "TRATTORIA SOLE"),
                       (D(2026, 8, 5), 300, "MUSEO CITTA"), (D(2026, 8, 6), 350, "MERCATO CENTRO")):
    txn(ROBIN, CARD, d, -euros * 412, desc, "travel", "purchase", orig=-euros * 100, currency="EUR")  # 3% over 4.00
    FX.append((d, euros * 412, euros * 100, "EUR"))
# Eating out: ₪400 a month, ₪900 from May, ₪660 in October, ₪300 so far in November.
for m in ALL:
    if m < D(2026, 5, 1):
        for day in (8, 22):
            txn(ROBIN, CARD, m.replace(day=day), -20_000, "מסעדת הגבעה", "restaurants", "purchase")
    elif m < D(2026, 10, 1):
        for day in (6, 16, 26):
            txn(ROBIN, CARD, m.replace(day=day), -20_000, "מסעדת הגבעה", "restaurants", "purchase")
        for day in (9, 19, 29):
            txn(ROBIN, CARD, m.replace(day=day), -10_000, "משלוחי הדרום", "delivery", "purchase")
    elif m < D(2026, 11, 1):
        for day in (6, 16):
            txn(ROBIN, CARD, m.replace(day=day), -20_000, "מסעדת הגבעה", "restaurants", "purchase")
        for day in (9, 19):
            txn(ROBIN, CARD, m.replace(day=day), -13_000, "משלוחי הדרום", "delivery", "purchase")
    else:
        txn(ROBIN, CARD, m.replace(day=6), -20_000, "מסעדת הגבעה", "restaurants", "purchase")
        txn(ROBIN, CARD, m.replace(day=9), -10_000, "משלוחי הדרום", "delivery", "purchase")
SPREE = ((D(2026, 9, 14), 12_000), (D(2026, 9, 15), 9_500), (D(2026, 9, 15), 15_000), (D(2026, 9, 16), 8_000),
         (D(2026, 9, 17), 11_000), (D(2026, 9, 17), 6_000))
for d, amount in SPREE:
    txn(ROBIN, CARD, d, -amount, "חנות הצפון אונליין", "shopping", "purchase")
LATE = (D(2026, 9, 2), D(2026, 9, 12), D(2026, 10, 3), D(2026, 10, 13), D(2026, 10, 23), D(2026, 11, 1), D(2026, 11, 8),
        D(2026, 11, 14))
for n, d in enumerate(LATE):
    desc, cat = ("אלקטרוניקה הדרום", "electronics") if n % 2 == 0 else ("בגדי הגבעה", "clothing")
    txn(ROBIN, CARD, d, -15_000, desc, cat, "purchase", at=datetime.datetime(d.year, d.month, d.day, 23, 30, tzinfo=TZ))


def add_fees() -> None:
    for m in ALL:
        txn(ROBIN, BANK, m, -1_090, "עמלת ניהול חשבון", "fees", "fee")


add_fees()
txn(ROBIN, CARD, D(2026, 4, 14), -10_000, "דוח חניה עיריית גבעת אורנים", "parking", "purchase")
txn(ROBIN, BANK, D(2026, 6, 3), -1_240, "ריבית פיגורים", "fees", "fee")
# Bob: a phone bill ₪50 → ₪80 from August.
for m in months(D(2026, 1, 1), D(2026, 11, 1)):
    txn(BOB, BOB_CARD, m.replace(day=10), -(5_000 if m < D(2026, 8, 1) else 8_000), "סלולר הדרום", "phone_internet", "purchase")


# --- Helpers --------------------------------------------------------------------------------------------

async def call(client: TestClient, method: str, path: str, who, body: Any = None) -> Tuple[int, Any]:
    resp = await client.request(method, path, json=body, headers={"Authorization": f"Bearer {TOKENS[who.id]}"})
    text = await resp.text()
    try:
        return resp.status, json.loads(text)
    except ValueError:
        return resp.status, text


def url(key: str, suffix: str = "") -> str:
    return f"/api/money/savings/{quote(key, safe='')}{suffix}"


def items_of(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {i["key"]: i for i in payload["items"]}


def row(user, key: str) -> Dict[str, Any]:
    with store.db() as conn:
        r = conn.execute("SELECT * FROM money_savings WHERE user_id = ? AND key = ?", (user.id, key)).fetchone()
    return dict(r) if r else {}


def set_row(user, key: str, **values: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in values)
    with store.db() as conn:
        conn.execute(f"UPDATE money_savings SET {sets} WHERE user_id = ? AND key = ?", (*values.values(), user.id, key))


PHONE = "bill:סלולר הצפון"
BOB_PHONE = "bill:סלולר הדרום"
ARNONA = "arnona:עיריית גבעת אורנים:2027"
RENEW_HOME = "renewal:ביטוח הדרום דירה:2026-11"
RENEW_CAR = "renewal:ביטוח הדרום חובה:2026-12"
SPREE_KEY = "spree:חנות הצפון אונליין:2026-09-14"


def balance_floor() -> int:
    """The bank's lowest end-of-day balance in the last 90 days, replayed backwards from today's balance."""
    lows = []
    d = TODAY - datetime.timedelta(days=90)
    while d <= TODAY:
        lows.append(4_500_000 - sum(a for day, a in BANK_ROWS if day > d))
        d += datetime.timedelta(days=1)
    return min(lows)


# --- The Bank of Israel ---------------------------------------------------------------------------------

def test_reference() -> None:
    reference.clear_cache()
    FETCHES.clear()
    rate = reference.policy_rate(TODAY)
    check("reference: the policy rate now and a year ago (the last business day on or before)",
          rate == {"rate": 4.25, "date": "2026-11-18", "year_ago": 4.5, "year_ago_date": "2025-11-18"}, rate)
    reference.policy_rate(TODAY)
    check("reference: a second read comes from the cache", len(FETCHES) == 1, FETCHES)
    check("reference: the cache file holds public data only, next to the DB", reference.cache_file().exists()
          and "MNT_RIB_BOI_D" in reference.cache_file().read_text())
    reference.clear_cache()
    reference.policy_rate(TODAY)
    check("reference: after a restart the day's answer is read from the file", len(FETCHES) == 1, FETCHES)
    usd = reference.fx_rates("usd", D(2026, 7, 1), D(2026, 7, 6))
    check("reference: FX rates per business day (BOM, extra columns), a weekend uses Friday's",
          usd == {"2026-07-01": 3.6, "2026-07-02": 3.6, "2026-07-03": 3.6, "2026-07-06": 3.6}
          and providers.rate_on(usd, "2026-07-05") == 3.6 and providers.rate_on(usd, "2026-06-20") is None, usd)
    check("reference: an unsupported currency is left out (the yen is quoted per 100)",
          reference.fx_rates("JPY", D(2026, 7, 1), D(2026, 7, 6)) == {})
    reference.clear_cache()
    FETCH_FAILS["on"] = True
    FETCHES.clear()
    check("reference: a failed fetch gives None", reference.policy_rate(D(2026, 11, 17)) is None)
    reference.policy_rate(D(2026, 11, 17))
    check("reference: and is not retried at once", len(FETCHES) == 1, FETCHES)
    found = items_of(savings.overview(ROBIN, D(2026, 11, 17)))
    check("reference: without the rate the items that need it are left out (cash, cushion), nothing guessed",
          not any(k.startswith("cash:") or k == "cushion" for k in found) and "fees" in found
          and found[ARNONA]["saving"] is None, sorted(found))
    FETCH_FAILS["on"] = False
    reference.clear_cache()
    with store.db() as conn:  # that read stored first sightings dated today: start clean
        conn.execute("DELETE FROM money_savings")


# --- Detectors -------------------------------------------------------------------------------------------

def test_detectors(data: Dict[str, Any]) -> None:
    items = items_of(data)
    check("overview: as of today, the policy rate now and a year ago",
          data["as_of"] == "2026-11-18" and data["rate"] == {"policy": 4.25, "date": "2026-11-18", "year_ago": 4.5,
                                                            "year_ago_date": "2025-11-18"}, data["rate"])
    expected_save = {PHONE, "electricity", f"cash:{INV}", "cushion", f"fx:{CARD}", "fees", ARNONA, RENEW_HOME, RENEW_CAR,
                     "insurance_review", "subscriptions", "mortgage"}
    expected_watch = {"creep:eating_out", SPREE_KEY, "leak:ענן הצפון", "overlap:ai", "penalties", "late_night"}
    check("detectors: every way to save and every habit fires, nothing else",
          {k for k, i in items.items() if i["group"] == "save"} == expected_save
          and {k for k, i in items.items() if i["group"] == "watch"} == expected_watch, sorted(items))
    check("payload: the exact keys, in order",
          all(list(i) == ["key", "type", "group", "status", "status_at", "snooze_until", "first_seen", "new", "active",
                          "title", "summary", "saving", "at_stake", "effort", "timing", "evidence", "math", "steps",
                          "script", "questions", "market", "subitems", "scope", "target", "suggested_target", "outcome"]
              for i in items.values()), list(next(iter(items.values()))))
    check("payload: new items are open, active, first seen today",
          all(i["status"] == "open" and i["active"] and i["new"] and i["first_seen"] == "2026-11-18" for i in items.values()))
    p = items[PHONE]
    check("bill: ₪69 → ₪109 a month: ₪480 a year, basis increase; the year's cost at stake; a call",
          p["saving"] == {"low": 48_000, "high": 48_000, "basis": "increase"} and p["at_stake"] == 130_800
          and p["effort"] == {"minutes": 20, "how": "call"} and p["title"] == "סלולר הצפון went up ₪40 a month", p)
    check("bill: the evidence (14 charges, a search link), the maths, a Hebrew script with both prices",
          len(p["evidence"]["series"]) == 14 and p["evidence"]["link"] == {"view": "transactions", "q": "סלולר הצפון"}
          and p["math"][1] == "₪40.00 × 12 = ₪480.00 a year" and p["script"]["lang"] == "he"
          and "₪69" in p["script"]["text"] and "₪109" in p["script"]["text"], p["math"])
    check("bill: the telecom check needs the service first (questions, market state none)",
          [q["id"] for q in p["questions"]] == ["service"] and p["questions"][0]["answer"] is None
          and p["market"]["topic"] == "telecom" and p["market"]["state"] == "none"
          and p["market"]["needs_answer"] == "service", p["market"])
    e = items["electricity"]
    check("electricity: the typical bill ₪575 × 6 bills a year = ₪3,450 at stake, no saving before a price check",
          e["at_stake"] == 345_000 and e["saving"] is None and e["market"]["topic"] == "electricity"
          and e["market"]["params"] == {} and len(e["evidence"]["series"]) == 6, (e["at_stake"], e["saving"]))
    c = items[f"cash:{INV}"]
    check("cash: ₪25,000 × (4.25% − 0.3%) = ₪987.50 a year, measured; names the כספית fund already held",
          c["saving"] == {"low": 98_750, "high": 98_750, "basis": "measured"}
          and c["steps"][0]["text"] == "You already hold קרן כספית הצפון: buy more of it with the cash"
          and c["evidence"]["link"] == {"view": "investments"}, c["saving"])
    floor = balance_floor()
    fixed = dashboard.cashflow(dashboard.load(ROBIN), 12)["typical"]["fixed"]
    cu = items["cushion"]
    lo, hi = sorted((floor - fixed, floor - 300_000))
    check("cushion: the 90-day floor minus a month of fixed costs / ₪3,000, × 3.95%",
          cu["saving"] == {"low": savings._r(max(0, lo) * 3.95 / 100), "high": savings._r(hi * 3.95 / 100),
                           "basis": "measured"} and len(cu["evidence"]["series"]) == 91, (floor, fixed, cu["saving"]))
    f = items[f"fx:{CARD}"]
    base = sum(cents * (4.0 if cur == "EUR" else (3.70 if d < D(2026, 7, 1) else 3.60)) for d, _, cents, cur in FX)
    markup = sum(ch for _, ch, _, _ in FX) - base
    check("fx: 19 purchases, ₪5,243.50 charged, ₪153.50 over the Bank of Israel rate of each day (3.02%, median 3.06%)",
          round(markup) == 15_350 and f["saving"] == {"low": 15_350, "high": 15_350, "basis": "measured"}
          and f["evidence"]["lines"] == ["19 purchases in foreign currency in 12 months, ₪5,243.50 charged",
                                         "3.02% over the Bank of Israel rate in total, median 3.06% a purchase"],
          (f["saving"], f["evidence"]["lines"]))
    fe = items["fees"]
    check("fees: 12 account fees, ₪130.80 (the late fee is a penalty, not here), with a Hebrew script",
          fe["saving"] == {"low": 13_080, "high": 13_080, "basis": "measured"} and "מסלול" in fe["script"]["text"], fe["saving"])
    a = items[ARNONA]
    check("arnona: monthly municipality in the Nov–Feb window → the 2027 check for גבעת אורנים, ₪7,200 at stake",
          a["market"]["params"] == {"city": "גבעת אורנים", "year": 2027} and a["at_stake"] == 720_000
          and a["saving"] is None and a["timing"]["from"] == "2026-11-01", a["market"]["params"])
    r1, r2 = items[RENEW_HOME], items[RENEW_CAR]
    check("renewal: due from 60 days before (home in 10 days, car in 40), last year's amount at stake",
          r1["timing"] == {"from": "2026-09-29", "until": "2026-11-28", "label": "Renews around 28 Nov 2026"}
          and r1["at_stake"] == 120_000 and r2["timing"]["until"] == "2026-12-28" and r2["at_stake"] == 240_000, r1["timing"])
    check("renewal: compulsory car insurance links the regulator's calculator; the home policy doesn't",
          any(s["url"] == "https://car.cma.gov.il" for s in r2["steps"]) and not any(s["url"] for s in r1["steps"]))
    ins = items["insurance_review"]
    check("insurance review: ₪180 × 12 + ₪2,400 + ₪1,200 = ₪5,760 a year, with הר הביטוח",
          ins["at_stake"] == 576_000 and ins["saving"] is None and ins["steps"][0]["url"] == "https://harb.cma.gov.il", ins["at_stake"])
    s = items["subscriptions"]
    lines = {x["key"]: (x["amount"], x["cadence"], x["yearly"], x["choice"]) for x in s["subitems"]}
    check("subscriptions: 5 lines (monthly at the typical price, the yearly one), ₪3,219.60 a year",
          s["at_stake"] == 321_960 and lines == {
              "claude pro monthly": (7_420, "monthly", 89_040, None), "chatgpt plus plan": (7_420, "monthly", 89_040, None),
              "ענן הצפון": (5_000, "monthly", 60_000, None), "תוכנת הדרום שנתי": (48_000, "yearly", 48_000, None),
              "סטרימינג הצפון": (2_990, "monthly", 35_880, None)} and s["saving"] is None, lines)
    mo = items["mortgage"]
    check("mortgage: 12 payments at stake, the rate a year ago vs now, worth checking (no calculation)",
          mo["at_stake"] == 6_000_000 and mo["saving"] is None and "4.25% now, 4.5% a year ago" in " ".join(mo["evidence"]["lines"])
          and "דוח יתרות לסילוק" in mo["steps"][0]["text"], mo["evidence"]["lines"])
    cr = items["creep:eating_out"]
    check("creep: eating out ₪860 a month vs ₪400: ₪5,520 a year more, suggested target ₪650",
          cr["at_stake"] == 552_000 and cr["suggested_target"] == 65_000 and cr["scope"] == {"categories": ["restaurants", "delivery"]}
          and cr["target"] is None and len(cr["evidence"]["series"]) == 13, (cr["at_stake"], cr["suggested_target"]))
    sp = items[SPREE_KEY]
    check("spree: 6 orders at one shop in 4 days, ₪615 at stake, scoped to the shop",
          sp["title"] == "6 orders at חנות הצפון אונליין in 4 days" and sp["at_stake"] == 61_500
          and sp["scope"] == {"merchants": ["חנות הצפון אונליין"]}, sp["title"])
    lk = items["leak:ענן הצפון"]
    check("leak: the cloud bill rising (₪120 in 3 months vs ₪47): 12 months = ₪276",
          lk["at_stake"] == 27_600 and lk["title"] == "ענן הצפון is growing", (lk["at_stake"], lk["title"]))
    ov = items["overlap:ai"]
    check("overlap: Claude and ChatGPT both charged in 60 days, ₪1,780.80 a year, named as on the bill",
          ov["at_stake"] == 178_080 and ov["title"] == "2 AI assistants at once"
          and ov["summary"].startswith("CHATGPT PLUS PLAN and CLAUDE PRO MONTHLY: ₪1,780.80 a year")
          and ov["scope"] == {"merchants": ["chatgpt plus plan", "claude pro monthly"]}, ov)
    pe = items["penalties"]
    check("penalties: a parking fine and a late fee, ₪112.40", pe["at_stake"] == 11_240, pe["at_stake"])
    ln = items["late_night"]
    check("late night: 8 purchases after 23:00 in 90 days, ₪1,200 → ₪4,866.67 a year",
          ln["at_stake"] == 486_667 and ln["scope"]["hours"] == [23, 5], ln["at_stake"])
    order = [i["key"] for i in data["items"]]
    check("ranking: due items first (arnona, the renewals), then savings by money per minute of effort",
          order[:3] == [ARNONA, RENEW_CAR, RENEW_HOME] and order.index(f"cash:{INV}") < order.index(PHONE)
          < order.index("fees") < order.index(f"fx:{CARD}") < order.index("insurance_review"), order[:8])
    t = data["totals"]
    open_save = [i for i in data["items"] if i["group"] == "save" and i["saving"]]
    check("totals: the open ways to save with a saving, the habits to watch",
          t["count"] == len(open_save) == 5 and t["low"] == sum(i["saving"]["low"] for i in open_save)
          and t["high"] == sum(i["saving"]["high"] for i in open_save) and t["saved"] == 0 and t["watch_count"] == 6, t)


# --- Price checks ----------------------------------------------------------------------------------------

async def test_checks(client: TestClient) -> None:
    s, body = await call(client, "POST", url(PHONE, "/check"), ROBIN)
    check("check: the service question first → 400 needs_answer", s == 400 and body["error"] == "needs_answer"
          and body["question"]["id"] == "service", body)
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"answers": {"service": "fax"}})
    s2, _ = await call(client, "PATCH", url(PHONE), ROBIN, {"answers": {"colour": "blue"}})
    check("answers: an unknown option or question → 400", (s, s2) == (400, 400) and body["field"] == "answers", body)
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"answers": {"service": "mobile"}})
    check("answers: mobile → the lines question appears", s == 200 and [q["id"] for q in body["item"]["questions"]]
          == ["service", "lines"] and body["item"]["market"]["needs_answer"] == "lines", body.get("item", {}).get("questions"))
    s, body = await call(client, "POST", url(PHONE, "/check"), ROBIN)
    check("check: then the lines → 400 needs_answer", s == 400 and body["question"]["id"] == "lines", body)
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"answers": {"lines": 1}})
    STARTS.clear()
    s, body = await call(client, "POST", url(PHONE, "/check"), ROBIN)
    check("check: 202 checking, the telecom params carry only service, lines and the current price",
          s == 202 and body["state"] == "checking"
          and STARTS == [(ROBIN.id, "telecom", {"service": "mobile", "lines": 1, "current_price": 109})], (s, STARTS))
    for exc, status in ((providers.PriceCheckRunning(), 409), (providers.PriceCheckTooSoon(120), 429),
                        (providers.PriceCheckUnavailable("no model"), 503)):
        START_MODE["raise"] = exc
        s, body = await call(client, "POST", url(PHONE, "/check"), ROBIN)
        check(f"check: {type(exc).__name__} → {status}", s == status and (status != 429 or body["retry_after"] == 120), body)
    START_MODE["raise"] = None
    s, item = await call(client, "GET", url(PHONE), ROBIN)
    check("check: after 'unavailable' the item says so", s == 200 and item["market"]["state"] == "unavailable", item["market"])
    savings._unavailable.clear()
    s, body = await call(client, "POST", url("fees", "/check"), ROBIN)
    check("check: an item without a price check → 400 no_check", s == 400 and body["error"] == "no_check", body)
    set_check(ROBIN, "telecom", {"service": "mobile", "lines": 1, "current_price": 109}, TELECOM)
    s, item = await call(client, "GET", url(PHONE), ROBIN)
    check("telecom: (₪109 − the best ₪35 average) × 12 = ₪888, low from the 3rd best (₪45): ₪768; basis market",
          item["saving"] == {"low": 76_800, "high": 88_800, "basis": "market"} and item["market"]["state"] == "ok"
          and len(item["market"]["items"]) == 4, item["saving"])
    check("telecom: the script and the second step carry the best offer",
          "תקשורת הדרום" in item["script"]["text"] and "₪35" in item["script"]["text"]
          and "תקשורת הדרום" in item["steps"][1]["text"] and item["steps"][1]["url"] == SOURCE["url"], item["steps"][1])
    CAPTURED["item_check"] = item
    set_check(ROBIN, "electricity", {}, ELECTRICITY)
    set_check(ROBIN, "fx_card", {}, FX_CARDS)
    set_check(ROBIN, "arnona", {"city": "גבעת אורנים", "year": 2027},
              [{"found": True, "discount_pct": 2.0, "deadline": "2027-01-31", "method": "באתר העירייה", "source": SOURCE}])
    found = items_of(savings.overview(ROBIN))
    check("electricity: ₪3,450 × 95% × 7% (all day, no smart meter) = ₪229.43 to × 20% (the best) = ₪655.50",
          found["electricity"]["saving"] == {"low": 22_943, "high": 65_550, "basis": "market"}, found["electricity"]["saving"])
    check("fx: nets out each card's fee: the best saves all ₪153.50, the median ₪128.05",
          found[f"fx:{CARD}"]["saving"] == {"low": 12_805, "high": 15_350, "basis": "market"}, found[f"fx:{CARD}"]["saving"])
    a = found[ARNONA]
    check("arnona: 2% of ₪7,200 = ₪144 minus 5.5 months of 3.95% on it (₪130.35) = ₪13.65; the deadline",
          a["saving"] == {"low": 1_365, "high": 1_365, "basis": "market"} and a["timing"]["until"] == "2027-01-31", a["saving"])
    set_check(ROBIN, "arnona", {"city": "גבעת אורנים", "year": 2027},
              [{"found": True, "discount_pct": 1.5, "deadline": "2027-01-31", "method": "", "source": SOURCE}])
    a = items_of(savings.overview(ROBIN))[ARNONA]
    check("arnona: a discount smaller than the interest → no saving, the card says it isn't worth it",
          a["saving"] is None and "isn't worth it" in a["summary"], a["summary"])
    set_check(ROBIN, "arnona", {"city": "גבעת אורנים", "year": 2027},
              [{"found": True, "discount_pct": 2.0, "deadline": "2027-01-31", "method": "באתר העירייה", "source": SOURCE}])


async def test_check_route(client: TestClient) -> None:
    """The route with the real ``market.start`` (it needs the event loop) and a fake search."""
    async def fake_find(topic: str, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        return [dict(i) for i in ELECTRICITY], [dict(SOURCE)]

    with price_checks(market.latest, market.start), patch.object(market, "_find", fake_find), patch.object(market.llm, "can_search", lambda: True):
        s, body = await call(client, "POST", url("electricity", "/check"), ROBIN)
        await asyncio.gather(*list(market._running.values()))  # (often done already)
        stored = market.latest(ROBIN, "electricity", {})
        s2, again = await call(client, "POST", url("electricity", "/check"), ROBIN)
    check("check route: the real price check starts on the event loop → 202, and its result is stored",
          s == 202 and body == {"state": "checking"} and stored and stored["state"] == "ok"
          and [i["supplier"] for i in stored["items"]] == [i["supplier"] for i in ELECTRICITY]
          and stored["sources"] == [SOURCE], (s, body, stored))
    check("check route: pressed again at once → 429 with retry_after", s2 == 429 and again.get("retry_after", 0) > 0, again)
    START_MODE["raise"] = ValueError("bad params (test)")
    s, body = await call(client, "POST", url("electricity", "/check"), ROBIN)
    START_MODE["raise"] = None
    check("check route: params the price checks refuse → 400 invalid", s == 400 and body["error"] == "invalid", body)

    def refuse_arnona(user, topic: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if topic == "arnona":
            raise ValueError("a town the checks don't take (test)")
        return fake_latest(user, topic, params)

    with price_checks(refuse_arnona, fake_start):
        s, data = await call(client, "GET", "/api/money/savings", ROBIN)
        s2, body = await call(client, "POST", url(ARNONA, "/check"), ROBIN)
    found = items_of(data) if s == 200 else {}
    check("check: params the checks refuse leave that item without a check; the rest of the page stands",
          s == 200 and found[ARNONA]["market"]["params"] is None and found[ARNONA]["market"]["state"] == "none"
          and {PHONE, "electricity", f"fx:{CARD}", "fees", "subscriptions"} <= set(found)
          and (s2, body.get("error")) == (400, "no_check"), (s, s2, body))


def named(desc: str) -> Dict[str, Any]:
    return {"description": desc, "merchant": desc, "merchant_key": ingest.merchant_key(desc)}


def test_fixes() -> None:
    """The review fixes, on Robin's real context with rows added or changed in memory (nothing stored)."""
    ctx = savings.context(ROBIN)
    rows = ctx.book.rows

    def like(desc: str, **changes: Any) -> Dict[str, Any]:
        base = next(r for r in rows if r.get("description") == desc)
        return {**base, "id": uuid.uuid4().hex, **changes}

    def with_rows(*extra: Dict[str, Any], drop: Tuple[str, ...] = ()) -> savings.Context:
        new = sorted([r for r in rows if r["id"] not in drop] + list(extra), key=lambda r: (r["purchase_date"], r["id"]))
        return dataclasses.replace(ctx, book=dataclasses.replace(ctx.book, rows=new))

    # Penalties: cash taken out is never one, the withdrawal fee is.
    atm = like("ריבית פיגורים", **named("משיכת מזומן כספומט עמלת משיכה"), amount=-50_000, flow="cash", eff_flow="cash",
               category="cash", purchase_date="2026-10-02")
    fee = like("ריבית פיגורים", **named("עמלת משיכה כספומט"), amount=-600, purchase_date="2026-10-02")
    pen = savings._penalties(with_rows(atm))
    check("penalties: ₪500 taken out at an ATM is not a penalty (even with 'עמלת משיכה' in its text); the ₪6 fee is",
          not savings._is_penalty(atm) and savings._is_penalty(fee) and pen and pen[0]["at_stake"] == 11_240
          and savings._penalties(with_rows(atm, fee))[0]["at_stake"] == 11_840
          and not savings.in_scope(with_rows(atm), atm, pen[0]["scope"]), pen and pen[0]["at_stake"])

    # Telecom: an offer's price for the user's lines; a bundle has no fair comparison.
    offers = [{**TELECOM[0], "lines": None}, {**TELECOM[1], "lines": 2}, {**TELECOM[2], "lines": 1},
              {**TELECOM[3], "lines": None}]

    def phone_with(answers: Dict[str, Any]) -> Dict[str, Any]:
        def checked(topic: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            return ({"state": "ok", "items": offers, "sources": [SOURCE], "checked_at": "2026-11-18T07:01:00+00:00",
                     "stale": False} if topic == "telecom" else None)
        c = dataclasses.replace(ctx, answers={**ctx.answers, PHONE: answers}, market=checked)
        return next(i for i in savings._bills(c) if i["key"] == PHONE)

    two = phone_with({"service": "mobile", "lines": 2})
    check("telecom, 2 lines: per-line offers × 2, a 2-line offer as it is: best ₪35, 3rd ₪90 → ₪228–₪888 a year",
          two["saving"] == {"low": 22_800, "high": 88_800, "basis": "market"}
          and two["math"][-1] == "Best offer: תקשורת הדרום בסיסי, ₪35.00 a month for 2 lines over 12 months: "
                                 "(₪109.00 − ₪35.00) × 12 = ₪888.00 a year"
          and "for 2 lines" in two["steps"][1]["text"] and "₪35" in two["script"]["text"], (two["saving"], two["math"]))
    bundle = phone_with({"service": "mobile_and_internet", "lines": 2})
    check("telecom: a bundle of mobile and internet keeps the increase as its saving, and says why",
          bundle["saving"] == {"low": 48_000, "high": 48_000, "basis": "increase"}
          and bundle["math"][-1] == "A bundle of mobile and internet: compare the offers with what your bundle includes",
          (bundle["saving"], bundle["math"]))

    # Bill outcome: only the bill's own charges; a big drop is confirmed by a second charge.
    pk = ingest.merchant_key("סלולר הצפון")
    bill = {"merchant_keys": [pk], "amount": 10_900, "cadence": "monthly", "category": "phone_internet"}
    since = D(2026, 9, 30)
    phone_rows = {r["purchase_date"]: r for r in rows if r.get("merchant_key") == pk}
    o = savings._bill_outcome(with_rows(like("סלולר הצפון", amount=-2_000, purchase_date="2026-11-15")), bill, since)
    check("bill outcome: a ₪20 purchase from the same company is not the bill (still ₪109)",
          o == {"state": "no_change", "saved": 0, "saved_yearly": 0, "detail": "Still ₪109.00 (charged 10 Nov 2026)"}, o)
    nov, oct_ = phone_rows["2026-11-10"], phone_rows["2026-10-10"]
    o = savings._bill_outcome(with_rows(dict(nov, amount=-5_000), drop=(nov["id"],)), bill, since)
    check("bill outcome: one charge 54% lower → waiting for the next to confirm (a one-time credit looks the same)",
          o["state"] == "waiting" and o["detail"] == "₪50.00 on 10 Nov 2026: waiting for the next charge to confirm", o)
    o = savings._bill_outcome(with_rows(dict(nov, amount=-5_000), dict(oct_, amount=-5_000), drop=(nov["id"], oct_["id"])),
                              bill, since)
    check("bill outcome: two lower charges → confirmed, ₪59 × 2 saved, ₪708 a year",
          o == {"state": "confirmed", "saved": 11_800, "saved_yearly": 70_800, "detail": "Now ₪50.00 a month, was ₪109.00"}, o)

    # Electricity outcome: a new utility that isn't a supplier is not a switch.
    elec = {"saving_low": 22_943}
    water = like("חברת החשמל לישראל", **named("תאגיד המים הגבעה"), amount=-15_000, purchase_date="2026-11-17")
    supplier = like("חברת החשמל לישראל", **named("סלקום אנרגיה"), amount=-40_000, purchase_date="2026-11-17")
    o1 = savings._electricity_outcome(with_rows(water), elec, since)
    o2 = savings._electricity_outcome(with_rows(supplier), elec, since)
    check("electricity outcome: a new water bill → still waiting; a private supplier's bill after the last IEC one → switched",
          o1["state"] == "waiting" and o2 == {"state": "confirmed", "saved": 63, "saved_yearly": 22_943,
                                              "detail": "Switched to סלקום אנרגיה"}, (o1, o2))

    # Cushion outcome: money set aside counts, a lower balance alone doesn't.
    cushion = {"idle_low": 500_000, "idle_high": 1_000_000, "yield": 3.95}
    o1 = savings._cushion_outcome(ctx, cushion, D(2026, 10, 1))
    deposit = like("משכנתא הצפון", **named("הפקדה לפיקדון"), amount=-600_000, flow="investment", eff_flow="investment",
                   category=None, purchase_date="2026-10-20")
    o2 = savings._cushion_outcome(with_rows(deposit), cushion, D(2026, 10, 1))
    check("cushion outcome: nothing set aside in 48 days → no_change; ₪6,000 to a deposit → confirmed at 3.95%",
          o1["state"] == "no_change" and o2 == {"state": "confirmed", "saved": 3_117, "saved_yearly": 23_700,
                                                "detail": "₪6,000.00 set aside from the account since"}, (o1, o2))

    # Subscriptions: an item drawn from its snapshot shows each line's current choice.
    snap = {"key": "subscriptions", "type": "subscriptions", "group": "save", "questions": [],
            "subitems": [{"key": "x", "choice": "cancel"}, {"key": "y", "choice": "keep"}]}
    paid = savings._payload(ctx, snap, {"status": "open", "answers": json.dumps({"sub:x": {"choice": "keep"}}),
                                        "first_seen_at": STAMP}, False)
    check("subscriptions: an inactive item's lines carry the current choices, not the snapshot's",
          [(x["key"], x["choice"]) for x in paid["subitems"]] == [("x", "keep"), ("y", None)], paid["subitems"])

    # Overlaps: subscriptions and entertainment only, and a cinema is not a streaming service.
    def charge(desc: str, day: str, amount: int, category: str = "subscriptions") -> Dict[str, Any]:
        return like("סטרימינג הצפון", **named(desc), purchase_date=day, amount=-amount, category=category)

    netflix = [charge("NETFLIX.COM", f"2026-{m:02d}-02", 4_990) for m in (9, 10, 11)]
    extra = [charge("YES PLANET ראשון", "2026-11-01", 9_000, "entertainment"), charge("DISNEY STORE", "2026-10-20", 12_000, "shopping")]
    keys = {i["key"] for i in savings._overlaps(with_rows(*netflix, *extra))}
    check("overlap: a cinema (YES PLANET) and a shop purchase are not video services → no video overlap",
          "overlap:video" not in keys and "overlap:ai" in keys, keys)
    disney = [charge("DISNEY PLUS", f"2026-{m:02d}-04", 3_990) for m in (10, 11)]
    ov = next((i for i in savings._overlaps(with_rows(*netflix, *extra, *disney)) if i["key"] == "overlap:video"), None)
    check("overlap: a second streaming subscription → 2 video services, each at its last 12 months' cost",
          ov and ov["title"] == "2 video services at once" and ov["at_stake"] == 22_950
          and ov["summary"].startswith("DISNEY PLUS and NETFLIX.COM: ₪229.50 a year together")
          and ov["evidence"]["lines"] == ["DISNEY PLUS: ₪79.80 in the last 12 months", "NETFLIX.COM: ₪149.70 in the last 12 months"],
          ov)

    # Bank of Israel FX: every supported currency or none, so the fetches say nothing about where the user spent.
    seen: List[str] = []
    real_fx = reference.fx_rates

    def recording(currency: str, start: D, end: D) -> Dict[str, float]:
        seen.append(currency)
        return real_fx(currency, start, end)

    providers.register_rates(dataclasses.replace(IL_RATES, fx_rates=recording))
    try:
        robin_fx = savings.context(ROBIN).fx
        robin_seen, seen[:] = sorted(seen), []
        bob_fx = savings.context(BOB).fx
    finally:
        providers.register_rates(IL_RATES)
    check("fx rates: spent abroad → every supported currency fetched (not only the ones used); never abroad → none",
          robin_seen == sorted(reference.FX_CURRENCIES) and set(robin_fx) == set(reference.FX_CURRENCIES)
          and seen == [] and bob_fx == {}, (robin_seen, seen))


def test_chat() -> None:
    card = context_chats.build_chat_context(ROBIN, "saving", PHONE)
    check("chat: a way to save opens a context chat with its card",
          card["kind"] == "saving" and card["ref_id"] == PHONE and card["emoji"] == "💡"
          and card["title"] == "סלולר הצפון went up ₪40 a month" and card["subtitle"] == "Ways to save · saves ₪480 a year"
          and card["quick_prompts"][0] == "How did you work this out?", card)
    habit = context_chats.build_chat_context(ROBIN, "saving", "creep:eating_out")
    check("chat: a habit's card", habit["emoji"] == "👀" and habit["subtitle"] == "Spending to watch · ₪5,520.00 a year at stake",
          habit)
    errors = []
    for who, exc in ((BOB, LookupError), (CARL, NotConnected)):
        try:
            context_chats.build_chat_context(who, "saving", PHONE)
            errors.append(None)
        except exc:
            errors.append(exc)
    check("chat: Robin's saving for Bob → not found; for Carl (no money) → not connected", errors == [LookupError, NotConnected], errors)
    prompt = context_chats.context_prompt(ROBIN, card) or ""
    fenced = re.search(r"<<external (\w+)>>\n(.*)\n<</external \1>>", prompt, re.S)
    check("chat: the context states the facts and fences the item's words (merchant names written by others)",
          prompt.startswith(f"## Opened from a saving idea\nSaving {PHONE}: group ways to save; type bill; status open; "
                            "saves ₪480 a year (increase); ₪1,308.00 a year at stake.")
          and fenced and "Title: סלולר הצפון went up ₪40 a month" in fenced.group(2) and "update_saving" in prompt, prompt)
    check("chat: no context for another user's key or without money",
          context_chats.context_prompt(BOB, card) is None and context_chats.context_prompt(CARL, card) is None)
    check("chat: a saving's chat counts as untrusted context (merchant names)",
          "saving" in session_runner.UNTRUSTED_CONTEXT_KINDS and "saving" in session_runner.SCREEN_KINDS)


# --- Lifecycle, outcomes, targets ------------------------------------------------------------------------

async def undo_it(client: TestClient, undo_id: str, who=None) -> int:
    s, _ = await call(client, "POST", f"/api/undo/{undo_id}", who or ROBIN)
    return s


async def test_lifecycle(client: TestClient) -> None:
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"status": "doing"})
    r = row(ROBIN, PHONE)
    check("doing: stored with the baseline (the risen price, cadence, merchant), outcome waiting",
          s == 200 and body["item"]["status"] == "doing" and body["undo_id"]
          and json.loads(r["baseline"])["amount"] == 10_900 and body["item"]["outcome"]["state"] == "waiting", body.get("item", {}).get("outcome"))
    check("undo: another user can't use it", await undo_it(client, body["undo_id"], BOB) == 404)
    check("undo: back to open, no baseline", await undo_it(client, body["undo_id"]) == 200
          and row(ROBIN, PHONE)["status"] == "open" and row(ROBIN, PHONE)["baseline"] is None)
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"status": "done"})
    set_row(ROBIN, PHONE, done_at="2026-09-01T00:00:00+00:00")
    s, item = await call(client, "GET", url(PHONE), ROBIN)
    check("outcome: still ₪109 on the charges since → no_change", item["status"] == "done"
          and item["outcome"] == {"state": "no_change", "saved": 0, "saved_yearly": 0,
                                  "detail": "Still ₪109.00 (charged 10 Nov 2026)"}, item["outcome"])
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"status": "doing"})
    check("done → doing keeps the first baseline", s == 200 and body["item"]["status"] == "doing")
    # Bob calls his phone company: the price goes back to ₪50 from October.
    s, body = await call(client, "PATCH", url(BOB_PHONE), BOB, {"status": "done"})
    set_row(BOB, BOB_PHONE, done_at="2026-10-01T00:00:00+00:00")
    with store.db() as conn:
        conn.execute("UPDATE money_transactions SET amount = -5000, orig_amount = -5000 WHERE user_id = ? "
                     "AND purchase_date IN ('2026-10-10', '2026-11-10')", (BOB.id,))
    s, bob = await call(client, "GET", "/api/money/savings", BOB)
    b = items_of(bob).get(BOB_PHONE)
    check("kept: the detector stopped firing (no price rise now) but the done item stays, from its snapshot",
          b and b["active"] is False and b["status"] == "done" and b["title"] == "סלולר הדרום went up ₪30 a month", b)
    check("outcome: ₪50 again → confirmed, ₪60 saved so far, ₪360 a year; in the totals",
          b and b["outcome"] == {"state": "confirmed", "saved": 6_000, "saved_yearly": 36_000,
                                 "detail": "Now ₪50.00 a month, was ₪80.00"}
          and bob["totals"]["saved"] == 6_000 and bob["totals"]["saved_yearly"] == 36_000, b and b["outcome"])
    s, body = await call(client, "PATCH", url("electricity"), ROBIN, {"status": "done"})
    check("outcome: electricity done today → waiting for the next bill",
          body["item"]["outcome"]["state"] == "waiting", body["item"]["outcome"])
    # Statuses, validation.
    bad = [(await call(client, "PATCH", url(k), ROBIN, b))[0] for k, b in (
        ("fees", {"status": "watching"}), ("fees", {"status": "maybe"}), ("fees", {"target": 5_000}),
        ("fees", {"status": "snoozed", "snooze_days": 0}), ("fees", {"subitem": {"key": "x", "choice": "cancel"}}),
        ("subscriptions", {"subitem": {"key": "nope", "choice": "cancel"}}), ("fees", {}),
        ("creep:eating_out", {"status": "done"}), ("creep:eating_out", {"target": -1}),
        ("creep:eating_out", {"status": "accepted", "target": 5_000}))]
    check("validation: a status of the other group, an unknown status, a target on a way to save, bad snooze days, "
          "a subitem elsewhere or unknown, nothing to change, a negative target → 400", bad == [400] * 10, bad)
    s, _ = await call(client, "PATCH", url("bill:nothing"), ROBIN, {"status": "done"})
    check("validation: an unknown key → 404", s == 404)
    s, body = await call(client, "PATCH", url("mortgage"), ROBIN, {"status": "dismissed"})
    check("dismissed: kept and listed", s == 200 and body["item"]["status"] == "dismissed" and body["item"]["active"])
    s, body = await call(client, "PATCH", url("mortgage"), ROBIN, {"status": "open"})
    check("open: Dana decides again (still listed: its detector fires)", s == 200 and body["item"]["status"] == "open")
    s, body = await call(client, "PATCH", url(RENEW_HOME), ROBIN, {"status": "snoozed", "snooze_days": 10})
    check("snoozed: until a date", s == 200 and body["item"]["snooze_until"] == "2026-11-28", body["item"]["snooze_until"])
    s, body = await call(client, "PATCH", url("mortgage"), ROBIN, {"status": "snoozed"})
    check("snoozed: 30 days by default", body["item"]["snooze_until"] == "2026-12-18", body["item"]["snooze_until"])
    set_row(ROBIN, "mortgage", snooze_until="2026-11-17")
    s, item = await call(client, "GET", url("mortgage"), ROBIN)
    check("snoozed: back to open once the date passed", item["status"] == "open" and item["snooze_until"] is None, item["status"])
    # Fees: dismissed, then the detector stops firing → kept; "open" then lets it go.
    s, body = await call(client, "PATCH", url("fees"), ROBIN, {"status": "dismissed"})
    delete_rows(ROBIN, "עמלת ניהול חשבון")
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    fe = items_of(data).get("fees")
    check("kept: a dismissed item whose detector stopped firing is still returned (inactive, from its snapshot)",
          fe and fe["active"] is False and fe["status"] == "dismissed"
          and fe["saving"] == {"low": 13_080, "high": 13_080, "basis": "measured"}, fe)
    s, body = await call(client, "PATCH", url("fees"), ROBIN, {"status": "open"})
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    check("open: an inactive item given back to Dana is no longer listed", "fees" not in items_of(data))
    s, _ = await call(client, "PATCH", url("fees"), ROBIN, {"status": "dismissed"})
    check("open: and can't be changed until it fires again", s == 404)
    add_fees()
    s, body = await call(client, "PATCH", url("fees"), ROBIN, {"status": "dismissed"})
    check("fires again: Dana's call, then dismissed again", s == 200 and body["item"]["status"] == "dismissed")
    delete_rows(ROBIN, "עמלת ניהול חשבון")
    # Watch: accepted, inactive.
    s, body = await call(client, "PATCH", url("late_night"), ROBIN, {"status": "accepted"})
    with store.db() as conn:
        conn.execute("UPDATE money_transactions SET occurred_at = REPLACE(occurred_at, 'T23:30', 'T12:30') WHERE user_id = ?",
                     (ROBIN.id,))
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    ln = items_of(data).get("late_night")
    check("accepted: 'worth it' stays listed after the habit stops", ln and ln["status"] == "accepted" and not ln["active"], ln)
    s, body = await call(client, "PATCH", url(SPREE_KEY), ROBIN, {"status": "accepted"})
    check("accepted: the spree", s == 200 and body["item"]["status"] == "accepted")
    # Cash: snoozed, then the cash is invested → inactive snoozed.
    s, body = await call(client, "PATCH", url(f"cash:{INV}"), ROBIN, {"status": "snoozed"})
    with store.db() as conn:
        conn.execute("UPDATE money_accounts SET details = ? WHERE id = ?", (json.dumps({"value": 3_500_000, "cash": 0}), INV))
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    ca = items_of(data).get(f"cash:{INV}")
    check("snoozed: kept while snoozed after its detector stops", ca and ca["status"] == "snoozed" and not ca["active"]
          and ca["snooze_until"] == "2026-12-18", ca)


async def test_subscriptions(client: TestClient) -> None:
    key = "סטרימינג הצפון"
    s, body = await call(client, "PATCH", url("subscriptions"), ROBIN, {"subitem": {"key": key, "choice": "cancel"}})
    line = next(x for x in body["item"]["subitems"] if x["key"] == key)
    check("subscriptions: cancel → the line's choice, a measured saving of its year (₪358.80)",
          s == 200 and line["choice"] == "cancel" and body["item"]["saving"] == {"low": 35_880, "high": 35_880, "basis": "measured"}
          and body["item"]["outcome"]["state"] == "waiting", body["item"]["saving"])
    s2, keep = await call(client, "PATCH", url("subscriptions"), ROBIN, {"subitem": {"key": key, "choice": "keep"}})
    check("subscriptions: Undo of keep restores cancel", s2 == 200 and await undo_it(client, keep["undo_id"]) == 200
          and next(x for x in items_of(savings.overview(ROBIN))["subscriptions"]["subitems"] if x["key"] == key)["choice"] == "cancel")
    # The cancel was made in mid-September; the October and November charges never came.
    r = row(ROBIN, "subscriptions")
    answers = json.loads(r["answers"])
    answers[f"sub:{key}"].update(at="2026-09-15T08:00:00+00:00", next_date="2026-10-07")
    set_row(ROBIN, "subscriptions", answers=json.dumps(answers, ensure_ascii=False))
    delete_rows(ROBIN, key, after="2026-09-15")
    item = items_of(savings.overview(ROBIN))["subscriptions"]
    line = next(x for x in item["subitems"] if x["key"] == key)
    check("subscriptions: two expected charges didn't come → confirmed, ₪59.80 saved, ₪358.80 a year",
          item["outcome"] == {"state": "confirmed", "saved": 5_980, "saved_yearly": 35_880,
                              "detail": "No charge since you cancelled: סטרימינג הצפון"}, item["outcome"])
    check("subscriptions: the cancelled line stays listed (inactive) with its choice; no longer in the saving",
          line["active"] is False and line["choice"] == "cancel" and item["saving"] is None and item["at_stake"] == 286_080, line)


async def test_targets(client: TestClient) -> None:
    key = "creep:eating_out"
    s, body = await call(client, "PATCH", url(key), ROBIN, {"target": 70_000})
    t = body["item"]["target"]
    check("target: set → watching; ₪300 of ₪700 on day 18 of 30 is on track, 12 days left",
          s == 200 and body["item"]["status"] == "watching"
          and t == {"monthly": 70_000, "this_month": 30_000, "on_track": True, "days_left": 12, "months": [], "saved": 0}, t)
    check("target: the baseline keeps the pace (₪860 a month)", json.loads(row(ROBIN, key)["baseline"])["pace"] == 86_000)
    check("target: Undo removes it", await undo_it(client, body["undo_id"]) == 200 and row(ROBIN, key)["target"] is None
          and row(ROBIN, key)["status"] == "open")
    s, body = await call(client, "PATCH", url(key), ROBIN, {"status": "watching"})
    check("target: watching without a number takes the suggested ₪650", body["item"]["target"]["monthly"] == 65_000)
    s, body = await call(client, "PATCH", url(key), ROBIN, {"target": 70_000})
    check("target: each change is kept with its month", json.loads(row(ROBIN, key)["baseline"])["targets"]
          == [{"month": "2026-11", "monthly": 70_000}], row(ROBIN, key)["baseline"])
    # As if it was set on 10 September.
    baseline = json.loads(row(ROBIN, key)["baseline"])
    baseline["targets"] = [{"month": "2026-09", "monthly": 70_000}]
    set_row(ROBIN, key, status_at="2026-09-10T08:00:00+00:00", baseline=json.dumps(baseline))
    item = items_of(savings.overview(ROBIN))[key]
    check("target: the months since it was set; ₪660 in October under ₪700 saves ₪860 − ₪660 = ₪200",
          item["target"]["months"] == [{"month": "2026-09", "spent": 90_000, "target": 70_000, "under": False},
                                       {"month": "2026-10", "spent": 66_000, "target": 70_000, "under": True}]
          and item["target"]["saved"] == 20_000, item["target"])
    s, body = await call(client, "PATCH", url(key), ROBIN, {"target": 35_000})
    t = body["item"]["target"]
    check("target: edited to ₪350 in November → over the pace this month; September and October keep the ₪700 "
          "they were judged by (₪200 saved stays); the pace stays",
          t["monthly"] == 35_000 and t["on_track"] is False and [m["target"] for m in t["months"]] == [70_000, 70_000]
          and t["saved"] == 20_000 and json.loads(row(ROBIN, key)["baseline"])["pace"] == 86_000
          and json.loads(row(ROBIN, key)["baseline"])["targets"] == [{"month": "2026-09", "monthly": 70_000},
                                                                   {"month": "2026-11", "monthly": 35_000}], t)
    from addons.money import hooks as money_hooks
    cards = [c.to_json() for c in money_hooks.savings_cards(ROBIN, NOW, {})]
    check("now: a target at ≥ 80% of this month's", f"money_target:{key}:2026-11" in [c["id"] for c in cards],
          [c["id"] for c in cards])
    s, body = await call(client, "PATCH", url(key), ROBIN, {"target": None})
    check("target: removed → open", body["item"]["status"] == "open" and body["item"]["target"] is None)
    s, body = await call(client, "PATCH", url(key), ROBIN, {"target": 70_000})
    set_row(ROBIN, key, status_at="2026-09-10T08:00:00+00:00")
    later = datetime.datetime(2026, 12, 2, 9, 0, tzinfo=TZ)
    cards = {c.id: c.to_json() for c in money_hooks.savings_cards(ROBIN, later, {})}
    nov = cards.get(f"money_target:{key}:2026-11")
    check("now: November's result in the first days of December (₪300 of ₪700: under by ₪400)",
          nov and nov["title"] == "Eating out in November: ₪300.00 of ₪700.00"
          and nov["why"] == "Under your target by ₪400.00", nov)


async def test_now(client: TestClient) -> None:
    from addons.money import hooks as money_hooks
    # Looking at 2 December (test_targets) ended the renewal's snooze: snooze it again.
    s, _ = await call(client, "PATCH", url(RENEW_HOME), ROBIN, {"status": "snoozed", "snooze_days": 10})
    cards = {c.id for c in money_hooks.savings_cards(ROBIN, NOW, {})}
    check("now: no card for a snoozed renewal or a bill already being handled (doing)",
          s == 200 and f"money_save:{RENEW_HOME}" not in cards and f"money_save:{PHONE}" not in cards, sorted(cards))
    s, body = await call(client, "PATCH", url(RENEW_HOME), ROBIN, {"status": "open"})
    s, body = await call(client, "PATCH", url(PHONE), ROBIN, {"status": "open"})
    cards = {c.id: c.to_json() for c in money_hooks.savings_cards(ROBIN, NOW, {})}
    renew, bill = cards.get(f"money_save:{RENEW_HOME}"), cards.get(f"money_save:{PHONE}")
    check("now: the renewal in 10 days and the new bill increase, as money cards",
          renew and renew["why"] == "Renews around 28 Nov 2026 · in 10 days" and renew["kind"] == "money"
          and renew["ref"] == {"type": "saving", "id": RENEW_HOME} and bill and bill["title"].startswith("סלולר הצפון")
          and bill["blocks"][0]["items"][0] == "Saves ₪768.00–₪888.00 a year", sorted(cards))
    res = await now_screen.act(ROBIN, f"money_save:{RENEW_HOME}", "dismiss")
    cards = {c.id for c in money_hooks.savings_cards(ROBIN, NOW, now_screen.now_hidden.active(ROBIN))}
    check("now: dismissing the card hides it (the saving's own status stays open)",
          f"money_save:{RENEW_HOME}" not in cards and row(ROBIN, RENEW_HOME)["status"] == "open" and res.get("undo_id"))
    with users.acting_as(ROBIN):
        await undo.run(res["undo_id"])
    cards = {c.id for c in money_hooks.savings_cards(ROBIN, NOW, now_screen.now_hidden.active(ROBIN))}
    check("now: Undo shows it again", f"money_save:{RENEW_HOME}" in cards)
    try:
        await now_screen.act(ROBIN, f"money_save:{BOB_PHONE}", "dismiss")
        ok = False
    except ActError as e:
        ok = e.status == 404
    check("now: another user's saving → 404", ok)
    try:
        await now_screen.act(CARL, f"money_save:{PHONE}", "dismiss")
        ok = False
    except NotConnected:
        ok = True
    check("now: without money → not connected", ok)
    res = await now_screen.act(ROBIN, "money_target:creep:eating_out:2026-11", "dismiss")
    check("now: a target card is dismissed by month", res["ok"]
          and "money_target:creep:eating_out:2026-11" in now_screen.now_hidden.active(ROBIN))
    check("now: nothing for Carl", money_hooks.savings_cards(CARL, NOW, {}) == [])


# --- API, isolation --------------------------------------------------------------------------------------

async def test_api(client: TestClient) -> None:
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    check("api: GET answers Robin", s == 200 and data["items"])
    test_detectors(data)
    s, item = await call(client, "GET", url(PHONE), ROBIN)
    check("api: one item by its URL-encoded key (':' and Hebrew)", s == 200 and item["key"] == PHONE)
    s, bob = await call(client, "GET", "/api/money/savings", BOB)
    check("isolation: Bob sees only his own phone bill", s == 200 and set(items_of(bob)) == {BOB_PHONE}, sorted(items_of(bob)))
    st = [(await call(client, m, url(PHONE, sfx), BOB, b))[0] for m, sfx, b in (
        ("GET", "", None), ("PATCH", "", {"status": "done"}), ("POST", "/check", None))]
    check("isolation: Robin's key answers 404 for Bob (GET, PATCH, check)", st == [404, 404, 404], st)
    s, _ = await call(client, "GET", url(BOB_PHONE), ROBIN)
    check("isolation: and Bob's for Robin", s == 404)
    st = [(await call(client, m, p, CARL, b))[0] for m, p, b in (
        ("GET", "/api/money/savings", None), ("GET", url(PHONE), None), ("PATCH", url(PHONE), {"status": "done"}),
        ("POST", url(PHONE, "/check"), None))]
    check("api: no money → 403 not_connected on every savings route", st == [403] * 4, st)


async def test_tools_and_cards() -> None:
    names = ["saving_opportunities", "update_saving"]
    check("tools: update_saving writes at once (no approval), both results untrusted; Robin and Bob have them, Carl not",
          [registry.is_write_tool(n) for n in names] == [False, True]
          and not any(registry.needs_confirmation(n, {"key": "fees", "status": "done"}) for n in names)
          and all(registry.returns_untrusted(n) for n in names)
          and all(registry.has_tool(u, n) for u in (ROBIN, BOB) for n in names)
          and not any(registry.has_tool(CARL, n) for n in names))
    conn = connections.require(ROBIN, "money")
    text = await tools.saving_opportunities(conn)
    ways = text.split("Ways to save")[1].split("Spending to watch")[0] if "Ways to save" in text else ""
    check("tool: saving_opportunities lists the open ways to save with their key, saving, effort and first step",
          "- [bill:סלולר הצפון] סלולר הצפון went up ₪40 a month · saves ₪768.00–₪888.00 a year (from a price check) · "
          "about 20 min, a call · first step: Call סלולר הצפון" in ways and "[fees]" not in ways and f"[cash:{INV}]" not in ways, ways)
    check("tool: the habits, this month's targets, what was acted on, and the user's choices",
          "- [overlap:ai] 2 AI assistants at once" in text
          and "- [creep:eating_out] Eating out: ₪300.00 of ₪700.00 (on track, 12 days left); saved ₪200.00 so far" in text
          and "- [electricity] Switch electricity supplier (done): waiting to see" in text
          and "— not for them" in text.split("The user's choices:")[-1] and "snoozed until 18 Dec" in text
          and "— worth it" in text, text)
    with undo.collect() as ids, users.acting_as(ROBIN):
        out = await tools.update_saving(conn, "mortgage", status="dismissed")
    check("tool: update_saving records a choice with Undo", "not for them" in out and ids and row(ROBIN, "mortgage")["status"] == "dismissed", out)
    out = await tools.update_saving(conn, "fees", monthly_target=500)
    check("tool: a target on a way to save is refused in words", out.startswith("Error:"), out)
    out = await tools.update_saving(conn, "nothing:here", status="done")
    check("tool: an unknown key", out.startswith("Error: no saving item"), out)
    out = await tools.update_saving(conn, SPREE_KEY, monthly_target=300)
    check("tool: a monthly target in ₪ on a habit", "target ₪300.00 a month" in out and row(ROBIN, SPREE_KEY)["target"] == 30_000, out)
    await tools.update_saving(conn, SPREE_KEY, status="accepted")
    await tools.update_saving(conn, "mortgage", status="open")
    week = weekly_review.money_week(ROBIN, NOW)
    check("weekly review: one savings line (ideas open, targets) in the Money section",
          week and week["savings"].startswith("Savings: ") and "ideas open worth" in week["savings"]
          and "Eating out ₪300 of ₪700 this month" in week["savings"] and week["savings"] in queries.week_lines(week), week and week["savings"])
    found = dashboard.month_insights(ROBIN)
    card = next((i for i in found["items"] if i["kind"] == "savings"), None)
    totals = savings.overview(ROBIN)["totals"]
    check("insights: the month in progress has 'N ways to save ₪a–₪b a year' opening the Save view",
          card and card["link"] == {"view": "save"} and card["title"].startswith(f"{totals['count']} ways to save ")
          and card["amount"] == totals["high"], card)
    check("insights: not on a past month", not any(i["kind"] == "savings" for i in dashboard.month_insights(ROBIN, "2026-10")["items"]))


def test_delete_all() -> None:
    store.delete_all(BOB)
    with store.db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM money_savings WHERE user_id = ?", (BOB.id,)).fetchone()[0]
        robin = conn.execute("SELECT COUNT(*) FROM money_savings WHERE user_id = ?", (ROBIN.id,)).fetchone()[0]
    check("delete_all: Bob's saving rows go, Robin's stay", n == 0 and robin > 0, (n, robin))


# --- The web fixture -------------------------------------------------------------------------------------

CAPTURED: Dict[str, Any] = {}


def shape(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return type(v).__name__


async def fixture(client: TestClient) -> None:
    s, patched = await call(client, "PATCH", url(PHONE), ROBIN, {"status": "doing"})
    CAPTURED["patch"] = patched
    s, item = await call(client, "GET", url(PHONE), ROBIN)
    CAPTURED["item_check"] = item
    s, data = await call(client, "GET", "/api/money/savings", ROBIN)
    CAPTURED["savings"] = data
    types = {i["type"] for i in data["items"]}
    statuses = {i["status"] for i in data["items"]}
    inactive = {i["status"] for i in data["items"] if not i["active"]}
    check("fixture: every type, every status, inactive dismissed / accepted / snoozed ones, a watched target, "
          "a market check that is ok",
          types == {"bill", "electricity", "cash", "cushion", "fx", "fees", "arnona", "renewal", "insurance_review",
                    "subscriptions", "mortgage", "creep", "spree", "leak", "overlap", "penalties", "late_night"}
          and statuses == set(savings.STATUSES) and {"dismissed", "accepted", "snoozed"} <= inactive
          and any(i["target"] for i in data["items"]) and item["market"]["state"] == "ok", (types, statuses, inactive))
    text = json.dumps(CAPTURED, indent=1, ensure_ascii=False, sort_keys=True)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
    else:
        with open(WEB_FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        drift = [k for k in CAPTURED if shape(CAPTURED[k]) != shape(stored.get(k))]
        check("fixture: tests/fixtures/money_savings_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_money_savings)", not drift, drift)


async def main() -> None:
    test_reference()
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    actions_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_api(client)
        test_fixes()
        test_chat()
        await test_check_route(client)
        await test_checks(client)
        await test_lifecycle(client)
        await test_subscriptions(client)
        await test_targets(client)
        await test_now(client)
        await test_tools_and_cards()
        test_delete_all()
        await fixture(client)


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
