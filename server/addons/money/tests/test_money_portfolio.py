"""Investment accounts (Psagot Trade): the sync with its device key, the portfolio ingest, the API, the tool and the web fixture.
Run: bin/test test_money_portfolio

``testfakes.FakeBankWorker`` plays the bank-worker with a made-up Psagot result
in the worker's real shape (bank-worker/README.md, "Psagot"): every security,
amount and name is invented. Robin (the first admin) and Bob have ``money``;
Carl has not. Checked: the company and its SMS code request (sender and
pattern the phone reads, the code reaching the job); the device key the job
registers kept only encrypted and passed to the next job, dropped when the
site refused it; the account (kind ``investment``, its number masked, its
value and summary), holdings (agorot, price from value over quantity, cost
from value minus profit, HTML entities and ID numbers out of names), daily
values with today's, activity kinds and no duplicates on the next sync, a
sold holding gone; no transactions and the spending dashboards untouched;
``/api/money/portfolio`` and the overview's investments per user both ways
(403 ``not_connected`` for Carl); the ``investment_portfolio`` tool; the pure
helpers (action kinds, dates); disconnecting deletes the portfolio; and the
payload fixture tests/fixtures/money_portfolio_web.json (written with
``WRITE_WEB_FIXTURE=1``, else checked for shape drift).
"""

