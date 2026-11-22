"""Money providers: a made-up provider addon's companies, sync, code, device, investment and meal formats, rates, per user; the money_il connect migration.
Run: bin/test test_money_providers

A provider addon defined here, ``money_test`` (``needs=("money",)``), brings
three made-up companies through money's extension point (docs/money.md,
"Providers"): a bank that asks for an SMS code, an investment house that
keeps a device key and a meal card, plus reference rates. Alice (the first
admin) has ``money`` and ``money_test``; Bob has ``money`` only; Carl has no
money. Every name, number and amount is invented. Checked: registering
(an unknown kind, another addon's id, the same addon replacing its own);
the companies per user (Alice's only, Bob none, Carl 403 ``money``); adding
a login (Bob 403 ``money_test``, an unknown id 400); a bank sync through the
provider (the job's credentials and start date, ``ask_code`` answered
through the API with the company's senders and pattern, ``signed_in``, the
transactions keyed by ``id_key`` with an installment's ``purchaseDate``);
a login error from the provider (``needs_attention``); the investment shape
(holdings, trades, values, the device key kept encrypted, passed back, and
forgotten when dropped); the meal shape; rates for Alice only (Bob's savings
have none, and no price checks); a source whose company nobody brings any
more (no Sync now, not due, 409 ``unavailable``); turning the provider off
(its logins removed and forgotten, accounts kept). Then money's m0002 on a
database with money connected: users with an Israeli source or only an
Israeli account get ``money_il``; one with another company's source, one
without money and one that has it already are left as they are.
"""

import asyncio
import datetime
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_providers_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake logins
os.environ["API_TOKEN"] = "money-providers-test-env-token"  # a test value: the first admin's token (a headless install)
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Alice"
sys.path.insert(0, str(testenv.SERVER))

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
from core.addons import Addon, store as connections  # noqa: E402
from addons.money import (api as money_api, config, meals, otp, portfolio, providers, savings, schedule,  # noqa: E402
                          sources, store, sync)
from addons.money.providers import Company, LoginField, SyncError, SyncJob, SyncResult  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- The made-up provider ----------------------------------------------------------------------

PROVIDER = "money_test"
JOBS: List[SyncJob] = []
CODES_GIVEN: List[str] = []
FORGOTTEN: List[List[str]] = []
MODE: Dict[str, Any] = {"bank_error": None, "drop_device": False}
TODAY = clock.now().date()
CODE = "613370"
SECRETS = ["alice.bank", "pw-bank-ALICE", "alice.invest", "pw-invest-ALICE", "made-up-device-key"]


async def bank_sync(job: SyncJob) -> SyncResult:
    JOBS.append(job)
    if MODE["bank_error"]:
        raise SyncError(MODE["bank_error"], "the test bank refused the login")
    CODES_GIVEN.append(await job.ask_code())
    await job.signed_in()
    return SyncResult(accounts=[{"accountNumber": "000-111222", "balance": 1500.25, "txns": [
        {"identifier": "t1", "type": "normal", "status": "completed", "date": "2026-11-10T08:00:00.000Z",
         "processedDate": "2026-11-10T08:00:00.000Z", "originalAmount": -42.5, "originalCurrency": "ILS",
         "chargedAmount": -42.5, "description": "Corner Shop Example"},
        {"identifier": "t2", "type": "installments", "status": "completed", "date": "2026-11-05T08:00:00.000Z",
         "processedDate": "2026-11-15T08:00:00.000Z", "originalAmount": -300.0, "originalCurrency": "ILS",
         "chargedAmount": -100.0, "description": "Sample Furniture", "installments": {"number": 2, "total": 3},
         "purchaseDate": "2026-09-01T10:30:00"}]}])


