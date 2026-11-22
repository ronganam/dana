"""Money page: periods, the query engine, recurring / unusual charges and the page API with its web fixture (docs/money.md, "Page API and insights").
Run: bin/test test_money_page

A synthetic dataset, every value made up, with today fixed at Wed 18 Nov
2026 09:30 (Asia/Jerusalem): Robin (the first admin) has a Hapoalim account
and two Cal cards with rows from Dec 2024 to today; Bob has one card; Carl
has no ``money``. Covers: every period name, explicit dates and the
comparisons ("at this point last month", the whole month before, month
lengths); spending totals asserted exactly (card bills, own transfers and
unmatched rows never count, refunds reduce, NULL-flow rules for card and
bank rows, installments by purchase date, provisional and pending rows
count); income and single flows; category / merchant / account filters and
every group_by; search (full text, an amount alone, ranges, cursor pages
newest first, unmatched rows listed with badges); the transaction detail
(never the stored raw JSON); the overview (bills, balances, the month-end
forecast, attention); pace; months; recurring detection (monthly price
change, weekly, yearly, installments and irregular / stopped series left
out) and unusual charges (large, first time, a duplicate within 10 minutes);
per user both ways (another user's account / transaction → not found / 404,
no ``money`` → 403 ``not_connected``); PATCH + one Undo; ``/changes``; and
the payload fixture tests/fixtures/money_web.json (written with
``WRITE_WEB_FIXTURE=1``, else checked for shape drift).
"""

import asyncio
import datetime
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_page_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-page-test-env-token"  # a test value: the first admin's token (a headless install)
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

from core import actions_api, clock  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import api as money_api, dashboard, ingest, insights, periods, queries, store  # noqa: E402

WEB_FIXTURE = os.path.join(testenv.FIXTURES, "money_web.json")
results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


TZ = clock.get_timezone()
NOW = datetime.datetime(2026, 11, 18, 9, 30, tzinfo=TZ)
TODAY = NOW.date()
_clock = patch.object(clock, "now", lambda: NOW)
_clock.start()

ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
STAMP = "2026-11-01T00:00:00+00:00"  # older than any real "now": a change moves the newest updated_at

# --- The dataset --------------------------------------------------------------------------------


_ADDED: List[str] = []


def source(user, company: str, state: str = "ok", reason: Optional[str] = None,
           last_ok: Optional[str] = "2026-11-18T05:01:00+00:00") -> str:
    sid = uuid.uuid4().hex
    _ADDED.append(sid)  # added a minute apart, in this order
    with store.db() as conn:
        conn.execute("INSERT INTO money_sources (id, user_id, company, state, state_reason, last_sync_at, last_ok_at, "
                     "next_sync_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, user.id, company, state, reason, last_ok, last_ok, "2026-11-18T17:00:00+00:00",
                      f"2026-08-01T00:{len(_ADDED):02d}:00+00:00", STAMP))
    return sid


def account(user, sid: Optional[str], company: str, kind: str, number: str, **kw: Any) -> str:
    aid = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, label, balance, "
                     "balance_at, next_charge, next_charge_date, billed_from_account_id, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (aid, user.id, sid, company, kind, number, kw.get("label"), kw.get("balance"),
                      kw.get("balance_at"), kw.get("next_charge"), kw.get("next_charge_date"), kw.get("billed_from"),
                      STAMP, STAMP))
    return aid


def txn(user, aid: Optional[str], date: str, amount: int, desc: str, *, category: Optional[str] = None,
        flow: Optional[str] = None, status: str = "completed", occurred: Optional[str] = None,
        charge: Optional[str] = None, orig: Optional[int] = None, currency: Optional[str] = None,
        inst: Optional[Tuple[int, int]] = None, memo: Optional[str] = None, note: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None) -> str:
    tid = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute(
            "INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, occurred_at, "
            "purchase_date, charge_date, amount, orig_amount, orig_currency, description, merchant, merchant_key, memo, "
            "category, category_source, flow, installment_number, installment_total, note, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, user.id, aid, f"x:{tid}", "notification" if status in ("provisional", "unmatched") else "scrape",
             status, occurred, date, charge, amount, orig, currency, desc, desc, ingest.merchant_key(desc), memo,
             category, "bank" if category else None, flow, inst[0] if inst else None, inst[1] if inst else None, note,
             json.dumps(raw or {"description": desc}, ensure_ascii=False), STAMP, STAMP))
    return tid


S_BANK = source(ROBIN, "hapoalim")
S_CAL = source(ROBIN, "visaCal", "failing", "bank worker not reachable", last_ok="2026-11-17T17:00:00+00:00")
BANK = account(ROBIN, S_BANK, "hapoalim", "bank", "12-345-678901", balance=1_234_567, balance_at="2026-11-18T05:01:00+00:00")
C1 = account(ROBIN, S_CAL, "visaCal", "card", "8763", next_charge=345_081, next_charge_date="2026-12-10")
C2 = account(ROBIN, S_CAL, "visaCal", "card", "4580", label="Shared card", next_charge=120_000,
             next_charge_date="2026-11-25", billed_from=BANK)

T: Dict[str, str] = {}
# Monthly on the bank: salary, a benefit, the mortgage; the card bills.
for m in ("2026-08", "2026-09", "2026-10", "2026-11"):
    T[f"salary_{m}"] = txn(ROBIN, BANK, f"{m}-10", 1_500_000, "משכורת נוגה תעשיות", category="salary", flow="income")
for m in ("2026-08", "2026-09", "2026-10"):
    txn(ROBIN, BANK, f"{m}-20", 17_300, "קצבת ילדים", category="benefits", flow="income")
    txn(ROBIN, BANK, f"{m}-20", -450_000, "משכנתא לאומי", category="housing", flow="loan")
    txn(ROBIN, C1, f"{m}-03", -3_990, "NETFLIX.COM", category="subscriptions", flow="purchase")
