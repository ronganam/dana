"""money_il's meal card (Pluxee / Cibus): the sync with its SMS code, the ingest, the cycles and the card line they bill, the API, the tool and the web fixture.
Run: bin/test test_money_il_meals

``testfakes.FakeBankWorker`` plays the bank-worker with a made-up Pluxee result
in the worker's real shape (addons/money_il/worker/README.md, "Pluxee"): every place,
amount and number is invented. Robin (the first admin) and Bob have ``money`` and
``money_il``; Carl has neither. Checked: the company (a phone, the SMS sender and a pattern
that finds a 6-digit code); the code reaching the job; the account (kind
``meal``, what is left of the budget, the cycle in ``details``); the charges
(agorot, the budget's and the card's parts, time, order type, a cancelled one,
ID numbers out of the text) upserted with no duplicates on the next sync; no
transactions and spending untouched; the cycles from the budget's day, the
current one's pace, and each past cycle's overflow matched to the user's own
card line by its exact amount after the cycle ended (not an earlier line of
the same amount, not another user's); the card line's transaction detail
naming its cycle; ``/api/money/meals`` and the overview's meal cards per user
both ways (403 ``not_connected`` for Carl); the ``meal_card`` tool;
disconnecting deletes the charges; and the payload fixture
tests/fixtures/money_meals_web.json (written with ``WRITE_WEB_FIXTURE=1``, else
checked for shape drift).
"""

