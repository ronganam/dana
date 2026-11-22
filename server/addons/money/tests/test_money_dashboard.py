"""Money dashboards: spend kinds, yearly charges, cash flow, the balance history and forecast, trends, merchants, insights, the dashboard API, the marks with Undo.
Run: bin/test test_money_dashboard

A synthetic year, every value made up, with today fixed at Wed 18 Nov 2026
09:30 (Asia/Jerusalem). Robin (the first admin) has a bank account and a
card billed on the 15th: a salary on the 1st whose amount varies (on
Sunday 2 Aug when the 1st was a Saturday), a mortgage, groceries every
Friday and restaurants twice a month, Netflix monthly with a price change,
electricity every ~2 months on drifting days, a car insurance once a year, a
home insurance paid in 3 installments, the car licence, an investment
transfer, a car bought by bank transfer, a wedding gift, a laptop with a
partial refund and a furniture plan in 4 payments. Bob has one card; Carl
has no ``money``. Covers: the kinds (fixed / yearly / one-off / everyday,
the user's marks win, a refund of a one-off, an installment plan at its
total, the yearly cap); recurring fixes (a salary's usual day, a merchant
under two spellings, bimonthly gaps 50–80 days); cash flow per month
(asserted exactly); the balance rebuilt backwards; the forecast (income and
debits on their days, Saturday → Sunday, card bills = what is known + the
plans' remaining payments + recurring and yearly card charges + everyday
spending at its rate, one-offs never projected, the band, the low points);
trends (typical medians, on track, movers from everyday spending only); merchants;
insights; everyday pace; the API for two users and one without (403
``not_connected``), 400 on a bad month; PATCH flow / one_off / yearly with
one Undo.
"""

import asyncio
import datetime
import json
import os
import sqlite3
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_dashboard_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-dashboard-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Robin"
sys.path.insert(0, ROOT)

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moneyfakes  # noqa: E402  (the made-up provider: moneyfakes.py)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import actions_api, clock  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import api as money_api, dashboard, ingest, insights, queries, store  # noqa: E402

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

ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
STAMP = "2026-11-01T00:00:00+00:00"

# --- The dataset --------------------------------------------------------------------------------


def account(user, kind: str, number: str, **kw: Any) -> str:
    aid = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, balance, balance_at, "
                     "next_charge, next_charge_date, billed_from_account_id, created_at, updated_at) "
                     "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (aid, user.id, "samplebank" if kind == "bank" else "samplecard", kind, number, kw.get("balance"),
                      kw.get("balance_at"), kw.get("next_charge"), kw.get("next_charge_date"), kw.get("billed_from"),
                      STAMP, STAMP))
    return aid


ROWS: List[Dict[str, Any]] = []


def txn(user, aid: str, day: D, amount: int, desc: str, category: Optional[str], flow: str, *,
        charge: Optional[D] = None, inst: Optional[Tuple[int, int]] = None, orig: Optional[int] = None,
        memo: Optional[str] = None, merchant: Optional[str] = None) -> str:
    tid = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute(
            "INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, occurred_at, "
            "purchase_date, charge_date, amount, orig_amount, orig_currency, description, merchant, merchant_key, memo, "
            "category, category_source, flow, installment_number, installment_total, raw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'scrape', 'completed', ?, ?, ?, ?, ?, 'ILS', ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)",
            (tid, user.id, aid, f"x:{tid}", f"{day.isoformat()}T12:00:00+02:00", day.isoformat(),
             charge.isoformat() if charge else None, amount, orig if orig is not None else amount, desc,
             merchant or desc, ingest.merchant_key(desc), memo, category, "model" if category else None, flow,
             inst[0] if inst else None, inst[1] if inst else None, STAMP, STAMP))
    ROWS.append({"id": tid, "user": user.id, "account": aid, "day": day, "amount": amount, "desc": desc,
                 "category": category, "flow": flow, "charge": charge})
    return tid


def bill(day: D) -> D:
    return dashboard.bill_date(day, 15)


BANK = account(ROBIN, "bank", "12-345-678901", balance=2_000_000, balance_at="2026-11-18T05:00:00+00:00")
CARD = account(ROBIN, "card", "8763", next_charge=150_000, next_charge_date="2026-12-15", billed_from=BANK)
BOB_CARD = account(BOB, "card", "4580")
T: Dict[str, str] = {}

months = [D(2025, 12, 1)] + [D(2026, m, 1) for m in range(1, 12)]
SALARY = [2_300_000, 2_500_000, 2_350_000, 2_400_000, 2_550_000, 2_300_000, 2_450_000, 2_400_000, 2_520_000,
          2_380_000, 2_410_000, 2_415_000]