txn(ROBIN, BANK, "2026-10-02", -300_000, "כאל", flow="card_bill")
# Yearly.
txn(ROBIN, C1, "2024-12-01", -6_000, "NAMECHEAP", category="subscriptions", flow="purchase")
txn(ROBIN, C1, "2025-12-01", -6_000, "NAMECHEAP", category="subscriptions", flow="purchase")
# September and October on the cards.
txn(ROBIN, C1, "2026-09-05", -9_000, "סופר פארם", category="health", flow="purchase")
txn(ROBIN, C1, "2026-09-20", -11_000, "סופר פארם", category="health", flow="purchase")
txn(ROBIN, C1, "2026-10-04", -10_000, "סופר פארם", category="health", flow="purchase")
txn(ROBIN, C1, "2026-10-22", -12_000, "סופר פארם", category="health", flow="purchase")
txn(ROBIN, C1, "2026-09-08", -55_000, "שופרסל דיל", category="groceries", flow="purchase")
txn(ROBIN, C1, "2026-10-06", -60_000, "שופרסל דיל", category="groceries", flow="purchase")
txn(ROBIN, C1, "2026-10-15", -45_000, "רמי לוי - יבנה", category="groceries", flow="purchase")
txn(ROBIN, C1, "2026-10-12", -30_000, "מסעדת הדג", category="restaurants", flow="purchase")
txn(ROBIN, C1, "2026-10-25", -20_000, "מסעדת הדג", category="restaurants", flow="purchase")
# A laptop in 3 payments bought on 15 Sep: every payment counts on the purchase date.
T["laptop1"] = txn(ROBIN, C2, "2026-09-15", -40_000, "KSP מחשבים", category="electronics", flow="purchase",
                   charge="2026-10-10", orig=-120_000, currency="ILS", inst=(1, 3))
txn(ROBIN, C2, "2026-09-15", -40_000, "KSP מחשבים", category="electronics", flow="purchase", charge="2026-11-10",
    orig=-120_000, currency="ILS", inst=(2, 3))
# November so far.
T["netflix_nov"] = txn(ROBIN, C1, "2026-11-03", -4_990, "NETFLIX.COM", category="subscriptions", flow="purchase",
                       occurred="2026-11-03T10:00:00+02:00")
T["rami"] = txn(ROBIN, C1, "2026-11-05", -32_000, "רמי לוי - יבנה", category="groceries")  # flow NULL → purchase
T["shufersal"] = txn(ROBIN, C1, "2026-11-10", -50_000, "שופרסל דיל", category="groceries", flow="purchase",
                     occurred="2026-11-10T10:00:00+00:00")  # after the salary row of the same day: a fixed order
T["fish"] = txn(ROBIN, C1, "2026-11-12", -82_000, "מסעדת הדג", category="restaurants", flow="purchase")
T["refund"] = txn(ROBIN, C1, "2026-11-14", 5_000, "שופרסל דיל", category="groceries")  # flow NULL, money in → refund
T["course"] = txn(ROBIN, C1, "2026-11-07", -1_594, "ONLINE COURSE CO", category="education", flow="purchase",
                  orig=-500, currency="USD", occurred="2026-11-07T17:07:18+02:00",
                  raw={"description": "ONLINE COURSE CO", "category": "חינוך", "secret_raw_marker": "raw-only"})
T["insurance"] = txn(ROBIN, C1, "2026-11-16", -70_900, "ביטוח רכב ישיר", category="insurance", flow="purchase",
                     charge="2026-12-10", orig=-283_600, currency="ILS", inst=(1, 4),
                     occurred="2026-11-16T15:00:00+02:00")
T["pharm"] = txn(ROBIN, C1, "2026-11-17", -150_000, "סופר פארם", category="health", flow="purchase",
                 occurred="2026-11-17T18:00:00+02:00")
T["coffee"] = txn(ROBIN, C1, "2026-11-18", -2_500, "קפה קפה", status="provisional",
                  occurred="2026-11-18T08:10:00+02:00")
T["wolt_unmatched"] = txn(ROBIN, C1, "2026-11-15", -9_900, "WOLT", category="delivery", status="unmatched")
T["wolt"] = txn(ROBIN, C1, "2026-11-17", -4_500, "WOLT", category="delivery", flow="purchase", status="pending")
T["fuel1"] = txn(ROBIN, C1, "2026-11-17", -20_000, "פז דלק", category="fuel", flow="purchase",
                 occurred="2026-11-17T12:00:00+02:00")
T["fuel2"] = txn(ROBIN, C1, "2026-11-17", -20_000, "פז דלק", category="fuel", flow="purchase",
                 occurred="2026-11-17T12:04:00+02:00")
T["cafe"] = txn(ROBIN, C2, "2026-11-08", -12_000, "קפה לנדוור", category="restaurants", flow="purchase")
T["kolbo"] = txn(ROBIN, C2, "2026-11-09", -6_000, "חנות כלבו")  # no category, no flow
T["bill"] = txn(ROBIN, BANK, "2026-11-02", -345_000, "כאל", flow="card_bill")
T["own"] = txn(ROBIN, BANK, "2026-11-04", -100_000, "העברה לחיסכון", flow="own_transfer")
T["dani"] = txn(ROBIN, BANK, "2026-11-06", -25_000, "העברה לדני כהן", category="transfers", flow="transfer_out",
                memo="עבור כרטיסים להופעה")
txn(ROBIN, BANK, "2026-11-01", -1_200, "עמלת פעולה", category="fees", flow="fee")
T["cash"] = txn(ROBIN, BANK, "2026-11-11", -30_000, "משיכת מזומן")  # bank row, no flow: not spending

S_BOB = source(BOB, "visaCal")
BOB_CARD = account(BOB, S_BOB, "visaCal", "card", "1111", next_charge=10_000, next_charge_date="2026-12-02")
T["bob_rami"] = txn(BOB, BOB_CARD, "2026-11-05", -10_000, "רמי לוי - יבנה", category="groceries", flow="purchase")

# November 1–18 spending (agorot): every counted row of Robin's.
NOV_SPENT = (4_990 + 32_000 + 50_000 + 82_000 - 5_000 + 1_594 + 70_900 + 150_000 + 2_500 + 4_500 + 20_000 + 20_000
             + 12_000 + 6_000 + 25_000 + 1_200)
OCT_SPENT = 3_990 + 10_000 + 12_000 + 60_000 + 45_000 + 30_000 + 20_000 + 450_000
OCT_1_18 = 3_990 + 10_000 + 60_000 + 45_000 + 30_000
SEP_SPENT = 3_990 + 9_000 + 11_000 + 55_000 + 80_000 + 450_000
AUG_SPENT = 3_990 + 450_000


def P(name: Optional[str] = None, **kw: Any) -> periods.Period:
    return periods.resolve(name, now=TODAY, **kw)


# --- Periods ------------------------------------------------------------------------------------