async def invest_sync(job: SyncJob) -> SyncResult:
    JOBS.append(job)
    account = {"accountNumber": "INV-9001", "details": {"value": 1_234_500, "cash": 10_000, "profit": 34_500},
               "holdings": [{"security": "5001", "name": "Example Index Fund", "quantity": 10, "price": 1234.5,
                             "currency": "ILS", "value": 1_224_500, "cost": 1_190_000, "profit": 34_500,
                             "raw": {"made_up": True}}],
               "trades": [{"ext_key": "tt:1", "date": "2026-11-01", "kind": "buy", "action": "buy example",
                           "security": "5001", "name": "Example Index Fund", "quantity": 10, "amount": -1_190_000},
                          {"ext_key": "tt:2", "date": "2026-10-20", "kind": "deposit", "amount": 1_200_000}],
               "values": {"2026-11-01": 1_200_000}}
    if MODE["drop_device"]:
        return SyncResult(accounts=[account], device_dropped=True)
    return SyncResult(accounts=[account], device={"key": "made-up-device-key"})


async def meal_sync(job: SyncJob) -> SyncResult:
    JOBS.append(job)
    start = TODAY.replace(day=1)
    return SyncResult(accounts=[{"accountNumber": "M-77", "budget": {
        "amount": 100_000, "left": 40_000, "start": start.isoformat(),
        "end": (start + datetime.timedelta(days=27)).isoformat()}, "meals": [
        {"ext_key": "tm:1", "date": TODAY.isoformat(), "place": "Sample Cafe", "order_type": "restaurant",
         "total": 3_500, "card_paid": 0},
        {"ext_key": "tm:2", "date": TODAY.isoformat(), "place": "Example Deli", "order_type": "delivery",
         "total": 6_000, "card_paid": 1_000, "raw": {"made_up": True}}]}])


async def forget(source_ids: List[str]) -> None:
    FORGOTTEN.append(sorted(source_ids))


def _changed(user) -> None:
    sources.provider_changed(user, PROVIDER)


Addon(PROVIDER, needs=("money",), title="Money (test)", description="A made-up provider", on_change=_changed)
BANK = providers.register_company(Company(
    "testbank", "Test Bank", "bank", (LoginField("login", "Login"), LoginField("password", "Password", True)),
    PROVIDER, bank_sync, sms_senders=("TestBank",), code_regex=r"code (\d{6})", id_key="tb", forget=forget))
INVEST = providers.register_company(Company(
    "testinvest", "Test Invest", "investment", (LoginField("login", "Login"), LoginField("password", "Password", True)),
    PROVIDER, invest_sync, forget=forget))
MEAL = providers.register_company(Company(
    "testmeal", "Test Meal", "meal", (LoginField("phone", "Phone"),), PROVIDER, meal_sync,
    card_line_words=("TESTMEAL",), forget=forget))
RATES_ASKED: List[str] = []


def policy_rate(today: datetime.date) -> Optional[Dict[str, Any]]:
    RATES_ASKED.append(today.isoformat())
    return {"rate": 3.0, "date": today.isoformat(), "year_ago": 3.5, "year_ago_date": "2025-11-01"}


providers.register_rates(providers.Rates(PROVIDER, policy_rate, lambda currency, start, end: {}, frozenset()))

ALICE = users.first_admin()
assert ALICE is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ALICE, "money")
connections.connect(ALICE, PROVIDER)
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ALICE, BOB, CARL)}
IDS: Dict[str, str] = {}
RESPONSES: List[str] = []


# --- Helpers ----------------------------------------------------------------------------------

async def until(cond: Callable[[], Any], timeout: float = 5.0) -> bool:
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
    RESPONSES.append(text)
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def rows(sql: str, *args: Any) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


async def idle(source_id: str) -> bool:
    return await until(lambda: sync.running(source_id) is None)


# --- 1. Registering ---------------------------------------------------------------------------

def test_register() -> None:
    try:
        providers.register_company(Company("testcrypto", "Coins", "crypto", (), PROVIDER, bank_sync))
        check("register: an unknown kind is refused", False)
    except ValueError:
        check("register: an unknown kind is refused", providers.company("testcrypto") is None)
    try:
        providers.register_company(Company("hapoalim", "Not Hapoalim", "bank", (), PROVIDER, bank_sync))
        check("register: another addon's company id is refused", False)
    except ValueError as e:
        check("register: another addon's company id is refused (money_il keeps it)",
              "money_il" in str(e) and providers.company("hapoalim").addon == "money_il", str(e))
    renamed = providers.register_company(Company("testbank", "Test Bank Renamed", "bank", BANK.fields, PROVIDER, bank_sync))
    check("register: the same addon replaces its own company",
          providers.name_of("testbank") == "Test Bank Renamed" and providers.company("testbank") is renamed)
    providers.register_company(BANK)
    check("register: name_of an id nobody brings is the id", providers.name_of("nobody") == "nobody")