import asyncio
import datetime
import json
import os
import re
import sys
from typing import Any, Callable, Dict, List, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("money_meals_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-meals-test-env-token"  # a test value: with it set, DB tokens are honoured
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
from core.addons import Connection, registry, store as connections  # noqa: E402
from addons.money import api as money_api, ingest, meals, providers, queries, store, sync, tools  # noqa: E402
from addons.money_il import config as il_config, convert  # noqa: E402
from addons.phone import push  # noqa: E402
from testfakes import FakeBankWorker  # noqa: E402

WEB_FIXTURE = os.path.join(testenv.FIXTURES, "money_meals_web.json")
results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(ROBIN, "money_il")
connections.connect(BOB, "money")
connections.connect(BOB, "money_il")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
WORKER = FakeBankWorker(il_config.token_file())
CODE = "482913"
PHONES = {"robin": "0521112233", "bob": "0547778899"}

TODAY = clock.now().date()
START_DAY = 15
CUR = meals.cycle_of(TODAY, START_DAY)
PREV = meals.cycle_of(CUR[0] - datetime.timedelta(days=1), START_DAY)
OLD = meals.cycle_of(PREV[0] - datetime.timedelta(days=1), START_DAY)  # two cycles back: billed already
BILLED_ON = OLD[1] + datetime.timedelta(days=19)


def dmy(day: datetime.date) -> str:
    return day.strftime("%d/%m/%Y")


def deal(deal_id: int, day: datetime.date, place: str, price: float, card: float = 0.0, *, order: int = 0,
         time: str = "12:30", active: int = 1) -> Dict[str, Any]:
    """A charge as the site lists it (prx_user_deals), with made-up values."""
    return {"deal_id": deal_id, "date": dmy(day), "time": time, "rest_name": place, "restaurant_id": 9000 + deal_id,
            "price": price, "display_price": price, "otl_price": card, "etc_company_price": round(price - card, 2),
            "etc_employee_price": 0.0, "discount": 0.0, "coupon": 0.0, "vtu_price": 0.0, "order_type": order,
            "is_active": active, "rule_name": "1000 חודשי" if card < price else "", "status": "", "refund_id": "",
            "logo": "x.png", "is_3rd_party": False}


def robin_account(*, cancel_second: bool = False) -> Dict[str, Any]:
    left = 310.5
    return {"kind": "meal", "accountNumber": "4321", "balance": left,
            "budgets": [{"CreatioBudget": "1000.00", "CurrBudget": str(left), "CreationDate": dmy(CUR[0]),
                         "ExpirationDate": dmy(CUR[1])}],
            "deals": [
                deal(1, CUR[0], "Made-up Diner - Tel Aviv", 47.3, time="21:49", order=1),
                deal(2, CUR[0], "Falafel Example 012345674", 625.1, active=0 if cancel_second else 1),
                deal(3, PREV[0], "Made-up Diner - Tel Aviv", 900.0),
                deal(4, PREV[0] + datetime.timedelta(days=3), "Pizza Sample", 150.0, 50.0, order=1),
                deal(5, OLD[0], "Made-up Diner - Tel Aviv", 1000.0),
                deal(6, OLD[0] + datetime.timedelta(days=2), "Burger Placeholder", 118.25, 118.25, order=1),
            ], "txns": []}


def bob_account() -> Dict[str, Any]:
    return {"kind": "meal", "accountNumber": "8888", "balance": 0.0,
            "budgets": [{"CreatioBudget": "500.00", "CurrBudget": "0.0", "CreationDate": dmy(CUR[0]),
                         "ExpirationDate": dmy(CUR[1])}],
            "deals": [deal(71, OLD[0], "Bob's Canteen", 618.25, 118.25)], "txns": []}


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


async def idle(source_id: str) -> bool:
    return await until(lambda: sync.running(source_id) is None)


def card_lines(user, lines: List[Tuple[datetime.date, float, str]]) -> None:
    """Cal rows for ``user`` (a card source of their own), through the real ingest."""
    sid = f"cal-{user.id}"
    now = store.now_iso()
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO money_sources (id, user_id, company, credentials, state, created_at, "
                     "updated_at) VALUES (?, ?, 'visaCal', 'x', 'ok', ?, ?)", (sid, user.id, now, now))
        conn.commit()
    txns = [{"date": f"{(d - datetime.timedelta(days=1)).isoformat()}T22:00:00.000Z", "chargedAmount": -amount,
             "description": text, "identifier": f"{user.id[:6]}{i}"} for i, (d, amount, text) in enumerate(lines)]
    ingest.apply(user, {"id": sid, "company": "visaCal"}, [{"accountNumber": "7777", "txns": txns}])


def txn_id(user, day: datetime.date, amount: int) -> str:
    return rows("SELECT id FROM money_transactions WHERE user_id = ? AND purchase_date = ? AND amount = ?",
                user.id, day.isoformat(), amount)[0]["id"]


def test_pure() -> None:
    c = providers.get(ROBIN, "pluxee")
    check("the company: a meal card, one phone field", c.kind == "meal" and [(f.name, f.secret) for f in c.fields]
          == [("phone", False)])
    check("the SMS: its sender as the phone shows it", "1700701130" in c.sms_senders and "1-700-701-130" in c.sms_senders)
    found = re.search(c.code_regex, "הקוד החד פעמי שלך הוא 482913. לא לשתף")
    check("the SMS: a 6-digit code is found, not a longer number", found and found.group(1) == "482913"
          and not re.search(c.code_regex, "1700701130"))
    check("cycles from the budget's day", meals.cycle_of(datetime.date(2026, 11, 19), 22)
          == (datetime.date(2026, 10, 22), datetime.date(2026, 11, 21))
          and meals.cycle_of(datetime.date(2026, 1, 5), 22) == (datetime.date(2025, 12, 22), datetime.date(2026, 1, 21))
          and meals.cycle_of(datetime.date(2026, 2, 28), 31) == (datetime.date(2026, 2, 28), datetime.date(2026, 3, 30)))
    d = convert.deal_row(deal(9, datetime.date(2026, 11, 14), "X 012345674", 190.0, 137.6, time="21:49", order=1))
    check("a charge: agorot, the budget's and the card's parts, time, delivery, ID scrubbed",
          d["total"] == 19000 and d["card_paid"] == 13760 and d["budget_paid"] == 5240
          and d["occurred_at"] == "2026-11-14T21:49:00+02:00" and d["order_type"] == "delivery"
          and d["place"] == "X [id]" and d["ext_key"] == "pluxee:9", d)
    check("a charge without a deal id or date is skipped", convert.deal_row({"date": "1/1/2026", "price": 1}) is None
          and convert.deal_row({"deal_id": 1, "date": "soon", "price": 1}) is None)
    check("billed lines: exact amount, after the cycle, each line once", meals.billed_lines(
        [{"start": "2026-09-22", "end": "2026-10-21", "card_paid": 11825},
         {"start": "2026-08-22", "end": "2026-09-21", "card_paid": 11825}],
        [{"id": "early", "purchase_date": "2026-09-10", "amount": -11825},
         {"id": "a", "purchase_date": "2026-10-10", "amount": -11825},
         {"id": "b", "purchase_date": "2026-11-10", "amount": -11825},
         {"id": "off", "purchase_date": "2026-11-10", "amount": -11824}])
        == {"2026-08-22": {"txn_id": "a", "date": "2026-10-10", "amount": 11825},
            "2026-09-22": {"txn_id": "b", "date": "2026-11-10", "amount": 11825}})


async def test_first_sync(client: TestClient) -> str:
    WORKER.script("*", accounts=[robin_account()], code=CODE)
    status, body = await call(client, "POST", "/api/money/sources", ROBIN,
                              {"company": "pluxee", "credentials": {"phone": PHONES["robin"]}})
    check("add: Pluxee is accepted with a phone", status == 201 and body["company"] == "pluxee", (status, body))
    sid = body["id"]
    await until(lambda: any(r["status"] == "pending" for r in rows(
        "SELECT * FROM money_code_requests WHERE source_id = ?", sid)))
    request = rows("SELECT * FROM money_code_requests WHERE source_id = ? AND status = 'pending'", sid)
    status, req = await call(client, "GET", f"/api/money/code-requests/{request[0]['id']}", ROBIN)
    check("the phone reads the Pluxee sender", status == 200 and "1700701130" in req.get("senders", []), req)
    status, _ = await call(client, "POST", f"/api/money/code-requests/{request[0]['id']}", ROBIN, {"code": CODE})
    await idle(sid)
    started = WORKER.started[-1]
    check("the job: pluxee with the phone", started["company"] == "pluxee"
          and started["credentials"] == {"phone": PHONES["robin"]}, started)
    check("the code reached the job", (started["id"], CODE) in WORKER.codes)
    check("the sync ends ok", rows("SELECT state FROM money_sources WHERE id = ?", sid)[0]["state"] == "ok")
    return sid


def test_ingest(sid: str) -> str:
    accounts = rows("SELECT * FROM money_accounts WHERE source_id = ?", sid)
    check("one meal card: what is left, the cycle", len(accounts) == 1 and accounts[0]["kind"] == "meal"
          and accounts[0]["balance"] == 31050 and json.loads(accounts[0]["details"])
          == {"amount": 100000, "left": 31050, "start": CUR[0].isoformat(), "end": CUR[1].isoformat()}, accounts)
    a = accounts[0]
    check("its label", store.account_label(a["company"], a["kind"], a["number"]) == "Pluxee 4321")
    stored = rows("SELECT * FROM money_meals WHERE account_id = ? ORDER BY ext_key", a["id"])
    check("six charges", [m["ext_key"] for m in stored] == [f"pluxee:{n}" for n in range(1, 7)])
    check("no transactions from a meal card", not rows("SELECT 1 FROM money_transactions WHERE account_id = ?", a["id"]))
    check("an ID number never stored", not any("012345674" in json.dumps(m) for m in stored))
    return a["id"]


async def test_next_sync(client: TestClient, sid: str, account_id: str) -> None:
    WORKER.script("*", accounts=[robin_account(cancel_second=True)])
    status, _ = await call(client, "POST", f"/api/money/sources/{sid}/sync", ROBIN)
    await idle(sid)
    stored = rows("SELECT * FROM money_meals WHERE account_id = ?", account_id)
    check("the next sync: no duplicates, a cancelled charge marked", len(stored) == 6
          and [m["active"] for m in stored if m["ext_key"] == "pluxee:2"] == [0], status)


async def test_api(client: TestClient, captured: Dict[str, Any]) -> None:
    card_lines(ROBIN, [(BILLED_ON, 118.25, "סיבוס פלאקסי"), (OLD[1] - datetime.timedelta(days=2), 118.25, "סיבוס פלאקסי"),
                     (BILLED_ON, 80.0, "WOLT")])
    WORKER.script("*", accounts=[bob_account()], code=CODE)
    status, body = await call(client, "POST", "/api/money/sources", BOB,
                              {"company": "pluxee", "credentials": {"phone": PHONES["bob"]}})
    bob_sid = body["id"]
    await until(lambda: any(r["status"] == "pending" for r in rows(
        "SELECT * FROM money_code_requests WHERE source_id = ?", bob_sid)))
    request = rows("SELECT id FROM money_code_requests WHERE source_id = ? AND status = 'pending'", bob_sid)[0]
    await call(client, "POST", f"/api/money/code-requests/{request['id']}", BOB, {"code": CODE})
    await idle(bob_sid)
    card_lines(BOB, [(BILLED_ON + datetime.timedelta(days=1), 118.25, "סיבוס פלאקסי")])

    status, robin = await call(client, "GET", "/api/money/meals", ROBIN)
    captured["meals"] = {"status": status, "body": robin}
    cur = robin["accounts"][0]["current"] if status == 200 and robin["accounts"] else {}
    check("meals: Robin's card now", status == 200 and len(robin["accounts"]) == 1 and robin["accounts"][0]["left"] == 31050
          and cur["budget"] == 100000 and cur["start"] == CUR[0].isoformat() and cur["count"] == 1
          and cur["used"] == 4730 and cur["days_left"] == (CUR[1] - TODAY).days, robin.get("accounts"))
    check("meals: three cycles, newest first, the cancelled charge left out",
          [(c["start"], c["count"], c["budget_paid"], c["card_paid"]) for c in robin["cycles"]] == [
              (CUR[0].isoformat(), 1, 4730, 0), (PREV[0].isoformat(), 2, 100000, 5000),
              (OLD[0].isoformat(), 2, 100000, 11825)], robin["cycles"])
    billed = {c["start"]: c["billed"] for c in robin["cycles"]}
    line = txn_id(ROBIN, BILLED_ON, -11825)
    check("meals: the old cycle's overflow is the card line after it (not the earlier one, not Bob's)",
          billed[OLD[0].isoformat()] == {"txn_id": line, "date": BILLED_ON.isoformat(), "amount": 11825}
          and billed[PREV[0].isoformat()] is None and billed[CUR[0].isoformat()] is None, billed)
    check("meals: places by visits, kinds, totals", robin["places"][0] == {"place": "Made-up Diner - Tel Aviv", "count": 3,
          "total": 194730} and {k["order_type"] for k in robin["kinds"]} == {"restaurant", "delivery"}
          and robin["totals"] == {"total": 221555, "budget_paid": 204730, "card_paid": 16825, "count": 5}, robin.get("totals"))
    check("meals: the latest meals, newest first, with the cancelled one marked",
          robin["meals"][0]["date"] == CUR[0].isoformat() and any(not m["active"] for m in robin["meals"]))
    check("meals: no raw site rows", "raw" not in json.dumps(robin) and "otl_price" not in json.dumps(robin))
    status, detail = await call(client, "GET", f"/api/money/transactions/{line}", ROBIN)
    captured["transaction"] = {"status": status, "body": detail}
    mc = detail.get("meal_cycle") or {}
    check("the card line's detail names its cycle and the meals over budget", status == 200
          and mc.get("start") == OLD[0].isoformat() and mc.get("card_paid") == 11825
          and [m["place"] for m in mc.get("meals", [])] == ["Burger Placeholder"], detail.get("meal_cycle"))
    early = txn_id(ROBIN, OLD[1] - datetime.timedelta(days=2), -11825)
    status, other = await call(client, "GET", f"/api/money/transactions/{early}", ROBIN)
    check("an earlier line of the same amount is not a meal card bill", status == 200 and other["meal_cycle"] is None)
    everything = queries.periods.resolve(None, start=(OLD[0] - datetime.timedelta(days=5)).isoformat(),
                                         end=(BILLED_ON + datetime.timedelta(days=5)).isoformat())
    spent = queries.summary(ROBIN, everything)
    check("spending: only the card's own lines count, none of the meal card's charges",
          spent["total"] == 11825 * 2 + 8000 and spent["count"] == 3, spent)

    status, bob = await call(client, "GET", "/api/money/meals", BOB)
    check("meals: Bob sees only his", status == 200 and [a["number"] for a in bob["accounts"]] == ["8888"]
          and [c["count"] for c in bob["cycles"]] == [1] and bob["cycles"][0]["billed"]["amount"] == 11825
          and bob["cycles"][0]["billed"]["txn_id"] != line, bob.get("cycles"))
    status, body = await call(client, "GET", f"/api/money/transactions/{line}", BOB)
    check("isolation: Bob cannot read Robin's card line", status == 404, status)
    status, body = await call(client, "GET", "/api/money/meals", CARL)
    check("meals: Carl without money → 403 not_connected", status == 403
          and body == {"error": "not_connected", "addon": "money"}, (status, body))
    status, overview = await call(client, "GET", "/api/money/overview", ROBIN)
    captured["overview_meals"] = {"status": status, "body": overview.get("meals")}
    m = overview.get("meals") or []
    check("overview: Robin's meal card", status == 200 and len(m) == 1 and m[0]["left"] == 31050
          and m[0]["company_name"] == "Pluxee" and m[0]["end"] == CUR[1].isoformat(), m)
    check("overview: a meal card is not a card or a bank", all(c["company"] != "pluxee" for c in overview["cards"])
          and all(b["company"] != "pluxee" for b in overview["banks"]))
    status, months = await call(client, "GET", "/api/money/meals?months=1", ROBIN)
    check("meals: months limits the cycles", status == 200 and len(months["cycles"]) == 1)


async def test_tool() -> None:
    text = await tools.meal_card(Connection(user=ROBIN, addon="money", credentials={}, settings={}))
    check("tool: left, the budget, days to go", "Pluxee 4321" in text and "₪310.50 left of ₪1,000.00" in text
          and f"{(CUR[1] - TODAY).days} days to go" in text, text)
    check("tool: past cycles with the card part and when it was billed", "₪118.25 on the card; billed" in text
          and "₪50.00 on the card." in text, text)
    check("tool: places and latest meals", "Made-up Diner - Tel Aviv (3×" in text and "(cancelled)" in text, text)
    check("tool: a read whose result is untrusted (places are named by the businesses)",
          registry.returns_untrusted("meal_card") and not registry.is_write_tool("meal_card"))
    none = await tools.meal_card(Connection(user=CARL, addon="money", credentials={}, settings={}))
    check("tool: no meal card", none.startswith("No meal card yet"), none)
    overview = await tools.money_overview(Connection(user=ROBIN, addon="money", credentials={}, settings={}))
    check("money_overview mentions the meal card", "Pluxee 4321 (meal card): ₪310.50 left" in overview, overview)


async def test_disconnect() -> None:
    connections.disconnect(ROBIN, "money")
    check("disconnecting deletes the meal card and its charges",
          not rows("SELECT 1 FROM money_meals WHERE user_id = ?", ROBIN.id)
          and not rows("SELECT 1 FROM money_accounts WHERE user_id = ?", ROBIN.id))
    check("Bob's stay", rows("SELECT COUNT(*) AS n FROM money_meals WHERE user_id = ?", BOB.id)[0]["n"] == 1)


def shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


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
            account_id = test_ingest(sid)
            await test_next_sync(client, sid, account_id)
            await test_api(client, captured)
            await test_tool()
            await test_disconnect()
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
        check("fixture: tests/fixtures/money_meals_web.json has the payloads' current shape "
              "(regenerate with WRITE_WEB_FIXTURE=1 bin/test test_money_il_meals)", not drift, drift)


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