def test_periods() -> None:
    def span(p: periods.Period) -> Tuple[str, str]:
        return p.start.isoformat(), p.end.isoformat()

    m = P("this_month")
    check("periods: this_month is 1–18 Nov, in progress, compared with 1–18 Oct and all of October",
          span(m) == ("2026-11-01", "2026-11-18") and m.in_progress
          and [(c.label, *span(c)) for c in m.compare_to] == [("at this point last month", "2026-10-01", "2026-10-18"),
                                                              ("all of last month", "2026-10-01", "2026-10-31")])
    check("periods: today / yesterday", span(P("today")) == ("2026-11-18", "2026-11-18")
          and span(P("today").compare_to[0]) == ("2026-11-17", "2026-11-17")
          and span(P("yesterday")) == ("2026-11-17", "2026-11-17") and not P("yesterday").in_progress)
    w = P("this_week")
    check("periods: this_week starts on Sunday 15 Nov; compared with Sun–Wed of the week before",
          span(w) == ("2026-11-15", "2026-11-18") and span(w.compare_to[0]) == ("2026-11-08", "2026-11-11")
          and w.compare_to[0].label == "at this point last week")
    check("periods: last_week is Sun 8 – Sat 14 Nov, compared with the week before",
          span(P("last_week")) == ("2026-11-08", "2026-11-14")
          and span(P("last_week").compare_to[0]) == ("2026-11-01", "2026-11-07"))
    lm = P("last_month")
    check("periods: last_month is whole October, compared with whole September",
          span(lm) == ("2026-10-01", "2026-10-31") and not lm.in_progress
          and [(c.label, *span(c)) for c in lm.compare_to] == [("September 2026", "2026-09-01", "2026-09-30")])
    check("periods: last_30_days and the 30 before", span(P("last_30_days")) == ("2026-10-20", "2026-11-18")
          and span(P("last_30_days").compare_to[0]) == ("2026-09-20", "2026-10-19"))
    y = P("this_year")
    check("periods: this_year compared with 1 Jan – 18 Nov 2025 and all of 2025",
          span(y) == ("2026-01-01", "2026-11-18")
          and [span(c) for c in y.compare_to] == [("2025-01-01", "2025-11-18"), ("2025-01-01", "2025-12-31")])
    check("periods: last_year, YYYY-MM (past and current), YYYY",
          span(P("last_year")) == ("2025-01-01", "2025-12-31") and span(P("2026-10")) == span(lm)
          and P("2026-10").label == "October 2026" and span(P("2026-11")) == span(m) and P("2026-11").in_progress
          and span(P("2025")) == ("2025-01-01", "2025-12-31"))
    c = P(start="2026-11-01", end="2026-11-15")
    check("periods: explicit dates (end inclusive) compared with the same length before",
          span(c) == ("2026-11-01", "2026-11-15") and c.label == "1–15 Nov 2026"
          and span(c.compare_to[0]) == ("2026-10-17", "2026-10-31"))
    check("periods: start alone runs to today; spaces and dashes in names work",
          span(P(start="2026-11-10")) == ("2026-11-10", "2026-11-18") and span(P("This Month")) == span(m)
          and span(P("last-week")) == span(P("last_week")))
    march = periods.resolve("this_month", now=datetime.date(2026, 3, 31))
    check("periods: on 31 Mar 'at this point last month' is all of February (clamped)",
          span(march.compare_to[0]) == ("2026-02-01", "2026-02-28"))
    errors = []
    for bad in ("someday", "2026-13"):
        try:
            P(bad)
        except ValueError:
            errors.append(bad)
    try:
        P(start="2026-11-10", end="2026-11-01")
    except ValueError:
        errors.append("reversed")
    check("periods: unknown names, month 13 and reversed dates are refused", errors == ["someday", "2026-13", "reversed"])
    with patch.object(clock, "now", lambda: datetime.datetime(2027, 1, 2, 8, 0, tzinfo=TZ)):
        check("periods: 'today' comes from core.clock", periods.resolve("this_month").start == datetime.date(2027, 1, 1))


# --- Summary --------------------------------------------------------------------------------------