# --- 2. Per user ------------------------------------------------------------------------------

async def test_per_user(client: TestClient) -> None:
    status, body = await call(client, "GET", "/api/money/companies", ALICE)
    check("companies: Alice gets her provider's three, with their login forms (not money_il's)",
          status == 200 and body["companies"] == [
              {"id": "testbank", "name": "Test Bank", "kind": "bank", "fields": [
                  {"name": "login", "label": "Login", "secret": False},
                  {"name": "password", "label": "Password", "secret": True}]},
              {"id": "testinvest", "name": "Test Invest", "kind": "investment", "fields": [
                  {"name": "login", "label": "Login", "secret": False},
                  {"name": "password", "label": "Password", "secret": True}]},
              {"id": "testmeal", "name": "Test Meal", "kind": "meal", "fields": [
                  {"name": "phone", "label": "Phone", "secret": False}]}], body)
    status, body = await call(client, "GET", "/api/money/companies", BOB)
    check("companies: Bob (money only) has none", status == 200 and body == {"companies": []}, body)
    status, body = await call(client, "GET", "/api/money/companies", CARL)
    check("companies: Carl (no money) → 403 not_connected money",
          status == 403 and body == {"error": "not_connected", "addon": "money"}, (status, body))
    status, body = await call(client, "POST", "/api/money/sources", BOB,
                              {"company": "testbank", "credentials": {"login": "bob.bank", "password": "pw-bob"}})
    check("add: Bob without the provider → 403 not_connected money_test",
          status == 403 and body == {"error": "not_connected", "addon": PROVIDER}, (status, body))
    status, body = await call(client, "POST", "/api/money/sources", ALICE,
                              {"company": "nobank", "credentials": {"login": "x", "password": "y"}})
    check("add: a company no provider brings → 400 unknown_company",
          status == 400 and body["error"] == "unknown_company", (status, body))
    check("add: nothing stored for Bob or the refused bodies", store.list_sources(BOB) == []
          and store.list_sources(ALICE) == [])
    check("rates: Alice has her provider's, Bob none; no price checks for either",
          providers.rates(ALICE) is not None and providers.rates(ALICE).addon == PROVIDER
          and providers.rates(BOB) is None and providers.price_checks(ALICE) is None
          and providers.price_checks(BOB) is None)
    ctx_alice, ctx_bob = savings.context(ALICE), savings.context(BOB)
    check("savings: Alice's context carries the policy rate; Bob's none and no market",
          ctx_alice.rate and ctx_alice.rate["rate"] == 3.0 and ctx_bob.rate is None and ctx_bob.fx == {}
          and ctx_alice.market is None and ctx_bob.market is None, (ctx_alice.rate, ctx_bob.rate))


# --- 3. The bank: a sync through the provider, with a code -------------------------------------