for i, m in enumerate(months):
    day = D(2026, 8, 2) if m == D(2026, 8, 1) else m  # 1 Aug 2026 was a Saturday
    T[f"salary_{m:%Y-%m}"] = txn(ROBIN, BANK, day, SALARY[i], "משכורת נוגה תעשיות בע\"מ", "salary", "income")
    T[f"mortgage_{m:%Y-%m}"] = txn(ROBIN, BANK, day, -(760_000 - 1_000 * i), "פועלים-משכנתא", "housing", "loan")

# Card everyday: groceries every Friday, restaurants on the 8th and the 22nd.
EVERYDAY: Dict[str, int] = {}
day = D(2025, 12, 1)
while day < TODAY:
    if day.weekday() == 4:
        amount = 25_000 + (day.month * 1_300 + day.day * 70) % 10_000
        txn(ROBIN, CARD, day, -amount, "שופרסל דיל", "groceries", "purchase", charge=bill(day))
        EVERYDAY[f"{day:%Y-%m}"] = EVERYDAY.get(f"{day:%Y-%m}", 0) + amount
    if day.day in (8, 22):
        amount = 12_000 + day.month * 500
        txn(ROBIN, CARD, day, -amount, "קפה ביאליק", "restaurants", "purchase", charge=bill(day))
        EVERYDAY[f"{day:%Y-%m}"] = EVERYDAY.get(f"{day:%Y-%m}", 0) + amount
    day += datetime.timedelta(days=1)
for d, amount in ((D(2026, 11, 14), 15_000), (D(2026, 11, 16), 20_000)):  # on the coming bill already
    txn(ROBIN, CARD, d, -amount, "מאפה נאמן", "groceries", "purchase", charge=bill(d))
    EVERYDAY["2026-11"] += amount
# Netflix on the 5th, ₪39.90 then ₪49.90 from November.
for m in months:
    d = m.replace(day=5)
    txn(ROBIN, CARD, d, -(4_990 if m >= D(2026, 11, 1) else 3_990), "NETFLIX.COM", "subscriptions", "purchase",
        charge=bill(d))
# Electricity every ~2 months on drifting days (gaps 60, 65, 53, 62 days), amounts following use.
for d, amount in ((D(2026, 1, 19), 50_000), (D(2026, 3, 20), 62_000), (D(2026, 5, 24), 54_000),
                  (D(2026, 7, 16), 61_000), (D(2026, 9, 16), 97_500)):
    txn(ROBIN, CARD, d, -amount, "חברת החשמל לישראל", "utilities", "purchase", charge=bill(d))
# Yearly: the car insurance, the home insurance in 3 payments, the car licence.
T["insurance"] = txn(ROBIN, CARD, D(2025, 12, 28), -240_000, "ביטוח ישיר", "insurance", "purchase",
                     charge=bill(D(2025, 12, 28)))
T["home"] = []  # type: ignore[assignment]
for n in (1, 2, 3):
    T[f"home_{n}"] = txn(ROBIN, CARD, D(2026, 3, 10), -40_000, "הראל ביטוח דירה", "insurance", "purchase",
                         charge=D(2026, 3 + n, 15), inst=(n, 3), orig=-120_000)
T["licence"] = txn(ROBIN, CARD, D(2026, 7, 20), -130_000, "משרד התחבורה", "car", "purchase", charge=bill(D(2026, 7, 20)))
# One-offs: the car (by bank transfer), a laptop and its partial refund, a furniture plan, a wedding gift.
T["car"] = txn(ROBIN, BANK, D(2025, 12, 22), -3_100_000, "העב' לאחר-נייד", "car", "purchase",
               memo="לטובת: מוטורס הצפון בע. עבור: רכב.", merchant="מוטורס הצפון")
T["laptop"] = txn(ROBIN, CARD, D(2026, 8, 10), -400_000, "KSP מחשבים", "electronics", "purchase",
                  charge=bill(D(2026, 8, 10)))
T["laptop_refund"] = txn(ROBIN, CARD, D(2026, 8, 20), 50_000, "KSP מחשבים", "electronics", "refund",
                         charge=bill(D(2026, 8, 20)))
T["sofa_1"] = txn(ROBIN, CARD, D(2026, 10, 5), -120_000, "רהיטי הבית", "home", "purchase", charge=D(2026, 11, 15),
                  inst=(1, 4), orig=-480_000)
T["sofa_2"] = txn(ROBIN, CARD, D(2026, 10, 5), -120_000, "רהיטי הבית", "home", "purchase", charge=D(2026, 12, 15),
                  inst=(2, 4), orig=-480_000)