import asyncio
import datetime
import json
import os
import sys
from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("money_portfolio_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-portfolio-test-env-token"  # a test value: with it set, DB tokens are honoured
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

from core import clock  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import Connection, crypto, store as connections  # noqa: E402
from addons.money import api as money_api, config, dashboard, portfolio, store, sync, tools  # noqa: E402
from addons.phone import push  # noqa: E402
from testfakes import FakeBankWorker  # noqa: E402

WEB_FIXTURE = os.path.join(testenv.FIXTURES, "money_portfolio_web.json")
results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
WORKER = FakeBankWorker(config.token_file())
CODE = "482913"
SECRETS = ["robin.trade.user", "pw-trade-ROBIN", "bob.trade.user", "pw-trade-BOB"]
DEVICE = {"user": "robin.trade.user", "udid": "0123456789abcdef0123456789abcdef",
          "key": "-----BEGIN PRIVATE KEY-----\nMADE-UP-KEY-FOR-THE-TEST\n-----END PRIVATE KEY-----\n"}

TODAY = clock.now().date()


def iso_day(days_ago: int) -> str:
    """A daily value's date as Psagot writes it (local midnight, with its offset)."""
    return (TODAY - datetime.timedelta(days=days_ago)).isoformat() + "T00:00:00.0000000+02:00"


def dmy(days_ago: int) -> str:
    """An activity date as Psagot writes it: dd/mm/yyyy."""
    return (TODAY - datetime.timedelta(days=days_ago)).strftime("%d/%m/%Y")


CASH = 'עו"ש מט"י'


def psagot_account(*, number: str = "150-123456", sold: bool = False, value: float = 61234.56) -> Dict[str, Any]:
    """A made-up Psagot account in the worker's shape (README "Psagot Trade"), as Psagot really answers:
    positions without names (``Key`` null) and names from the market table (``names``: Hebrew for one,
    only English for another, none for the third, which takes its activity's name); the activity with
    security numbers padded to 8 digits, ``dd/mm/yyyy`` dates, negative commissions, a deposit, and the
    cash account's side of a buy twice (its whole amount in the tax field); daily values newest first."""
    holdings = [
        {"EquityNumber": "2001234", "Key": None, "OnlineNV": 120, "OnlineVL": 30000.0, "OnlineNisVL": 30000.0,
         "BaseRate": 24800, "LastRate": 25000, "CurrencyCode": "ILS", "AveragePrice": 21500,
         "AveragePriceProfitLossNis": 4200.0, "AveragePriceProfitLossPercentage": 16.28, "OnlinePercentage": 48.99,
         "MorningNV": 120, "Source": "TA"},
        {"EquityNumber": "5551234", "Key": None, "OnlineNV": 15, "OnlineVL": 2250.0, "OnlineNisVL": 8325.0,
         "CurrencyCode": "USD", "AveragePriceProfitLossNis": -312.5, "AveragePriceProfitLossPercentage": -3.62,
         "OnlinePercentage": 13.6},
    ]
    if not sold:
        holdings.append({"EquityNumber": "1100007", "Key": None, "OnlineNV": 20000, "OnlineVL": 21909.56,
                         "OnlineNisVL": 21909.56, "CurrencyCode": "ILS", "AveragePriceProfitLossNis": 109.56,
                         "AveragePriceProfitLossPercentage": 0.5, "OnlinePercentage": 35.78})
    return {
        "kind": "investment", "accountNumber": number, "nickname": "ראשי", "balance": value,
        "summary": {"OnlineValue": value, "MorningValue": value - 412.3, "OnlineCash": 1000.0, "CurrencyCode": "ILS",
                    "AveragePriceNisProfitLoss": 3997.06, "AveragePriceNisProfitLossPercentage": 6.98,
                    "OnlineCashByCurrency": [{"Code": "ILS", "Value": 1000.0}, {"Code": "USD", "Value": 0}]},
        "holdings": holdings,
        "names": {"2001234": {"heb": "קרן מחקה &quot;דוגמה&quot; 500", "eng": "", "symbol": ""},
                  "5551234": {"heb": "", "eng": "Example Corp", "symbol": "EXC"}},
        "activity": [
            {"ActionDate": dmy(60), "Action": "קניה בבורסה", "EquityNumber": "01100007",
             "Name": "אג\"ח מדינה לדוגמה 123456782", "NV": 20000, "Price": 109, "VL": -21800.0, "NisVL": -21800.0,
             "Commission": -15.0, "TaxVL": 0, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(30), "Action": "התקבל מח.בסניף", "EquityNumber": "99400001", "Name": CASH,
             "NV": 20000, "Price": 0, "VL": 20000, "NisVL": 20000, "Commission": 0, "TaxVL": 0, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(20), "Action": "קניה בבורסה", "EquityNumber": "02001234", "Name": "קרן מחקה דוגמה 500",
             "NV": 20, "Price": 24800, "VL": -4960.0, "NisVL": -4960.0, "Commission": -7.5, "TaxVL": 0, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(20), "Action": "נע-קניה200001", "EquityNumber": "99700001", "Name": CASH,
             "NV": 4967.5, "Price": 0, "VL": -4967.5, "NisVL": -4967.5, "Commission": 0, "TaxVL": 4967.5, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(19), "Action": "נע-קניה200001", "EquityNumber": "99400001", "Name": CASH,
             "NV": 4967.5, "Price": 0, "VL": -4967.5, "NisVL": -4967.5, "Commission": 0, "TaxVL": 4967.5, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(9), "Action": "דיבידנד", "EquityNumber": "05551234", "Name": "Example Corp", "NV": 15,
             "Price": 0.24, "VL": 3.6, "NisVL": 13.32, "TaxVL": 3.33, "CurrencyCode": "USD"},
            {"ActionDate": dmy(9), "Action": "חיוב מס", "EquityNumber": "99400001", "Name": CASH, "VL": -3.33,
             "NisVL": -3.33, "CurrencyCode": "ILS"},
            {"ActionDate": dmy(2), "Action": "פעולה חדשה כלשהי", "EquityNumber": "99400001", "Name": CASH, "NisVL": 0},
            {"Action": "קניה בבורסה", "NV": 1, "NisVL": -1},  # no date: skipped
        ],
        "history": [{"Date": iso_day(d), "Value": value - 50 * d, "Yield": 0} for d in (1, 2, 3, 30, 40)],
        "txns": [],
    }


async def fake_push(user, type_: str, data: Dict[str, Any], replace: bool = False) -> bool:
    return True


async def fake_notice(user, title: str, text: str, tag: str = "", channel: str = "messages") -> bool:
    return True


async def until(cond: Callable[[], Any], timeout: float = 8.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return bool(cond())


async def call(client: TestClient, method: str, path: str, who, body: Any = None) -> Tuple[int, Any]:
    r = await client.request(method, path, headers={"Authorization": f"Bearer {TOKENS[who.id]}"},
                             json=body if body is not None else None)
    text = await r.text()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def rows(sql: str, *args: Any) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def source_row(source_id: str) -> Dict[str, Any]:
    found = rows("SELECT * FROM money_sources WHERE id = ?", source_id)
    return found[0] if found else {}


async def idle(source_id: str) -> bool:
    return await until(lambda: sync.running(source_id) is None)


def test_pure() -> None:
    check("action kinds", [portfolio.action_kind(a) for a in (
        "קניה בבורסה", "מכירה בבורסה", "קנ. מחוץ לבורסה-המ", "דיבידנד", "בטול דיבידנד", "קיעה דיבידנד", "זכוי ריבית זמני",
        "התקבל מח.בסניף", "הפקדה", "משיכה לחשבון", "חיוב מס", "אחוד/פצול הון מקבל", "פידיון", "תשלום הוני",
        "הטבה באחר,מתקבל", "פקיעה לקונה", "משהו אחר", None)]
        == ["buy", "sell", "buy", "dividend", "dividend", "dividend", "interest", "deposit", "deposit", "withdrawal",
            "tax", "split", "redemption", "redemption", "benefit", "expiry", "other", "other"])
    check("security numbers lose their padding", [portfolio.security_id(v) for v in ("01100007", 1100007, " 99400001 ", "0", "", None)]
          == ["1100007", "1100007", "99400001", "0", None, None])
    check("the account's cash is told apart from securities", portfolio.is_cash_account("99400001", None)
          and portfolio.is_cash_account(None, 'עו"ש מט"י') and portfolio.is_cash_account("1", "עו''ש")
          and not portfolio.is_cash_account("1100007", "קרן") and not portfolio.is_cash_account("9940900", None))
    check("the cash side of a trade is not activity", portfolio.is_cash_leg({"Action": "נע-קניה110000"})
          and not portfolio.is_cash_leg({"Action": "קניה בבורסה"}) and not portfolio.is_cash_leg({}))
    check("a name from the market table: Hebrew, else English, else the symbol; none for others",
          portfolio.security_name({"1": {"heb": "א", "eng": "A"}, "2": {"heb": " ", "eng": "B"}, "3": {"symbol": "C"}}, "1") == "א"
          and portfolio.security_name({"2": {"heb": " ", "eng": "B"}}, "2") == "B"
          and portfolio.security_name({"3": {"symbol": "C"}}, "3") == "C" and portfolio.security_name({}, "4") is None
          and portfolio.security_name({"5": "junk"}, "5") is None)
    check("dates: ISO, d/m/Y, ddMMyyyy; junk is none",
          [portfolio.parse_date(v) for v in ("2026-11-17T00:00:00", "17/11/2026", "17112026", "", "soon", None)]
          == [datetime.date(2026, 11, 17)] * 3 + [None] * 3)
    h = portfolio.holding_row({"EquityNumber": "0001", "OnlineNV": 4, "OnlineVL": 10.0, "CurrencyCode": "ILS"})
    check("a holding without a shekel value uses its own value when it is in shekels",
          h["value"] == 1000 and h["price"] == 2.5 and h["profit"] is None and h["cost"] is None and h["security"] == "1", h)
    check("a holding without a security number is skipped", portfolio.holding_row({"OnlineNV": 1}) is None)


async def test_first_sync(client: TestClient) -> str:
    WORKER.script("*", accounts=[psagot_account()], code=CODE, device=DEVICE)
    status, body = await call(client, "POST", "/api/money/sources", ROBIN,
                              {"company": "psagot", "credentials": {"username": "robin.trade.user",
                                                                   "password": "pw-trade-ROBIN"}})
    check("add: Psagot Trade is accepted", status == 201 and body["company"] == "psagot", (status, body))
    sid = body["id"]
    await until(lambda: any(r["status"] == "pending" for r in rows(
        "SELECT * FROM money_code_requests WHERE source_id = ?", sid)))
    request = rows("SELECT * FROM money_code_requests WHERE source_id = ? AND status = 'pending'", sid)
    check("the sign-in asks for the SMS code", len(request) == 1)
    status, req = await call(client, "GET", f"/api/money/code-requests/{request[0]['id']}", ROBIN)
    check("the phone reads the Psagot sender and pattern", status == 200 and req.get("senders") == ["Psagot"]
          and "פסגות טרייד הוא" in req.get("code_regex", ""), req)
    status, _ = await call(client, "POST", f"/api/money/code-requests/{request[0]['id']}", ROBIN, {"code": CODE})
    check("the code is handed over", status in (200, 202), status)
    await idle(sid)
    started = WORKER.started[-1]
    check("the job: psagot, the credentials, no device yet", started["company"] == "psagot"
          and started["credentials"] == {"username": "robin.trade.user", "password": "pw-trade-ROBIN"}
          and started["device"] is None, started)
    check("the code reached the job", (started["id"], CODE) in WORKER.codes)
    src = source_row(sid)
    check("the sync ends ok", src["state"] == "ok", src["state"])
    check("the device key is kept, only encrypted", src["device"] and DEVICE["key"] not in src["device"]
          and "MADE-UP-KEY" not in src["device"] and crypto.decrypt(src["device"]) == DEVICE)
    return sid


def test_ingest(sid: str) -> None:
    accounts = rows("SELECT * FROM money_accounts WHERE source_id = ?", sid)
    check("one investment account", len(accounts) == 1 and accounts[0]["kind"] == "investment"
          and accounts[0]["balance"] == 6123456, accounts)
    a = accounts[0]
    details = json.loads(a["details"])
    check("its summary: cash, today's change, profit", details["cash"] == 100000 and details["day_change"] == 41230
          and details["profit"] == 399706 and details["profit_pct"] == 6.98 and details["holdings_value"] == 6023456,
          details)
    check("the payload masks its number", store.account_payload(a)["number"] == "••••456"
          and store.account_label(a["company"], a["kind"], a["number"]) == "Psagot Trade ••••456")
    held = {h["security"]: h for h in rows("SELECT * FROM money_holdings WHERE account_id = ?", a["id"])}
    check("three holdings", set(held) == {"2001234", "5551234", "1100007"}, list(held))
    etf, usd, bond = held["2001234"], held["5551234"], held["1100007"]
    check("a holding: value, price from value / quantity, cost = value - profit",
          etf["value"] == 3000000 and etf["price"] == 250.0 and etf["profit"] == 420000 and etf["cost"] == 2580000
          and etf["quantity"] == 120 and etf["weight"] == 48.99 and etf["profit_pct"] == 16.28, etf)
    check("a foreign holding: its value in shekels, its price in its currency",
          usd["value"] == 832500 and usd["price"] == 150.0 and usd["currency"] == "USD" and usd["profit"] == -31250, usd)
    check("names from the market table, HTML entities decoded; English when there is no Hebrew",
          etf["name"] == 'קרן מחקה "דוגמה" 500' and usd["name"] == "Example Corp", (etf["name"], usd["name"]))
    check("a holding the market table did not name takes its activity's name (padded number matched)",
          bond["name"] and bond["name"].startswith('אג"ח מדינה לדוגמה'), bond["name"])
    check("names and raw rows: ID numbers scrubbed", "123456782" not in (bond["name"] or "") + (bond["raw"] or "")
          and "[id]" in bond["name"], bond["name"])
    values = {r["date"]: r["value"] for r in rows("SELECT * FROM money_account_values WHERE account_id = ?", a["id"])}
    check("daily values, today's from the summary", values.get(TODAY.isoformat()) == 6123456
          and values.get((TODAY - datetime.timedelta(days=40)).isoformat()) == 6123456 - 200000 and len(values) == 6,
          values)
    trades = rows("SELECT * FROM money_trades WHERE account_id = ? ORDER BY date, kind", a["id"])
    check("activity: dated rows only, the cash side of the buy left out, with kinds",
          [t["kind"] for t in trades] == ["buy", "deposit", "buy", "dividend", "tax", "other"]
          and not [t for t in trades if (t["action"] or "").startswith("נע-")], [t["kind"] for t in trades])
    buy = next(t for t in trades if t["security"] == "2001234")
    check("a buy: amount and commission in agorot, quantity, the site's price, the number unpadded",
          buy["amount"] == -496000 and buy["commission"] == -750 and buy["quantity"] == 20 and buy["price"] == 24800
          and buy["action"] == "קניה בבורסה", buy)
    deposit = next(t for t in trades if t["kind"] == "deposit")
    check("a deposit: money into the account; a cash row names no security", deposit["amount"] == 2000000
          and deposit["security"] is None and deposit["name"] is None
          and all(t["name"] != CASH for t in trades), deposit)
    check("no transactions from an investment account",
          not rows("SELECT id FROM money_transactions WHERE account_id = ?", a["id"]))
    tiles = dashboard.tiles(ROBIN)
    check("the spending dashboards ignore it (no bank balance, no forecast)", tiles["forecast"] is None, tiles)


async def test_next_syncs(client: TestClient, sid: str) -> None:
    WORKER.script("*", accounts=[psagot_account(sold=True, value=39500.0)])
    status, _ = await call(client, "POST", f"/api/money/sources/{sid}/sync", ROBIN)
    check("sync now starts", status == 202, status)
    await idle(sid)
    started = WORKER.started[-1]
    check("the next job carries the device key", started["device"] == DEVICE, started.get("device"))
    src = source_row(sid)
    check("…and the key stays", src["state"] == "ok" and crypto.decrypt(src["device"]) == DEVICE)
    a = rows("SELECT * FROM money_accounts WHERE source_id = ?", sid)[0]
    held = {h["security"] for h in rows("SELECT * FROM money_holdings WHERE account_id = ?", a["id"])}
    check("a sold holding is gone; the value updated", held == {"2001234", "5551234"} and a["balance"] == 3950000,
          (held, a["balance"]))
    check("activity is not duplicated", len(rows("SELECT id FROM money_trades WHERE account_id = ?", a["id"])) == 6)
    check("still one value for today", rows("SELECT value FROM money_account_values WHERE account_id = ? AND date = ?",
                                            a["id"], TODAY.isoformat()) == [{"value": 3950000}])
    # The site refused the key: forgotten (the worker signed in with the password in the same job).
    WORKER.script("*", accounts=[psagot_account(sold=True, value=39500.0)], device_dropped=True)
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET last_manual_at = NULL WHERE id = ?", (sid,))
        conn.commit()
    status, _ = await call(client, "POST", f"/api/money/sources/{sid}/sync", ROBIN)
    await idle(sid)
    check("a refused device key is forgotten", status == 202 and source_row(sid)["device"] is None,
          (status, source_row(sid)["device"]))


async def test_api(client: TestClient, captured: Dict[str, Any]) -> None:
    WORKER.script("*", accounts=[psagot_account(number="150-777888", value=1500.0)])
    status, body = await call(client, "POST", "/api/money/sources", BOB,
                              {"company": "psagot", "credentials": {"username": "bob.trade.user", "password": "pw-trade-BOB"}})
    await idle(body["id"])
    status, robin = await call(client, "GET", "/api/money/portfolio", ROBIN)
    captured["portfolio"] = {"status": status, "body": robin}
    check("portfolio: Robin's account and holdings", status == 200 and len(robin["accounts"]) == 1
          and robin["accounts"][0]["number"] == "••••456" and len(robin["accounts"][0]["holdings"]) == 2
          and robin["total"]["value"] == 3950000 and robin["total"]["cash"] == 100000, robin.get("total"))
    check("portfolio: holdings largest first", [h["value"] for h in robin["accounts"][0]["holdings"]] == [3000000, 832500])
    check("portfolio: history and the total per day", robin["history"] and robin["history"][-1]["date"] == TODAY.isoformat()
          and robin["history"][-1]["value"] == 3950000)
    check("portfolio: activity newest first", [t["kind"] for t in robin["trades"]][:1] == ["other"]
          and robin["trades"][-1]["kind"] == "buy" and len(robin["trades"]) == 6)
    check("portfolio: money put in or taken out, for the value chart", robin["flows"] == [
        {"date": (TODAY - datetime.timedelta(days=30)).isoformat(), "amount": 2000000}], robin.get("flows"))
    check("portfolio: allocation shares add up with cash", robin["allocation"]
          and abs(sum(x["share"] for x in robin["allocation"]) - (3000000 + 832500 + 100000) / 3950000) < 1e-6,
          robin["allocation"])
    check("portfolio: no raw site rows", "raw" not in json.dumps(robin) and "AveragePrice" not in json.dumps(robin))
    status, bob = await call(client, "GET", "/api/money/portfolio", BOB)
    check("portfolio: Bob sees only his", status == 200 and [a["number"] for a in bob["accounts"]] == ["••••888"]
          and bob["total"]["value"] == 150000)
    status, body = await call(client, "GET", "/api/money/portfolio", CARL)
    check("portfolio: Carl without money → 403 not_connected", status == 403
          and body == {"error": "not_connected", "addon": "money"}, (status, body))
    status, overview = await call(client, "GET", "/api/money/overview", ROBIN)
    captured["overview"] = {"status": status, "body": overview}
    inv = overview.get("investments") or []
    check("overview: Robin's investments", status == 200 and len(inv) == 1 and inv[0]["value"] == 3950000
          and inv[0]["company_name"] == "Psagot Trade" and inv[0]["day_change"] == 41230, inv)
    status, sources = await call(client, "GET", "/api/money/sources", ROBIN)
    acc = sources["sources"][0]["accounts"] if isinstance(sources, dict) else []
    check("sources: the account listed, masked, with its value", acc and acc[0]["kind"] == "investment"
          and acc[0]["number"] == "••••456" and acc[0]["balance"] == 3950000, acc)
    captured["sources"] = {"status": status, "body": sources}
    status, empty = await call(client, "GET", "/api/money/portfolio?days=30&trades=2", BOB)
    check("portfolio: days and trades limit", status == 200 and len(empty["trades"]) <= 2)


async def test_tool() -> None:
    text = await tools.investment_portfolio(Connection(user=ROBIN, addon="money", credentials={}, settings={}))
    check("tool: total, cash, today, profit", "₪39,500.00" in text and "cash ₪1,000.00" in text
          and "today +₪412.30" in text and "profit +₪3,997.06" in text, text)
    check("tool: holdings with share, quantity and profit", 'קרן מחקה "דוגמה" 500 (#2001234): ₪30,000.00' in text
          and "48.99" not in text and "49.0% of the account" in text and "quantity 120" in text
          and "profit +₪4,200.00 (+16.3%)" in text, text)
    check("tool: the latest activity", "Latest activity:" in text and "קניה בבורסה" in text and "×20" in text, text)
    check("tool: how the value moved, the money put in told apart", "Since " in text and "(a month)" in text
          and "₪20,000.00 put in" in text, text)
    check("tool: a deposit without the cash account's name or units", "התקבל מח.בסניף ₪20,000.00" in text
          and CASH not in text, text)
    carl_text = await tools.investment_portfolio(Connection(user=CARL, addon="money", credentials={}, settings={}))
    check("tool: no account yet", carl_text.startswith("No investment account yet"), carl_text)


async def test_disconnect() -> None:
    connections.disconnect(BOB, "money")
    await asyncio.sleep(0.1)
    left = rows("SELECT COUNT(*) AS n FROM money_holdings WHERE user_id = ?", BOB.id)[0]["n"] + \
        rows("SELECT COUNT(*) AS n FROM money_trades WHERE user_id = ?", BOB.id)[0]["n"] + \
        rows("SELECT COUNT(*) AS n FROM money_account_values WHERE user_id = ?", BOB.id)[0]["n"]
    check("disconnect: Bob's portfolio is deleted", left == 0, left)
    check("disconnect: Robin's stays", rows("SELECT COUNT(*) AS n FROM money_holdings WHERE user_id = ?", ROBIN.id)[0]["n"] == 2)


def shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__ if value is not None else None


def test_secrets() -> None:
    conn = get_db_connection()
    try:
        text = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in conn.execute(f"SELECT * FROM '{t}'"):
                text.extend(str(v) for v in row)
    finally:
        conn.close()
    blob = "\n".join(text)
    check("secrets: credentials and the device key are stored only encrypted",
          not [s for s in SECRETS + ["MADE-UP-KEY"] if s in blob])


async def main() -> None:
    test_pure()
    await WORKER.start()
    os.environ["MONEY_WORKER_URL"] = WORKER.url
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    captured: Dict[str, Any] = {}
    with patch.object(push, "send", fake_push), patch.object(push, "notify", fake_notice):
        async with TestClient(TestServer(app)) as client:
            sid = await test_first_sync(client)
            test_ingest(sid)
            await test_next_syncs(client, sid)
            await test_api(client, captured)
            await test_tool()
            await test_disconnect()
    test_secrets()
    await WORKER.close()
    text = json.dumps(captured, indent=1, ensure_ascii=False, sort_keys=True)
    if os.getenv("WRITE_WEB_FIXTURE"):
        with open(WEB_FIXTURE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {WEB_FIXTURE}")
    else:
        with open(WEB_FIXTURE, encoding="utf-8") as f:
            stored = json.load(f)
        drift = [k for k in captured if shape(captured[k]) != shape(stored.get(k))]
        check("fixture: tests/fixtures/money_portfolio_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_money_portfolio)", not drift, drift)


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