def test_summary() -> None:
    s = queries.summary(ROBIN, P("this_month"), group_by="group")
    check("summary: November spending exact (card bills, own transfers, unmatched and bank no-flow rows left out; "
          "refund reduces; provisional and pending counted)", s["total"] == NOV_SPENT and s["count"] == 16,
          (s["total"], s["count"]))
    check("summary: compared with 1–18 Oct and all of October",
          [(c["label"], c["total"]) for c in s["compare"]] == [("at this point last month", OCT_1_18),
                                                                ("all of last month", OCT_SPENT)], s["compare"])
    groups = {b["key"]: b["total"] for b in s["breakdown"]}
    check("summary: by group (uncategorized rows as their own group)",
          groups == {"food": 175_500, "health": 150_000, "bills": 75_890, "transport": 40_000, "people": 25_000,
                     "uncategorized": 8_500, "family": 1_594, "finance": 1_200}, groups)
    check("summary: the breakdown is largest first", [b["total"] for b in s["breakdown"]]
          == sorted((b["total"] for b in s["breakdown"]), reverse=True))
    zero = txn(ROBIN, C1, "2026-11-17", 0, "בנק הפועלים", category="fees", flow="fee")
    z = queries.summary(ROBIN, P("this_month"), group_by="group")
    listed = [t["id"] for t in queries.search(ROBIN, period=P("this_month"), limit=200)["transactions"]]
    with store.db() as conn:
        conn.execute("DELETE FROM money_transactions WHERE id = ?", (zero,))
    check("summary: a ₪0 line (a card company's notice) is listed but never counted",
          (z["total"], z["count"], {b["key"]: b["count"] for b in z["breakdown"]}.get("finance"))
          == (NOV_SPENT, 16, 1) and zero in listed, (z["total"], z["count"], z["breakdown"]))
    food = queries.summary(ROBIN, P("this_month"), category="food", group_by="category")
    check("summary: 'food' this month = groceries (NULL flow on a card counts; the refund reduces) + restaurants + "
          "delivery (the unmatched Wolt row left out)",
          food["total"] == 175_500 and food["category_label"] == "Food"
          and {b["key"]: b["total"] for b in food["breakdown"]} == {"groceries": 77_000, "restaurants": 94_000,
                                                                    "delivery": 4_500}
          and [c["total"] for c in food["compare"]] == [135_000, 155_000], food)
    check("summary: a Hebrew label and a category key work",
          queries.summary(ROBIN, P("this_month"), category="מסעדות ובתי קפה")["total"] == 94_000
          and queries.summary(ROBIN, P("this_month"), category="groceries")["total"] == 77_000)
    unc = queries.summary(ROBIN, P("this_month"), category="uncategorized")
    check("summary: uncategorized spending (the bank's no-flow row is not spending)",
          unc["total"] == 8_500 and unc["count"] == 2 and unc["category_label"] == "Uncategorized")
    oct_ = queries.summary(ROBIN, P("last_month"))
    check("summary: October whole (the mortgage as a loan counts), compared with September",
          oct_["total"] == OCT_SPENT and [(c["label"], c["total"]) for c in oct_["compare"]]
          == [("September 2026", SEP_SPENT)], oct_)
    elec = {p: queries.summary(ROBIN, P(p), category="electronics")["total"] for p in ("2026-09", "2026-10", "2026-11")}
    check("summary: installments count on their purchase date (both laptop payments in September)",
          elec == {"2026-09": 80_000, "2026-10": 0, "2026-11": 0}, elec)
    check("summary: income this month is the salary; a single flow (card_bill) shows its own total",
          queries.summary(ROBIN, P("this_month"), flow="income")["total"] == 1_500_000
          and queries.summary(ROBIN, P("this_month"), flow="card_bill")["total"] == 345_000
          and queries.summary(ROBIN, P("this_month"), flow="own_transfer")["total"] == 100_000)
    rami = queries.summary(ROBIN, P("this_month"), merchant="רמי לוי")
    check("summary: merchant filter (prefix of the merchant key / full text), only the user's own rows",
          rami["total"] == 32_000 and rami["count"] == 1
          and queries.summary(BOB, P("this_month"), merchant="רמי")["total"] == 10_000
          and queries.summary(ROBIN, P("this_month"), merchant="netflix")["total"] == 4_990)
    check("summary: account filter", queries.summary(ROBIN, P("this_month"), account_id=C2)["total"] == 18_000
          and queries.summary(ROBIN, P("this_month"), account_id=BANK)["total"] == 26_200)
    by_account = {b["label"]: b["total"]
                  for b in queries.summary(ROBIN, P("this_month"), group_by="account")["breakdown"]}
    check("summary: by account, labelled", by_account == {"Cal 8763": NOV_SPENT - 18_000 - 26_200,
                                                           "Shared card": 18_000, "Bank Hapoalim ••••901": 26_200},
          by_account)
    by_merchant = queries.summary(ROBIN, P("this_month"), group_by="merchant")["breakdown"]
    check("summary: by merchant (merchant key, display name)", by_merchant[0]["label"] == "סופר פארם"
          and by_merchant[0]["total"] == 150_000 and len(by_merchant) == 14, by_merchant[:3])
    months = queries.summary(ROBIN, P(start="2026-08-01", end="2026-11-18"), group_by="month")["breakdown"]
    check("summary: by month, oldest first", [(b["key"], b["total"]) for b in months]
          == [("2026-08", AUG_SPENT), ("2026-09", SEP_SPENT), ("2026-10", OCT_SPENT), ("2026-11", NOV_SPENT)], months)
    weeks = queries.summary(ROBIN, P("this_month"), group_by="week")["breakdown"]
    check("summary: by week (Sunday)", [b["key"] for b in weeks] == ["2026-11-01", "2026-11-08", "2026-11-15"]
          and sum(b["total"] for b in weeks) == NOV_SPENT and weeks[-1]["label"] == "week of 15 Nov 2026", weeks)
    days = queries.summary(ROBIN, P("this_week"), group_by="day")["breakdown"]
    check("summary: by day", [(b["key"], b["total"]) for b in days]
          == [("2026-11-16", 70_900), ("2026-11-17", 194_500), ("2026-11-18", 2_500)], days)
    check("summary: Bob sees only his own spending", queries.summary(BOB, P("this_month"))["total"] == 10_000)
    refused = []
    for kwargs in ({"account_id": BOB_CARD}, {"category": "spaceships"}, {"flow": "teleport"}, {"group_by": "planet"}):
        try:
            queries.summary(ROBIN, P("this_month"), **kwargs)
        except queries.NotFound:
            refused.append("not_found")
        except ValueError:
            refused.append("invalid")
    check("summary: another user's account answers like a missing one; unknown category / flow / group refused",
          refused == ["not_found", "invalid", "invalid", "invalid"], refused)


# --- Search and detail ----------------------------------------------------------------------------