async def test_bank(client: TestClient) -> None:
    status, body = await call(client, "POST", "/api/money/sources", ALICE,
                              {"company": "testbank", "credentials": {"login": "alice.bank", "password": "pw-bank-ALICE"}})
    check("bank: added → 201, named by its provider, Sync now offered",
          status == 201 and body["company"] == "testbank" and body["company_name"] == "Test Bank"
          and body["can_sync"] is not None, (status, body))
    sid = IDS["bank"] = body["id"]
    await until(lambda: store.code_requests(ALICE, status="pending"))
    status, listed = await call(client, "GET", "/api/money/code-requests?status=pending", ALICE)
    request_id = listed["requests"][0]["id"] if listed.get("requests") else ""
    status, request = await call(client, "GET", f"/api/money/code-requests/{request_id}", ALICE)
    check("bank: ask_code opens a request with the company's senders and pattern",
          status == 200 and request["company"] == "testbank" and request["company_name"] == "Test Bank"
          and request["senders"] == ["TestBank"] and request["code_regex"] == r"code (\d{6})", request)
    check("bank: the source waits for the code", store.get_source(ALICE, sid)["state"] == "needs_code")
    status, body = await call(client, "POST", f"/api/money/code-requests/{request_id}", ALICE, {"code": CODE})
    check("bank: the code is accepted", status == 200 and body == {"ok": True}, (status, body))
    await idle(sid)
    job = JOBS[-1]
    check("bank: the provider got the decrypted login, the user, no device and a first-sync start date",
          job.company == "testbank" and job.source_id == sid and job.user.id == ALICE.id
          and dict(job.credentials) == {"login": "alice.bank", "password": "pw-bank-ALICE"} and job.device is None
          and job.start_date == TODAY - datetime.timedelta(days=config.FIRST_SYNC_DAYS), job)
    check("bank: the code reached the provider", CODES_GIVEN == [CODE], CODES_GIVEN)
    check("bank: signed_in sent the notice once", otp.signed_in.await_count == 1)
    source = store.get_source(ALICE, sid)
    check("bank: the source is ok", source["state"] == "ok" and source["last_ok_at"], source)
    txns = rows("SELECT ext_key, amount, purchase_date, installment_number, installment_total, merchant "
                "FROM money_transactions WHERE user_id = ? ORDER BY ext_key", ALICE.id)
    check("bank: transactions keyed by the company's id_key + installment number (0 without), agorot, an installment on its purchase date",
          [(t["ext_key"], t["amount"]) for t in txns] == [("tb:t1:0", -4250), ("tb:t2:2", -10000)]
          and txns[1]["purchase_date"] == "2026-09-01" and txns[1]["installment_total"] == 3
          and txns[0]["purchase_date"] == "2026-11-10", txns)
    accounts = store.list_accounts(ALICE, sid)
    check("bank: its account with the balance", len(accounts) == 1 and accounts[0]["kind"] == "bank"
          and accounts[0]["balance"] == 150025, accounts)

    MODE["bank_error"] = "INVALID_PASSWORD"
    await sync.run(ALICE, sid, "manual")
    MODE["bank_error"] = None
    source = store.get_source(ALICE, sid)
    check("bank: a login error from the provider → needs_attention, no Sync now",
          source["state"] == "needs_attention" and source["error_type"] == "INVALID_PASSWORD"
          and store.source_payload(ALICE, source)["can_sync"] is False, source)
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET state = 'ok', error_type = NULL, state_reason = NULL WHERE id = ?", (sid,))
        conn.commit()


# --- 4. Investments and the device key; the meal card -------------------------------------------