T["gift"] = txn(ROBIN, BANK, D(2026, 9, 21), -1_200_000, "העב' לאחר-נייד", "gifts_donations", "transfer_out",
                memo="לטובת: דנה ויואב. עבור: חתונה.", merchant="דנה ויואב")
T["invest"] = txn(ROBIN, BANK, D(2026, 4, 9), -4_000_000, "העב' לאחר-נייד", "investments", "investment",
                  memo="לטובת: בית ההשקעות הראשון בע\"מ. עבור: השקעה.", merchant="בית ההשקעות הראשון")
# The card bills on the bank: what was charged on each 15th (from December on).
charged: Dict[D, int] = {}
for r in ROWS:
    if r["account"] == CARD and r["charge"] and r["charge"] < TODAY:
        charged[r["charge"]] = charged.get(r["charge"], 0) + r["amount"]
for d, amount in sorted(charged.items()):
    if d >= D(2025, 12, 1):
        txn(ROBIN, BANK, d, amount, "כאל", None, "card_bill")
# Bob.
T["bob_coffee"] = txn(BOB, BOB_CARD, D(2026, 11, 10), -2_500, "קפה רחל", "restaurants", "purchase",
                      charge=D(2026, 12, 2))
txn(BOB, BOB_CARD, D(2026, 10, 10), -500_000, "מקרר שמש", "home", "purchase", charge=D(2026, 11, 2))

BOOK = dashboard.load(ROBIN)


def kind(key: str) -> Optional[str]:
    return BOOK.kinds.get(T[key])


# --- Kinds, recurring, yearly ---------------------------------------------------------------------