def test_search() -> None:
    wolt = queries.search(ROBIN, query="wolt")["transactions"]
    check("search: full text finds both Wolt rows, the unmatched one listed with its badge",
          [t["id"] for t in wolt] == [T["wolt"], T["wolt_unmatched"]]
          and wolt[0]["badges"] == [{"type": "pending"}] and wolt[1]["badges"] == [{"type": "unmatched"}])
    by_amount = queries.search(ROBIN, query="₪709")["transactions"]
    check("search: an amount alone finds the charge", [t["id"] for t in by_amount] == [T["insurance"]]
          and by_amount[0]["badges"] == [{"type": "installment", "number": 1, "total": 4}])
    course = queries.search(ROBIN, query="course")["transactions"][0]
    check("search: a foreign charge carries its original amount", course["badges"]
          == [{"type": "foreign", "amount": -500, "currency": "USD"}] and course["account_label"] == "Cal 8763"
          and course["category_label"] == "Education" and course["group_label"] == "Family")
    memo = queries.search(ROBIN, query="כרטיסים")["transactions"]
    check("search: a transfer note is searched too", [t["id"] for t in memo] == [T["dani"]])
    big = queries.search(ROBIN, period=P("this_month"), min_amount=1000)["transactions"]
    check("search: an amount range (absolute ₪) within a period",
          sorted(t["amount"] for t in big) == [-345_000, -150_000, -100_000, 1_500_000], [t["amount"] for t in big])
    cat = queries.search(ROBIN, period=P("this_month"), category="uncategorized")["transactions"]
    check("search: uncategorized rows (incl. the bank's)",
          {t["id"] for t in cat} == {T["coffee"], T["kolbo"], T["cash"], T["bill"], T["own"]}
          and next(t for t in cat if t["id"] == T["coffee"])["badges"] == [{"type": "provisional"}])
    check("search: NULL flows as the numbers use them", next(t for t in cat if t["id"] == T["cash"])["flow"] == "other"
          and not next(t for t in cat if t["id"] == T["cash"])["spending"]
          and next(t for t in cat if t["id"] == T["kolbo"])["flow"] == "purchase")
    everything = queries.search(ROBIN, period=P("this_month"), limit=200)["transactions"]
    pages, cursor = [], None
    while True:
        page = queries.search(ROBIN, period=P("this_month"), limit=7, cursor=cursor)
        pages.append(page["transactions"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    flat = [t["id"] for p in pages for t in p]
    check("search: cursor pages cover everything once, newest first",
          flat == [t["id"] for t in everything] and len(flat) == len(set(flat)) == 21 and len(pages) == 3
          and everything[0]["id"] == T["coffee"]
          and [t["purchase_date"] for t in everything] == sorted((t["purchase_date"] for t in everything),
                                                                 reverse=True), (len(flat), len(pages)))
    check("search: the same day ordered by time (newest first)",
          [t["id"] for t in everything if t["purchase_date"] == "2026-11-17"][:3] == [T["pharm"], T["fuel2"], T["fuel1"]])
    check("search: Bob's search never sees Robin's rows", [t["id"] for t in queries.search(BOB, query="רמי")["transactions"]]
          == [T["bob_rami"]])
    refused = []
    for kwargs in ({"account_id": BOB_CARD}, {"cursor": "not-a-cursor"}):
        try:
            queries.search(ROBIN, **kwargs)
        except queries.NotFound:
            refused.append("not_found")
        except ValueError:
            refused.append("invalid")
    check("search: another user's account → not found; a bad cursor → invalid", refused == ["not_found", "invalid"])


def test_detail() -> None:
    d = queries.transaction(ROBIN, T["course"])
    check("detail: foreign amount, both dates, the bank's category; never the stored raw JSON",
          d["foreign"] == {"amount": -500, "currency": "USD"} and d["bank_category"] == "חינוך"
          and "raw" not in d and "raw-only" not in json.dumps(d, ensure_ascii=False)
          and d["occurred_at"] == "2026-11-07T17:07:18+02:00" and d["source"] == "scrape", d)
    i = queries.transaction(ROBIN, T["insurance"])
    check("detail: installment n of N with the purchase amount; charge date apart from the purchase date",
          i["installment"] == {"number": 1, "total": 4, "purchase_amount": -283_600}
          and i["purchase_date"] == "2026-11-16" and i["charge_date"] == "2026-12-10")
    p = queries.transaction(ROBIN, T["pharm"])
    check("detail: the merchant's history", p["same_merchant"] == {"count": 5, "total": -192_000,
                                                                   "first_date": "2026-09-05"}, p["same_merchant"])
    try:
        queries.transaction(ROBIN, T["bob_rami"])
        check("detail: another user's transaction is not found", False)
    except queries.NotFound:
        check("detail: another user's transaction is not found", True)


# --- Overview, pace, months ---------------------------------------------------------------------------

def test_overview() -> None:
    o = {**queries.overview(ROBIN), **dashboard.tiles(ROBIN)}
    check("overview: spent so far vs the same day last month; income",
          o["spent"]["total"] == NOV_SPENT and o["spent"]["compare"][0]["total"] == OCT_1_18
          and o["income"]["total"] == 1_500_000 and o["month"] == "2026-11")
    check("overview: each card's next bill, the bank's balance (masked number)",
          [(c["label"], c["next_charge"], c["next_charge_date"]) for c in o["cards"]]
          == [("Shared card", 120_000, "2026-11-25"), ("Cal 8763", 345_081, "2026-12-10")]
          and [(b["number"], b["balance"]) for b in o["banks"]] == [("••••901", 1_234_567)], (o["cards"], o["banks"]))
    fc = o["forecast"]
    check("overview: the forecast from the bank's balance — the benefit and the mortgage on 20 Nov, the shared "
          "card's bill on 25 Nov (the lowest point), then December's salary",
          fc["balance"] == 1_234_567 and fc["as_of"] == "2026-11-18"
          and [(e["label"], e["amount"], e["date"]) for e in fc["next"][:4]]
          == [("קצבת ילדים", 17_300, "2026-11-20"), ("משכנתא לאומי", -450_000, "2026-11-20"),
              ("Shared card", -120_000, "2026-11-25"), ("משכורת נוגה תעשיות", 1_500_000, "2026-12-10")]
          and fc["soon_low"] == {"date": "2026-11-25", "balance": 1_234_567 + 17_300 - 450_000 - 120_000, "days": 45},
          fc)
    check("overview: recurring monthly total, uncategorized count, sources and what needs attention",
          o["recurring_monthly"] == 450_000 + 4_990 + 500 and o["uncategorized"] == 2
          and [s["company"] for s in o["sources"]] == ["hapoalim", "visaCal"]
          and o["attention"] == [{"source_id": S_CAL, "name": "Cal", "state": "failing",
                                  "reason": "bank worker not reachable"}]
          and o["last_sync_at"] == "2026-11-18T05:01:00+00:00", (o["recurring_monthly"], o["attention"]))
    past = {**queries.overview(ROBIN, "2026-10"), **dashboard.tiles(ROBIN, "2026-10")}
    check("overview: a past month is whole, compared with the month before, with no forecast",
          past["spent"]["total"] == OCT_SPENT and past["spent"]["compare"][0]["total"] == SEP_SPENT
          and past["forecast"] is None and "projection" not in past)
    b = {**queries.overview(BOB), **dashboard.tiles(BOB)}
    check("overview: Bob's own (no bank: no forecast)", b["spent"]["total"] == 10_000 and b["forecast"] is None
          and [c["number"] for c in b["cards"]] == ["1111"] and b["attention"] == [])


def test_pace_and_months() -> None:
    p = dashboard.everyday_pace(dashboard.load(ROBIN))
    this = p["this_month"]
    check("pace: everyday spending this month day by day (cumulative), nothing after today",
          p["days"] == 30 and p["today"] == 18 and this[18] is None and this[17] is not None
          and this[0] == 1_200, this[:5])
    check("pace: typical / low / high day by day from the whole months before",
          p["months"] == 6 and len(p["typical"]) == 30 and all(lo <= t <= hi for lo, t, hi in
                                                              zip(p["low"], p["typical"], p["high"])))
    past = dashboard.everyday_pace(dashboard.load(ROBIN), "2026-10")
    check("pace: a past month has every day and no 'today'", past["today"] is None and None not in past["this_month"])
    m = queries.months(ROBIN, 12)
    check("months: 12 months ending this month, stacked by group",
          [x["month"] for x in m["months"]][0] == "2025-12" and m["months"][-1]["month"] == "2026-11"
          and m["months"][-1]["total"] == NOV_SPENT and m["months"][-2]["total"] == OCT_SPENT
          and m["months"][-2]["groups"] == {"bills": 3_990, "health": 22_000, "food": 155_000, "housing": 450_000}
          and m["months"][0]["groups"] == {"bills": 6_000}
          and [g["key"] for g in m["groups"]] == ["food", "housing", "transport", "bills", "health", "shopping",
                                                   "family", "people", "finance", "uncategorized"], m["groups"])
    tree = queries.category_tree(ROBIN, P("this_month"))
    food = next(g for g in tree["groups"] if g["key"] == "food")
    check("categories: groups with their categories, largest first",
          tree["groups"][0]["key"] == "food" and food["total"] == 175_500
          and [(c["key"], c["total"]) for c in food["categories"]] == [("restaurants", 94_000), ("groceries", 77_000),
                                                                      ("delivery", 4_500)]
          and tree["total"] == NOV_SPENT, tree["groups"][:2])


# --- Insights -----------------------------------------------------------------------------------------

def row(day: str, amount: int, key: str, **kw: Any) -> Dict[str, Any]:
    return {"id": kw.pop("id", uuid.uuid4().hex), "purchase_date": day, "amount": amount, "merchant_key": key,
            "merchant": kw.pop("merchant", key), "status": kw.pop("status", "completed"),
            "occurred_at": kw.pop("occurred_at", None), "installment_total": kw.pop("installment_total", None),
            "installment_number": None, "account_kind": "card", "account_company": "visaCal",
            "account_number": "8763", "account_id": "acc", "category": kw.pop("category", None),
            "eff_flow": kw.pop("flow", "purchase"), "description": key, **kw}


def test_insights_pure() -> None:
    today = TODAY
    monthly = [row(d, -3_990, "spotify") for d in ("2026-07-02", "2026-08-02", "2026-09-02")] + \
              [row("2026-10-02", -4_490, "spotify"), row("2026-11-02", -4_490, "spotify")]
    found = insights.detect_recurring(monthly, today)
    check("recurring: a monthly charge with a price change (₪39.90 → ₪44.90 since 2 Oct)",
          len(found) == 1 and found[0]["cadence"] == "monthly" and found[0]["amount"] == 4_490
          and found[0]["price_change"] == {"from": 3_990, "to": 4_490, "since": "2026-10-02"}
          and found[0]["next_date"] == "2026-12-02" and found[0]["monthly_cost"] == 4_490, found)
    weekly = [row((today - datetime.timedelta(days=7 * k)).isoformat(), -2_000, "gym") for k in range(5)]
    w = insights.detect_recurring(weekly, today)
    check("recurring: weekly (5 weeks) with its monthly cost", [(i["cadence"], i["monthly_cost"]) for i in w]
          == [("weekly", round(2_000 * 52 / 12))], w)
    yearly = [row("2024-11-20", -12_000, "domain"), row("2025-11-21", -12_000, "domain")]
    check("recurring: yearly (two years)", [(i["cadence"], i["monthly_cost"], i["next_date"])
                                            for i in insights.detect_recurring(yearly, today)]
          == [("yearly", 1_000, "2026-11-21")])
    inst = [row(d, -70_900, "car insurance", installment_total=4) for d in ("2026-08-24", "2026-09-24", "2026-10-24")]
    check("recurring: card installments are not a subscription", insights.detect_recurring(inst, today) == [])
    two = [row(d, -3_990, "x") for d in ("2026-10-02", "2026-11-02")]
    irregular = [row(d, -3_990, "y") for d in ("2026-08-02", "2026-08-20", "2026-10-02", "2026-11-12")]
    stopped = [row(d, -3_990, "z") for d in ("2026-05-02", "2026-06-02", "2026-07-02")]
    varied = [row(d, a, "market") for d, a in (("2026-09-08", -55_000), ("2026-10-06", -60_000),
                                               ("2026-11-10", -50_000))]
    bills = [row(d, -300_000, "cal", flow="card_bill") for d in ("2026-09-02", "2026-10-02", "2026-11-02")]
    check("recurring: two months, irregular gaps, a stopped series, varying amounts and card bills are not",
          insights.detect_recurring(two + irregular + stopped + varied + bills, today) == [])
    health = [row(d, a, "maccabi", category="health") for d, a in
              (("2026-07-03", -9_604), ("2026-08-03", -21_840), ("2026-09-03", -12_100), ("2026-10-03", -15_200),
               ("2026-11-03", -16_000))]
    electric = [row(d, a, "electric", category="utilities") for d, a in
                (("2026-05-20", -62_000), ("2026-07-21", -31_000), ("2026-09-19", -45_000), ("2026-11-18", -97_500))]
    groceries = [row(d, a, "butcher", category="groceries") for d, a in
                 (("2026-08-05", -6_533), ("2026-09-04", -15_745), ("2026-10-06", -9_000), ("2026-11-05", -12_000))]
    found = {i["merchant_key"]: i for i in insights.detect_recurring(health + electric + groceries, today)}
    check("recurring: a health fund whose amount varies is a variable monthly bill (median of the last three)",
          found.get("maccabi", {}).get("variable") is True and found["maccabi"]["cadence"] == "monthly"
          and found["maccabi"]["amount"] == 15_200 and found["maccabi"]["price_change"] is None, found.get("maccabi"))
    check("recurring: electricity billed every two months, varying", found.get("electric", {}).get("cadence") ==
          "bimonthly" and found["electric"]["variable"] and found["electric"]["monthly_cost"] == round(45_000 * 0.5)
          and found["electric"]["next_date"] == "2027-01-18", found.get("electric"))
    check("recurring: a monthly butcher with varying amounts is still not recurring", "butcher" not in found, found)
    check("recurring: a fixed-price subscription is not variable", monthly and not insights.detect_recurring(
        monthly, today)[0]["variable"])
    salary = [row(d, 1_500_000, "salary", flow="income") for d in ("2026-09-10", "2026-10-10", "2026-11-10")]
    check("recurring: regular income is found as money in", [(i["direction"], i["merchant_key"])
                                                             for i in insights.detect_recurring(salary, today)]
          == [("in", "salary")])

    hist = [row(d, a, "pharm") for d, a in (("2026-09-05", -9_000), ("2026-09-20", -11_000), ("2026-10-04", -10_000),
                                            ("2026-10-22", -12_000))]
    big = row("2026-11-17", -40_000, "pharm")
    small_hist = hist[1:]
    found = insights.find_unusual([big], hist + [big])
    check("unusual: ≥ 3× the median of ≥ 4 earlier charges", [(u["reason"], u["typical"]) for u in found]
          == [("large", 10_500)] and found[0]["why"] == "about 4× the usual ₪105.00 there"
          and found[0]["title"] == "₪400.00 at pharm", found)
    check("unusual: not with only 3 earlier charges, nor under 3×",
          insights.find_unusual([big], small_hist + [big]) == []
          and insights.find_unusual([row("2026-11-17", -30_000, "pharm")], hist) == [])
    first = row("2026-11-17", -60_000, "new shop")
    check("unusual: a first charge from a merchant over ₪500 (not at ₪500)",
          [u["reason"] for u in insights.find_unusual([first], [first])] == ["first_time"]
          and insights.find_unusual([row("2026-11-17", -50_000, "other shop")], []) == [])
    a = row("2026-11-17", -20_000, "fuel", id="a", occurred_at="2026-11-17T12:00:00+02:00")
    b = row("2026-11-17", -20_000, "fuel", id="b", occurred_at="2026-11-17T12:09:00+02:00")
    late = row("2026-11-17", -20_000, "fuel", id="c", occurred_at="2026-11-17T12:11:00+02:00")
    dup = insights.find_unusual([a, b], [a, b])
    check("unusual: the same amount at the same merchant within 10 minutes (the later one)",
          [(u["transaction"]["id"], u["reason"], u["duplicate_of"]) for u in dup] == [("b", "duplicate", "a")]
          and dup[0]["why"] == "charged twice within 9 minutes", dup)
    no_time = [row("2026-11-17", -20_000, "fuel2", id="d"), row("2026-11-17", -20_000, "fuel2", id="e")]
    prov = row("2026-11-17", -20_000, "fuel", id="f", status="provisional", occurred_at="2026-11-17T12:01:00+02:00")
    check("unusual: not 11 minutes apart, not without times, not a notification row beside its bank row",
          insights.find_unusual([late], [a, late]) == [] and insights.find_unusual(no_time, no_time) == []
          and insights.find_unusual([prov], [a, prov]) == [])


def test_insights_db() -> None:
    rec = insights.recurring(ROBIN)
    items = {i["merchant"]: i for i in rec["items"]}
    check("recurring (Robin): the mortgage, Netflix and the yearly domain; groceries, installments and card bills not",
          set(items) == {"משכנתא לאומי", "NETFLIX.COM", "NAMECHEAP"} and rec["monthly_total"] == 455_490, set(items))
    n = items["NETFLIX.COM"]
    check("recurring: Netflix ₪39.90 → ₪49.90 since 3 Nov, next 3 Dec, on Cal 8763",
          n["price_change"] == {"from": 3_990, "to": 4_990, "since": "2026-11-03"} and n["next_date"] == "2026-12-03"
          and n["account_label"] == "Cal 8763" and n["category_label"] == "Subscriptions & software"
          and items["משכנתא לאומי"]["next_date"] == "2026-11-20" and items["NAMECHEAP"]["cadence"] == "yearly", n)
    check("recurring: income (salary, the benefit)", sorted(i["merchant"] for i in rec["income"])
          == sorted(["משכורת נוגה תעשיות", "קצבת ילדים"]))
    check("recurring: Bob has none", insights.recurring(BOB) == {"items": [], "income": [], "monthly_total": 0})
    unusual = insights.unusual(ROBIN, "2026-11-16")
    check("unusual (Robin, since 16 Nov): Super-Pharm 14×, the fuel charged twice, the first insurance charge",
          [(u["transaction"]["id"], u["reason"]) for u in unusual]
          == [(T["pharm"], "large"), (T["fuel2"], "duplicate"), (T["insurance"], "first_time")], unusual)
    check("unusual: Bob has none", insights.unusual(BOB, "2026-11-01") == [])


# --- The API --------------------------------------------------------------------------------------------

async def call(client: TestClient, method: str, path: str, who, body: Any = None) -> Tuple[int, Any]:
    r = await client.request(method, path, headers={"Authorization": f"Bearer {TOKENS[who.id]}"},
                             json=body if body is not None else None)
    text = await r.text()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


PAGE_ROUTES = ("/api/money/overview", "/api/money/pace", "/api/money/categories", "/api/money/months",
               "/api/money/transactions", "/api/money/recurring", "/api/money/changes")


async def test_api(client: TestClient) -> None:
    carl = [await call(client, "GET", p, CARL) for p in PAGE_ROUTES]
    carl.append(await call(client, "GET", f"/api/money/transactions/{T['rami']}", CARL))
    carl.append(await call(client, "PATCH", f"/api/money/transactions/{T['rami']}", CARL, {"note": "x"}))
    check("api: without money every page route answers 403 not_connected",
          all(s == 403 and b.get("error") == "not_connected" for s, b in carl), carl[:2])
    cross = [await call(client, "GET", f"/api/money/transactions/{T['bob_rami']}", ROBIN),
             await call(client, "PATCH", f"/api/money/transactions/{T['bob_rami']}", ROBIN, {"note": "x"}),
             await call(client, "GET", f"/api/money/transactions?account={BOB_CARD}", ROBIN),
             await call(client, "GET", f"/api/money/transactions/{T['rami']}", BOB)]
    check("api: another user's transaction / account answers 404", [s for s, _ in cross] == [404, 404, 404, 404], cross)
    bad = [await call(client, "GET", "/api/money/overview?month=11-2026", ROBIN),
           await call(client, "GET", "/api/money/categories?period=someday", ROBIN),
           await call(client, "GET", "/api/money/transactions?min=lots", ROBIN),
           await call(client, "GET", "/api/money/transactions?cursor=zzz", ROBIN),
           await call(client, "PATCH", f"/api/money/transactions/{T['rami']}", ROBIN, {"category": "spaceships"}),
           await call(client, "PATCH", f"/api/money/transactions/{T['rami']}", ROBIN, {})]
    check("api: malformed month / period / numbers / cursor / category answer 400", [s for s, _ in bad]
          == [400] * 6 and all(b.get("error") == "invalid" for _, b in bad), bad)
    s, ov = await call(client, "GET", "/api/money/overview", ROBIN)
    check("api: overview (this month) with the unusual charges of the month",
          s == 200 and ov["spent"]["total"] == NOV_SPENT
          and [u["transaction"]["id"] for u in ov["unusual"]] == [T["pharm"], T["fuel2"], T["insurance"]], ov.get("unusual"))
    s, tx = await call(client, "GET", "/api/money/transactions?period=this_month&q=wolt", ROBIN)
    check("api: transactions filtered", s == 200 and [t["id"] for t in tx["transactions"]] == [T["wolt"], T["wolt_unmatched"]])
    s, c0 = await call(client, "GET", "/api/money/changes", ROBIN)
    s1, c1 = await call(client, "GET", f"/api/money/changes?since={c0['cursor']}", ROBIN)
    check("api: changes: a first poll is changed, the next one with its cursor is not",
          s == 200 and c0["changed"] is True and s1 == 200 and c1 == {"changed": False, "cursor": c0["cursor"]})
    s, p = await call(client, "PATCH", f"/api/money/transactions/{T['shufersal']}", ROBIN,
                      {"category": "home", "for_merchant": True})
    others = queries.search(ROBIN, query="שופרסל")["transactions"]
    check("api: PATCH category for the merchant: every row of it changed, one undo id",
          s == 200 and p["transaction"]["category"] == "home" and p["changed"] == 4 and p["undo_id"]
          and {t["category"] for t in others} == {"home"}, p)
    _, c2 = await call(client, "GET", f"/api/money/changes?since={c0['cursor']}", ROBIN)
    _, bob_changes = await call(client, "GET", "/api/money/changes", BOB)
    check("api: changes after the PATCH", c2["changed"] is True and c2["cursor"] != c0["cursor"]
          and bob_changes["cursor"] != c2["cursor"])
    s, u = await call(client, "POST", f"/api/undo/{p['undo_id']}", ROBIN)
    back = queries.transaction(ROBIN, T["shufersal"])["category"]
    check("api: the undo puts the categories back", s == 200 and back == "groceries"
          and {t["category"] for t in queries.search(ROBIN, query="שופרסל")["transactions"]} == {"groceries"}, (s, u))
    s, both = await call(client, "PATCH", f"/api/money/transactions/{T['kolbo']}", ROBIN,
                         {"category": "shopping", "for_merchant": False, "note": "a gift"})
    check("api: PATCH category + note → one undo for both", s == 200 and both["transaction"]["category"] == "shopping"
          and both["transaction"]["note"] == "a gift" and both["changed"] == 1)
    s, _ = await call(client, "POST", f"/api/undo/{both['undo_id']}", BOB)
    check("api: another user can't use the undo id", s == 404)
    s, _ = await call(client, "POST", f"/api/undo/{both['undo_id']}", ROBIN)
    k = queries.transaction(ROBIN, T["kolbo"])
    check("api: that undo restores both", s == 200 and k["category"] is None and k["note"] is None, k)


# --- The web fixture -------------------------------------------------------------------------------------

def shape(value: Any) -> Any:
    """The keys of a payload, recursively (lists by their items' shapes, values dropped)."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__ if value is not None else None


async def test_web_fixture(client: TestClient) -> None:
    captured: Dict[str, Any] = {}

    async def grab(key: str, method: str, path: str, body: Any = None, who=ROBIN) -> Any:
        status, payload = await call(client, method, path, who, body)
        captured[key] = {"request": f"{method} {path}", "status": status, "body": payload}
        if body is not None:
            captured[key]["request_body"] = body
        return payload

    await grab("overview", "GET", "/api/money/overview")
    await grab("overview_past", "GET", "/api/money/overview?month=2026-10")
    await grab("pace", "GET", "/api/money/pace")
    await grab("categories", "GET", "/api/money/categories?period=this_month")
    await grab("categories_range", "GET", "/api/money/categories?start=2026-10-01&end=2026-10-31")
    await grab("months", "GET", "/api/money/months?n=12")
    status, upto = await call(client, "GET", "/api/money/months?n=3&end=2026-10", ROBIN)
    bad, _ = await call(client, "GET", "/api/money/months?end=10-2026", ROBIN)
    check("months: end= ends the bars at that month (a past month's Everything bars); a bad month → 400",
          status == 200 and [m["month"] for m in upto["months"]] == ["2026-08", "2026-09", "2026-10"]
          and upto["months"][-1]["total"] == OCT_SPENT and bad == 400, (status, upto, bad))
    first = await grab("transactions", "GET", "/api/money/transactions?period=this_month&limit=12")
    await grab("transactions_next", "GET",
               f"/api/money/transactions?period=this_month&limit=12&cursor={first['next_cursor']}")
    await grab("transactions_search", "GET", "/api/money/transactions?q=wolt")
    await grab("transactions_filtered", "GET",
               f"/api/money/transactions?period=last_30_days&category=food&account={C1}&min=100")
    await grab("transaction_foreign", "GET", f"/api/money/transactions/{T['course']}")
    await grab("transaction_installment", "GET", f"/api/money/transactions/{T['insurance']}")
    await grab("transaction_provisional", "GET", f"/api/money/transactions/{T['coffee']}")
    await grab("recurring", "GET", "/api/money/recurring")
    await grab("cashflow", "GET", "/api/money/cashflow?n=12")
    await grab("balance", "GET", "/api/money/balance?days=90")
    await grab("trends", "GET", "/api/money/trends")
    await grab("trends_past", "GET", "/api/money/trends?month=2026-10")
    await grab("merchants", "GET", "/api/money/merchants?period=last_30_days")
    await grab("insights", "GET", "/api/money/insights")
    await grab("insights_past", "GET", "/api/money/insights?month=2026-10")
    ch = await grab("changes", "GET", "/api/money/changes")
    await grab("changes_unchanged", "GET", f"/api/money/changes?since={ch['cursor']}")
    patched = await grab("patch_category", "PATCH", f"/api/money/transactions/{T['coffee']}",
                         {"category": "restaurants", "for_merchant": True})
    await grab("changes_after_patch", "GET", f"/api/money/changes?since={ch['cursor']}")
    await grab("undo", "POST", f"/api/undo/{patched['undo_id']}")
    await grab("patch_note", "PATCH", f"/api/money/transactions/{T['coffee']}", {"note": "with Noa"})
    flow = await grab("patch_flow", "PATCH", f"/api/money/transactions/{T['course']}", {"flow": "investment"})
    await grab("undo_flow", "POST", f"/api/undo/{flow['undo_id']}")
    marked = await grab("patch_one_off", "PATCH", f"/api/money/transactions/{T['course']}", {"one_off": True})
    await grab("undo_one_off", "POST", f"/api/undo/{marked['undo_id']}")
    await grab("error_not_connected", "GET", "/api/money/overview", who=CARL)
    await grab("error_not_found", "GET", f"/api/money/transactions/{T['bob_rami']}")
    await grab("error_invalid", "GET", "/api/money/categories?period=someday")
    check("fixture: every captured call answered as expected",
          [k for k, v in captured.items() if v["status"] != 200 and not k.startswith("error_")] == []
          and [captured[k]["status"] for k in ("error_not_connected", "error_not_found", "error_invalid")]
          == [403, 404, 400])
    check("fixture: representative (provisional, unmatched, installment, foreign, refund, uncategorized, a price "
          "change, an unusual charge, a forecast, attention)",
          {b["type"] for t in captured["transactions"]["body"]["transactions"]
           + captured["transactions_next"]["body"]["transactions"] for b in t["badges"]}
          >= {"provisional", "pending", "unmatched", "installment", "foreign"}
          and any(t["amount"] > 0 and t["flow"] == "refund" for t in captured["transactions_next"]["body"]["transactions"]
                  + captured["transactions"]["body"]["transactions"])
          and any(i["price_change"] for i in captured["recurring"]["body"]["items"])
          and captured["overview"]["body"]["unusual"] and captured["overview"]["body"]["forecast"]
          and captured["overview"]["body"]["attention"] and captured["overview"]["body"]["uncategorized"])
    text = json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
    else:
        with open(WEB_FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))] + [k for k in stored if k not in captured]
        check("fixture: tests/fixtures/money_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_money_page)", not drift, drift)


async def main() -> None:
    test_periods()
    test_summary()
    test_search()
    test_detail()
    test_overview()
    test_pace_and_months()
    test_insights_pure()
    test_insights_db()
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    actions_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_api(client)
        await test_web_fixture(client)


asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