async def test_invest_and_meal(client: TestClient) -> None:
    status, body = await call(client, "POST", "/api/money/sources", ALICE,
                              {"company": "testinvest", "credentials": {"login": "alice.invest",
                                                                       "password": "pw-invest-ALICE"}})
    sid = IDS["invest"] = body["id"]
    await idle(sid)
    folio = portfolio.portfolio(ALICE)
    a = folio["accounts"][0] if folio["accounts"] else {}
    check("invest: the account's value, cash and profit from details",
          len(folio["accounts"]) == 1 and a["value"] == 1_234_500 and a["cash"] == 10_000 and a["profit"] == 34_500
          and a["company_name"] == "Test Invest", a)
    check("invest: its holding", [(h["security"], h["name"], h["value"], h["cost"]) for h in a.get("holdings", [])]
          == [("5001", "Example Index Fund", 1_224_500, 1_190_000)], a.get("holdings"))
    check("invest: its trades, newest first, and the deposit as a flow",
          [(t["kind"], t["amount"]) for t in folio["trades"]] == [("buy", -1_190_000), ("deposit", 1_200_000)]
          and folio["flows"] == [{"date": "2026-10-20", "amount": 1_200_000}], (folio["trades"], folio["flows"]))
    check("invest: daily values, today's from the value",
          {p["date"]: p["value"] for p in a.get("history", [])}.get(TODAY.isoformat()) == 1_234_500
          and {p["date"]: p["value"] for p in a.get("history", [])}.get("2026-11-01") == 1_200_000, a.get("history"))
    token = rows("SELECT device FROM money_sources WHERE id = ?", sid)[0]["device"]
    check("invest: the device key is kept, only encrypted", token and "made-up-device-key" not in token)
    await sync.run(ALICE, sid, "manual")
    check("invest: the next sync gets the device key back", dict(JOBS[-1].device or {}) == {"key": "made-up-device-key"},
          JOBS[-1].device)
    MODE["drop_device"] = True
    await sync.run(ALICE, sid, "manual")
    MODE["drop_device"] = False
    check("invest: a dropped device key is forgotten", rows("SELECT device FROM money_sources WHERE id = ?", sid)[0]["device"]
          is None)

    status, body = await call(client, "POST", "/api/money/sources", ALICE,
                              {"company": "testmeal", "credentials": {"phone": "0500000000"}})
    sid = IDS["meal"] = body["id"]
    await idle(sid)
    view = meals.meals(ALICE)
    card = view["accounts"][0] if view["accounts"] else {}
    check("meal: the card's budget and what is left", len(view["accounts"]) == 1 and card["left"] == 40_000
          and card["budget"] == 100_000 and card["company_name"] == "Test Meal", card)
    check("meal: its charges, the budget's and the card's parts",
          sorted((m["place"], m["total"], m["budget_paid"], m["card_paid"]) for m in view["meals"])
          == [("Example Deli", 6_000, 5_000, 1_000), ("Sample Cafe", 3_500, 3_500, 0)], view["meals"])
    check("meal: no transactions from a meal card",
          not rows("SELECT 1 FROM money_transactions WHERE account_id = ?", card.get("id")))
    check("meal: the card line words come from the company",
          meals.card_line_words(store.list_accounts(ALICE, sid)) == ["TESTMEAL"])
    check("isolation: Bob sees none of Alice's accounts", store.list_accounts(BOB) == []
          and portfolio.portfolio(BOB)["accounts"] == [] and meals.meals(BOB)["accounts"] == [])


# --- 5. A company nobody brings any more; turning the provider off --------------------------------

async def test_gone_and_off(client: TestClient) -> None:
    now = store.now_iso()
    with get_db_connection() as conn:
        conn.execute("INSERT INTO money_sources (id, user_id, company, credentials, state, next_sync_at, created_at, "
                     "updated_at) VALUES ('gone-src', ?, 'gonebank', 'x', 'ok', ?, ?, ?)", (ALICE.id, now, now, now))
        conn.commit()
    payload = store.source_payload(ALICE, store.get_source(ALICE, "gone-src"))
    check("gone: a source whose company no provider brings is listed by its id, no Sync now",
          payload["company_name"] == "gonebank" and payload["can_sync"] is False, payload)
    check("gone: it is never due", "gone-src" not in [s["id"] for s in schedule.due_sources(ALICE)])
    status, body = await call(client, "POST", "/api/money/sources/gone-src/sync", ALICE)
    check("gone: Sync now → 409 unavailable", status == 409 and body["error"] == "unavailable", (status, body))
    with get_db_connection() as conn:
        conn.execute("DELETE FROM money_sources WHERE id = 'gone-src'")
        conn.commit()

    accounts = {a["id"] for a in store.list_accounts(ALICE)}
    txns = len(store.list_transactions(ALICE))
    FORGOTTEN.clear()
    connections.disconnect(ALICE, PROVIDER)
    check("off: Alice's logins at the provider's companies are removed", store.list_sources(ALICE) == [])
    check("off: her accounts and transactions stay, without a source",
          {a["id"] for a in store.list_accounts(ALICE)} == accounts
          and all(a["source_id"] is None for a in store.list_accounts(ALICE))
          and len(store.list_transactions(ALICE)) == txns)
    check("off: the provider is asked to forget them, per company",
          await until(lambda: sorted(sum(FORGOTTEN, [])) == sorted(IDS.values())), FORGOTTEN)
    check("off: no companies and no rates left for Alice; money stays",
          providers.companies(ALICE) == [] and providers.rates(ALICE) is None
          and connections.is_connected(ALICE, "money"))
    status, body = await call(client, "GET", "/api/money/companies", ALICE)
    check("off: GET companies is empty", status == 200 and body == {"companies": []}, body)