def test_kinds_and_detection() -> None:
    check("kinds: the mortgage and Netflix fixed, the insurances and the licence yearly, groceries everyday",
          (kind("mortgage_2026-10"), kind("insurance"), kind("home_2"), kind("licence"))
          == ("fixed", "yearly", "yearly", "yearly")
          and BOOK.kinds[next(r["id"] for r in ROWS if r["desc"] == "NETFLIX.COM")] == "fixed"
          and BOOK.kinds[next(r["id"] for r in ROWS if r["desc"] == "שופרסל דיל")] == "everyday")
    check("kinds: one-offs — the car (₪35,000 is a purchase, not a yearly renewal), the laptop and its refund, "
          "every payment of the furniture plan (its total counts), the gift",
          [kind(k) for k in ("car", "laptop", "laptop_refund", "sofa_1", "sofa_2", "gift")] == ["one_off"] * 6)
    check("kinds: money set aside and income are not spending", kind("invest") is None and kind("salary_2026-10") is None)
    rec = {i["merchant"]: i for i in BOOK.recurring}
    sal = rec.get("משכורת נוגה תעשיות בע\"מ")
    check("recurring: the salary is found though its amount varies (median of the last three), due on the 1st",
          sal and sal["direction"] == "in" and sal["variable"] and sal["amount"] == 2_410_000
          and sal["next_date"] == "2026-12-01", sal)
    el = rec.get("חברת החשמל לישראל")
    check("recurring: electricity every ~2 months on drifting days (53–65 days apart) is bimonthly, due on its usual day",
          el and el["cadence"] == "bimonthly" and el["next_date"] == "2026-11-16" and el["monthly_cost"] == el["amount"] // 2,
          el)
    nf = rec.get("NETFLIX.COM")
    check("recurring: Netflix's price change, the new price typical",
          nf and nf["amount"] == 4_990 and nf["price_change"] == {"from": 3_990, "to": 4_990, "since": "2026-11-05"}, nf)
    check("recurring: the mortgage's latest payments are its price (it drifts down within one run)",
          rec["פועלים-משכנתא"]["amount"] == 750_000, rec["פועלים-משכנתא"]["amount"])
    yearly = {y["merchant"]: y for y in BOOK.yearly}
    check("yearly: the car insurance (next a year on), the home insurance at its total with its 3 payments, the licence",
          set(yearly) == {"ביטוח ישיר", "הראל ביטוח דירה", "משרד התחבורה"}
          and (yearly["ביטוח ישיר"]["amount"], yearly["ביטוח ישיר"]["next_date"]) == (240_000, "2026-12-28")
          and (yearly["הראל ביטוח דירה"]["amount"], yearly["הראל ביטוח דירה"]["payments"]) == (120_000, 3)
          and yearly["משרד התחבורה"]["monthly_cost"] == 130_000 // 12, sorted(yearly))
    # Pure: a merchant under two spellings; the usual day; a yearly pair too close; the cap; marks.
    def r(day: str, amount: int, key: str, **kw: Any) -> Dict[str, Any]:
        return {"id": uuid.uuid4().hex, "purchase_date": day, "amount": amount, "merchant_key": key,
                "merchant": kw.pop("merchant", key), "status": "completed", "occurred_at": None,
                "installment_total": kw.pop("installment_total", None), "installment_number": None,
                "account_kind": "card", "account_company": "samplecard", "account_number": "1", "account_id": "a",
                "category": kw.pop("category", "utilities"), "eff_flow": "purchase", "description": key, **kw}
    water = [r("2026-07-16", -79_000, "מי העיר בעמ", merchant="מי העיר"),
             r("2026-09-10", -33_000, "מי העיר בעמ הק", merchant="מי העיר"),
             r("2026-11-11", -30_000, "מי העיר בעמ הק", merchant="מי העיר")]
    found = insights.detect_recurring(water, TODAY)
    check("recurring: a merchant whose rows come under two spellings is tried under its display name",
          len(found) == 1 and found[0]["cadence"] == "bimonthly"
          and found[0]["merchant_keys"] == ["מי העיר בעמ", "מי העיר בעמ הק"], found)
    salary = [r(d, 1_000_000 + i * 80_000, "pay", category="salary") for i, d in
              enumerate(("2026-07-01", "2026-08-02", "2026-09-01", "2026-10-01", "2026-11-02"))]
    found = insights.detect_recurring([dict(x, amount=-x["amount"]) for x in salary][:0] + salary, TODAY)
    check("recurring: due on its usual day (the 1st), not a month after a late payment on the 2nd",
          found and found[0]["next_date"] == "2026-12-01", found)
    garage = [r("2025-12-01", -90_000, "garage", category="car"), r("2026-05-01", -90_000, "garage", category="car")]
    big = [r("2026-01-01", -2_000_000, "dealer", category="car")]
    marked = [r("2026-02-01", -50_000, "gym", category="health", yearly=1)]
    names = {y["merchant_key"] for y in insights.detect_yearly(garage + big + marked, TODAY)}
    check("yearly: two charges five months apart are not yearly, ₪20,000 is above the cap, a row the user marked is",
          names == {"gym"}, names)


# --- Cash flow and the balance ---------------------------------------------------------------------

def test_cashflow_and_balance() -> None:
    cf = dashboard.cashflow(BOOK, 12)
    oct_ = next(m for m in cf["months"] if m["month"] == "2026-10")
    expect = {"income": SALARY[10], "fixed": (760_000 - 1_000 * 10) + 3_990, "yearly": 0,
              "everyday": EVERYDAY["2026-10"], "one_off": 240_000, "set_aside": 0}
    got = {k: oct_[k] for k in expect}
    check("cashflow: October exactly (salary; mortgage + Netflix fixed; the furniture plan's rows one-off; everyday)",
          got == expect and oct_["spent"] == sum(v for k, v in expect.items() if k not in ("income", "set_aside"))
          and oct_["net"] == oct_["income"] - oct_["spent"] and oct_["complete"], (got, expect))
    apr = next(m for m in cf["months"] if m["month"] == "2026-04")
    check("cashflow: money set aside is apart (not spent)", apr["set_aside"] == 4_000_000
          and apr["spent"] < 1_000_000, apr)
    check("cashflow: 12 months ending this month, the current one not complete; typical = medians of whole months",
          [m["month"] for m in cf["months"]][0] == "2025-12" and not cf["months"][-1]["complete"]
          and cf["typical"]["income"] == sorted(m["income"] for m in cf["months"] if m["complete"])[5]
          and cf["totals"]["set_aside"] == 4_000_000, cf["typical"])
    hist = dashboard.balance_history(BOOK, store.list_accounts(ROBIN))
    by = {p["date"]: p["balance"] for p in hist}
    after = sum(r["amount"] for r in ROWS if r["account"] == BANK and r["day"] > D(2026, 11, 14))
    check("balance: rebuilt backwards from today's balance through the bank's rows",
          hist[-1] == {"date": "2026-11-18", "balance": 2_000_000} and by["2026-11-14"] == 2_000_000 - after
          and by["2025-11-30"] == 2_000_000 - sum(r["amount"] for r in ROWS if r["account"] == BANK), hist[-1])


# --- The forecast ------------------------------------------------------------------------------

def test_forecast() -> None:
    fc = dashboard.forecast(ROBIN, 180, BOOK)
    assert fc is not None
    ev = fc["events"]
    incomes = [e["date"] for e in ev if e["kind"] == "income"]
    check("forecast: the salary on the 1st of each month, a Saturday's on the Sunday (1 May 2027 → 2 May)",
          incomes == ["2026-12-01", "2027-01-01", "2027-02-01", "2027-03-01", "2027-04-01", "2027-05-02"]
          and all(e["amount"] == 2_410_000 for e in ev if e["kind"] == "income"), incomes)
    mort = [e for e in ev if e["kind"] == "fixed"]
    check("forecast: the mortgage with it, at its latest price", [e["date"] for e in mort] == incomes
          and all(e["amount"] == -750_000 for e in mort), mort[:1])
    bills = {e["date"]: e for e in ev if e["kind"] == "card_bill"}
    dec = bills.get("2026-12-15")
    known = sum(-r["amount"] for r in ROWS if r["account"] == CARD and r["charge"] == D(2026, 12, 15))
    rate = fc["rates"]["card"]["typical"]
    whole = sorted(EVERYDAY[k] for k in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"))
    check("forecast: the everyday rate = the mean of the last 6 whole months without the busiest and the quietest; "
          "the band = the quietest and the busiest",
          rate == round(sum(whole[1:-1]) / 4) and fc["rates"]["card"]["low"] == whole[0]
          and fc["rates"]["card"]["high"] == whole[-1], (rate, whole))
    days_dec = sum(1 for i in range(0, 40) if bill(TODAY + datetime.timedelta(days=i)) == D(2026, 12, 15))
    check("forecast: the 15 Dec bill = what is on it already (the card company's figure or the rows, the larger) + "
          "electricity due + everyday spending until the cycle closes",
          dec and dec["parts"]["known"] == max(known, 150_000) and dec["parts"]["installments"] == 0
          and any(i["label"] == "חברת החשמל לישראל" for i in dec["items"])
          and dec["parts"]["everyday"] == round(rate * 12 / 365.25 * days_dec) and dec["estimated"], dec)
    jan, feb, mar = bills.get("2027-01-15"), bills.get("2027-02-15"), bills.get("2027-03-15")
    check("forecast: the furniture plan's remaining payments on the next two bills; Netflix each month",
          jan["parts"]["installments"] == 120_000 and feb["parts"]["installments"] == 120_000
          and mar["parts"]["installments"] == 0
          and all(any(i["label"] == "NETFLIX.COM" and i["amount"] == 4_990 for i in b["items"]) for b in (jan, feb, mar)),
          (jan["parts"], feb["parts"]))
    check("forecast: the yearly car insurance (28 Dec) on the 15 Jan bill; the home insurance in its 3 payments "
          "from March (15 May 2027 is a Saturday: the bill on the 16th)",
          jan["parts"]["yearly"] == 240_000 and mar["parts"]["yearly"] == 40_000
          and bills["2027-04-15"]["parts"]["yearly"] == 40_000 and bills.get("2027-05-16", {}).get("parts", {}).get("yearly") == 40_000,
          sorted(bills))
    labels = {e["label"] for e in ev} | {i["label"] for e in ev for i in e.get("items", [])}
    check("forecast: one-offs are never projected (the car, the laptop, the gift); money set aside neither",
          not labels & {"מוטורס הצפון", "KSP מחשבים", "דנה ויואב", "בית ההשקעות הראשון", "משרד התחבורה"},
          labels)
    s = fc["series"]
    total = sum(e["amount"] for e in ev)
    check("forecast: the daily series from today's balance, every event added, the band around it",
          s[0]["date"] == "2026-11-18" and s[0]["balance"] == 2_000_000 and s[-1]["date"] == fc["end"] == "2027-05-17"
          and s[-1]["balance"] == 2_000_000 + total and all(p["low"] <= p["balance"] <= p["high"] for p in s)
          and s[-1]["low"] < s[-1]["balance"] < s[-1]["high"], (s[-1], total))
    low = min(s[1:], key=lambda p: p["balance"])
    soon = min(s[1:46], key=lambda p: p["balance"])
    check("forecast: the low points (after today; in the next 45 days)",
          fc["low_point"] == {"date": low["date"], "balance": low["balance"]}
          and fc["soon_low"] == {"date": soon["date"], "balance": soon["balance"], "days": 45})
    check("forecast: plans, the yearly charges ahead and the one-offs it left out",
          [(p["merchant"], p["left"], p["dates"]) for p in fc["plans"]] == [("רהיטי הבית", 240_000,
                                                                             ["2027-01-15", "2027-02-15"])]
          and [y["merchant"] for y in fc["yearly"]] == ["ביטוח ישיר", "הראל ביטוח דירה"]
          and fc["left_out_total"] == 3_100_000 + 400_000 - 50_000 + 240_000 + 1_200_000
          and fc["left_out"][0]["merchant"] == "מוטורס הצפון", (fc["plans"], fc["left_out_total"]))
    check("forecast: a past day is a Saturday → Sunday shift only (Friday stays)",
          dashboard.bank_day(D(2027, 5, 1)) == D(2027, 5, 2) and dashboard.bank_day(D(2027, 4, 30)) == D(2027, 4, 30))
    check("forecast: a card purchase goes on the first bill at least 2 days later",
          (bill(D(2026, 11, 13)), bill(D(2026, 11, 14)), bill(D(2026, 12, 31))) == (D(2026, 11, 15), D(2026, 12, 15),
                                                                                  D(2027, 1, 15)))
    check("forecast: none without a bank balance", dashboard.forecast(BOB) is None)


# --- Trends, merchants, pace, insights -------------------------------------------------------------------

def test_trends_and_insights() -> None:
    t = dashboard.trends(BOOK, "2026-10")
    food = next(g for g in t["groups"] if g["key"] == "food")
    gro = next(c for c in food["categories"] if c["key"] == "groceries")
    whole = ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
    gro_months = sorted(sum(-r["amount"] for r in ROWS if r["desc"] in ("שופרסל דיל", "מאפה נאמן")
                            and f"{r['day']:%Y-%m}" == m) for m in whole)
    check("trends: a past month against the median of the 6 whole months before, no 'on track'",
          not t["in_progress"] and gro["typical"] == round((gro_months[2] + gro_months[3]) / 2)
          and gro["on_track"] is None and gro["total"] == gro["months"][-1], gro)
    home = next((g for g in t["groups"] if g["key"] == "housing"), None)
    check("trends: one-offs are left out (the furniture plan) and listed apart",
          all(c["key"] != "home" or c["total"] == 0 for c in (home or {}).get("categories", []))
          and {o["merchant"] for o in t["one_offs"]} == {"רהיטי הבית"}, t["one_offs"])
    now = dashboard.trends(BOOK)
    e = now["everyday"]
    typical = sorted(EVERYDAY[m] for m in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"))
    typical_e = round((typical[2] + typical[3]) / 2)
    check("trends: this month's everyday on track = so far + the typical share of the days left",
          now["in_progress"] and e["total"] == EVERYDAY["2026-11"] and e["typical"] == typical_e
          and e["on_track"] == round(EVERYDAY["2026-11"] + typical_e * 12 / 30), e)
    check("trends: no mover is a drop while the month runs", all(m["direction"] != "down" for m in now["movers"]))
    ins = dashboard.trends(BOOK, "2025-12")
    check("trends: the yearly insurance is not a mover in its month",
          all(m["key"] != "insurance" for m in ins["movers"]), ins["movers"])
    sep = dashboard.trends(BOOK, "2026-09")
    util = next(c for g in sep["groups"] for c in g["categories"] if c["key"] == "utilities")
    check("trends: a bimonthly bill is in its category's total but never a mover (it doubles every other month)",
          util["total"] == 97_500 and all(m["key"] != "utilities" for m in sep["movers"]), sep["movers"])
    top = dashboard.merchants(BOOK, D(2026, 10, 1), D(2026, 11, 1))
    by = {m["merchant"]: m for m in top}
    check("merchants: October's top merchants with count, average, the last 12 months and one-off flags",
          by["פועלים-משכנתא"]["count"] == 1 and by["רהיטי הבית"]["one_off"] and by["רהיטי הבית"]["total"] == 240_000
          and by["שופרסל דיל"]["count"] == sum(1 for r in ROWS if r["desc"] == "שופרסל דיל" and f"{r['day']:%Y-%m}" == "2026-10")
          and len(by["שופרסל דיל"]["months"]) == 12 and top[0]["total"] >= top[-1]["total"], list(by))
    pace = dashboard.everyday_pace(BOOK)
    by_30 = sorted(sum(-r["amount"] for r in ROWS if r["desc"] in ("שופרסל דיל", "קפה ביאליק", "מאפה נאמן")
                       and f"{r['day']:%Y-%m}" == m and r["day"].day <= 30)
                   for m in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"))
    check("pace: everyday spending day by day this month (nothing after today), typical (the median by day of the "
          "month) with its band",
          pace["today"] == 18 and pace["this_month"][17] == EVERYDAY["2026-11"] and pace["this_month"][18] is None
          and pace["months"] == 6 and pace["typical"][-1] == round((by_30[2] + by_30[3]) / 2)
          and all(lo <= ty <= hi for lo, ty, hi in zip(pace["low"], pace["typical"], pace["high"])),
          (pace["this_month"][17], EVERYDAY["2026-11"], pace["typical"][-1], typical_e, pace["months"]))
    fc = dashboard.forecast(ROBIN, 180, BOOK)
    items = dashboard.insights_for(BOOK, None, fc, [])
    kinds = [i["kind"] for i in items]
    check("insights: this month — everyday pace, the balance ahead, Netflix's price change, the insurance renewing "
          "soon, the plan left",
          {"everyday_pace", "low_point", "price_change", "yearly_soon", "installments"} <= set(kinds)
          and all(i["tone"] in ("good", "bad", "warn", "info") and i["title"] and i["detail"] for i in items), kinds)
    pc = next(i for i in items if i["kind"] == "price_change")
    check("insights: the price change in words with its yearly cost",
          pc["title"] == "NETFLIX.COM up to ₪49.90" and "₪120.00 a year more" in pc["detail"], pc)
    renewal = queries.transaction(ROBIN, T["insurance"])
    unusual = [{"reason": "first_time", "title": "renewal", "why": "the first charge from them", "transaction": renewal},
               {"reason": "duplicate", "title": "twice", "why": "charged twice within 5 minutes", "transaction": renewal}]
    dec = [i["title"] for i in dashboard.insights_for(BOOK, "2025-12", None, unusual) if i["kind"] == "unusual"]
    check("insights: a yearly renewal is never 'unusual' for being new, only for a double charge", dec == ["twice"], dec)
    past = dashboard.insights_for(BOOK, "2026-09", None, [])
    net = next(i for i in past if i["kind"] == "net")
    check("insights: a past month's net names its one-offs; its biggest one-off",
          "of it one-offs" in net["detail"] and any(i["kind"] == "one_off" and "דנה ויואב" in i["title"] for i in past),
          [i["title"] for i in past])


# --- The API ------------------------------------------------------------------------------------

async def call(client: TestClient, method: str, path: str, who, body: Any = None) -> Tuple[int, Any]:
    resp = await client.request(method, path, json=body, headers={"Authorization": f"Bearer {TOKENS[who.id]}"})
    text = await resp.text()
    try:
        return resp.status, json.loads(text)
    except ValueError:
        return resp.status, text


async def test_api(client: TestClient) -> None:
    paths = ["/api/money/cashflow?n=6", "/api/money/balance?days=90", "/api/money/trends",
             "/api/money/merchants?period=this_month", "/api/money/insights", "/api/money/pace"]
    got = {p: await call(client, "GET", p, ROBIN) for p in paths}
    check("api: every dashboard endpoint answers Robin", all(s == 200 for s, _ in got.values()),
          {p: s for p, (s, _) in got.items()})
    check("api: payloads", len(got[paths[0]][1]["months"]) == 6 and got[paths[1]][1]["forecast"]["as_of"] == "2026-11-18"
          and got[paths[1]][1]["history"] and got[paths[4]][1]["items"], got[paths[1]][1].keys())
    s, ov = await call(client, "GET", "/api/money/overview", ROBIN)
    check("api: the overview's tiles (everyday, net, fixed costs a month with the yearly share, the forecast)",
          s == 200 and ov["everyday"]["on_track"] is not None and ov["forecast"]["soon_low"]["days"] == 45
          and ov["fixed_monthly"] > ov["yearly_monthly"] > 0 and "net" in ov, {k: ov.get(k) for k in ("net", "fixed_monthly")})
    s, past = await call(client, "GET", "/api/money/overview?month=2026-10", ROBIN)
    check("api: no forecast for a past month", s == 200 and past["forecast"] is None)
    s, bob = await call(client, "GET", "/api/money/merchants?period=last_30_days", BOB)
    check("api: Bob sees his own merchants only", s == 200 and [m["merchant"] for m in bob["merchants"]] == ["קפה רחל"],
          bob)
    s, bob_bal = await call(client, "GET", "/api/money/balance", BOB)
    check("api: Bob without a bank balance: no forecast, no history", s == 200 and bob_bal == {"history": [], "forecast": None})
    statuses = [(await call(client, "GET", p, CARL))[0] for p in paths]
    check("api: no money → 403 not_connected on every dashboard endpoint", statuses == [403] * len(paths), statuses)
    bad = [(await call(client, "GET", p, ROBIN))[0] for p in ("/api/money/trends?month=2026-13", "/api/money/insights?month=nov",
                                                              "/api/money/cashflow?end=2026", "/api/money/merchants?period=someday")]
    check("api: a bad month / end / period → 400", bad == [400, 400, 400, 400], bad)
    s, lst = await call(client, "GET", "/api/money/transactions?period=2026-10&limit=200", ROBIN)
    kinds = {t["id"]: t["kind"] for t in lst["transactions"]}
    check("api: listed transactions carry their kind", kinds.get(T["sofa_1"]) == "one_off"
          and kinds.get(T["mortgage_2026-10"]) == "fixed", None)
    s, detail = await call(client, "GET", f"/api/money/transactions/{T['laptop']}", ROBIN)
    check("api: the detail has its kind and the user's marks (none yet)",
          s == 200 and (detail["kind"], detail["one_off"], detail["yearly"]) == ("one_off", None, None), detail.get("kind"))


async def test_marks(client: TestClient) -> None:
    sep = lambda: next(m for m in dashboard.cashflow(dashboard.load(ROBIN), 12)["months"] if m["month"] == "2026-09")  # noqa: E731
    before = sep()
    s, p = await call(client, "PATCH", f"/api/money/transactions/{T['gift']}", ROBIN, {"flow": "investment"})
    after = sep()
    check("marks: counts as money set aside → out of spending, into set aside; the row is the user's",
          s == 200 and p["transaction"]["flow"] == "investment" and p["transaction"]["kind"] is None
          and after["set_aside"] == before["set_aside"] + 1_200_000 and after["spent"] == before["spent"] - 1_200_000
          and p["transaction"]["category_source"] == "user", (before, after))
    s, _ = await call(client, "POST", f"/api/undo/{p['undo_id']}", BOB)
    check("marks: another user can't use the undo", s == 404)
    s, _ = await call(client, "POST", f"/api/undo/{p['undo_id']}", ROBIN)
    check("marks: the undo restores the flow and the category source",
          s == 200 and sep() == before and store.get_transaction(ROBIN, T["gift"])["flow"] == "transfer_out")
    s, p = await call(client, "PATCH", f"/api/money/transactions/{T['laptop']}", ROBIN, {"one_off": False})
    check("marks: not a one-off → everyday", s == 200 and p["transaction"]["one_off"] is False
          and p["transaction"]["kind"] == "everyday", p["transaction"]["kind"])
    s, back = await call(client, "PATCH", f"/api/money/transactions/{T['laptop']}", ROBIN, {"one_off": None})
    check("marks: null gives it back to Dana's call", s == 200 and back["transaction"]["one_off"] is None
          and back["transaction"]["kind"] == "one_off")
    s, p = await call(client, "PATCH", f"/api/money/transactions/{T['sofa_2']}", ROBIN, {"yearly": True, "note": "every year?"})
    with store.db() as conn:
        marks = [r["yearly"] for r in conn.execute("SELECT yearly FROM money_transactions WHERE id IN (?, ?)",
                                                   (T["sofa_1"], T["sofa_2"]))]
    check("marks: yearly on one payment of a plan marks every payment; with a note → one undo",
          s == 200 and marks == [1, 1] and p["changed"] == 2 and p["transaction"]["kind"] == "yearly"
          and any(y["merchant"] == "רהיטי הבית" and y["marked"] for y in dashboard.load(ROBIN).yearly), (marks, p["changed"]))
    s, _ = await call(client, "POST", f"/api/undo/{p['undo_id']}", ROBIN)
    with store.db() as conn:
        marks = [r["yearly"] for r in conn.execute("SELECT yearly FROM money_transactions WHERE id IN (?, ?)",
                                                   (T["sofa_1"], T["sofa_2"]))]
    check("marks: that undo clears both marks and the note", s == 200 and marks == [None, None]
          and store.get_transaction(ROBIN, T["sofa_2"])["note"] is None, marks)
    bad = [(await call(client, "PATCH", f"/api/money/transactions/{T['laptop']}", ROBIN, body))[0]
           for body in ({"flow": "spending"}, {"one_off": "yes"}, {"yearly": 1}, {})]
    check("marks: an unknown flow, a non-boolean mark, nothing to change → 400", bad == [400, 400, 400, 400], bad)
    s, _ = await call(client, "PATCH", f"/api/money/transactions/{T['laptop']}", BOB, {"one_off": False})
    s2, _ = await call(client, "PATCH", f"/api/money/transactions/{T['bob_coffee']}", CARL, {"one_off": False})
    check("marks: another user's transaction → 404; no money → 403", (s, s2) == (404, 403), (s, s2))


async def main() -> None:
    test_kinds_and_detection()
    test_cashflow_and_balance()
    test_forecast()
    test_trends_and_insights()
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    actions_api.setup(app)
    async with TestClient(TestServer(app)) as client:
        await test_api(client)
        await test_marks(client)


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