def test_secrets() -> None:
    every = "\n".join(RESPONSES)
    check("secrets: no credential or device key in any API response", not [s for s in SECRETS if s in every])


# --- 6. money's m0002: connecting money_il --------------------------------------------------------

def test_migration() -> None:
    now = store.now_iso()
    eve, finn, gil, hana, ido = (users.create(n) for n in ("Eve", "Finn", "Gil", "Hana", "Ido"))
    for u in (eve, finn, gil, ido):
        connections.connect(u, "money")
    connections.connect(ido, "money_il")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO money_sources (id, user_id, company, credentials, state, next_sync_at, created_at, "
                     "updated_at) VALUES ('eve-src', ?, 'visaCal', 'x', 'ok', ?, ?, ?)", (eve.id, now, now, now))
        conn.execute("INSERT INTO money_accounts (id, user_id, company, kind, number, created_at, updated_at) "
                     "VALUES ('finn-acc', ?, 'pluxee', 'meal', '1234', ?, ?)", (finn.id, now, now))
        conn.execute("INSERT INTO money_sources (id, user_id, company, credentials, state, created_at, updated_at) "
                     "VALUES ('gil-src', ?, 'testbank', 'x', 'ok', ?, ?)", (gil.id, now, now))
        conn.execute("INSERT INTO money_accounts (id, user_id, company, kind, number, created_at, updated_at) "
                     "VALUES ('hana-acc', ?, 'hapoalim', 'bank', '12-345-000000', ?, ?)", (hana.id, now, now))
        conn.execute("DELETE FROM schema_migrations WHERE addon = 'money' AND version = 2")
        conn.commit()
        before = {r[0]: r[1] for r in conn.execute("SELECT user_id, created_at FROM connections WHERE addon = 'money_il'")}
    db.migrate()
    connections.invalidate()
    with get_db_connection() as conn:
        after = {r[0]: r[1] for r in conn.execute("SELECT user_id, created_at FROM connections WHERE addon = 'money_il'")}
        applied = conn.execute("SELECT name FROM schema_migrations WHERE addon = 'money' AND version = 2").fetchone()
    check("migration: m0002 ran again and is recorded", applied is not None and applied[0] == "money_il", applied)
    check("migration: an Israeli source → money_il connected", connections.is_connected(eve, "money_il"))
    check("migration: only an Israeli account (no source) → connected", connections.is_connected(finn, "money_il"))
    check("migration: another provider's source only → not connected", not connections.is_connected(gil, "money_il"))
    check("migration: no money → not connected, whatever rows", not connections.is_connected(hana, "money_il"))
    check("migration: one who had it already keeps the same connection", after.get(ido.id) == before.get(ido.id)
          and ido.id in after)
    check("migration: nobody else gained it", set(after) - set(before) == {eve.id, finn.id}, (set(after) - set(before)))
    check("migration: the migrated source can sync and is due",
          providers.available(eve, "visaCal") and [s["id"] for s in schedule.due_sources(eve)] == ["eve-src"])
    check("migration: money_il's connection carries no credentials",
          rows("SELECT credentials, settings FROM connections WHERE addon = 'money_il' AND user_id = ?", eve.id)
          == [{"credentials": None, "settings": "{}"}])


async def main() -> None:
    test_register()
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    with patch.object(otp, "_announce", AsyncMock()), patch.object(otp, "signed_in", AsyncMock()):
        async with TestClient(TestServer(app)) as client:
            await test_per_user(client)
            await test_bank(client)
            await test_invest_and_meal(client)
            await test_gone_and_off(client)
    test_secrets()
    test_migration()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
