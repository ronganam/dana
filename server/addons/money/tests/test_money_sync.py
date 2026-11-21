"""Money syncs end to end against a fake bank worker: the code broker, the schedule, the API per user and the check.
Run: bin/test test_money_sync

``testfakes.FakeBankWorker`` plays the bank-worker sidecar (addons/money/worker/README.md)
with scripted outcomes; Dana's token file is the one it checks. The first
admin and Bob have ``money`` (each with a Telegram chat); Carl has not. Phone
push and Telegram are recorded, not sent. Checked: a sync that succeeds
(credentials decrypted for the worker only, start dates, accounts, hooks run
as the user, a failing hook ignored, the health mark); a sign-in code given
through the API (the phone's request with senders and code regex, the push,
the Telegram message, the "signed in" notice), through the Telegram handler
(never reaching the agent) and not at all (the request expires, the job is
cancelled, the source waits for its next slot); a login problem
(``needs_attention``: no retry, no manual sync, until new credentials);
a sign-in with no clear answer (``LOGIN_UNKNOWN``: stopped, only the
user's Sync now) and a second missed code in a row (stopped the same way);
backoff for other failures (30 min, 2 h, the next slot) with no payment
signal pulling a failing source; a job left on the worker from before a
restart; an unreachable worker; the loop (due sources only, as each user, signal syncs); the API
per user (403 ``not_connected`` without the addon, another user's source /
request / syncs answer 404 or nothing, refused bodies); removing a source
with and without its transactions; recovery after a restart; the ``money``
check; disconnecting the addon. Credentials and codes appear in no response,
log line or database value (codes) / only encrypted (credentials).
"""

import asyncio
import datetime
import json
import logging
import os
import socket
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("money_sync_test")
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake logins
os.environ["API_TOKEN"] = "money-sync-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Robin"
os.environ["ALLOWED_TELEGRAM_USER_IDS"] = "111"
os.environ["TELEGRAM_CHAT_ID"] = "111"


class Capture(logging.Handler):
    """Every log line of the process (for "no secret is logged")."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(record.getMessage() + (self.formatException(record.exc_info) if record.exc_info else ""))
        except Exception:
            self.lines.append(str(record.msg))


LOGS = Capture()
logging.getLogger().addHandler(LOGS)
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)  # its own handler prints every debug line

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import checks, clock, notify  # noqa: E402
from core import telegram_bot as tb  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import after_sync, api as money_api, config, otp, schedule, slots, sources, store, sync, worker_client  # noqa: E402
from addons.money.addon import addon as money_addon  # noqa: E402
from addons.phone import push  # noqa: E402
from testfakes import FakeBankWorker  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None and ADMIN.telegram_ids == (111,)
BOB = users.create("Bob", telegram_ids=[222], telegram_chat_id=222)
CARL = users.create("Carl")
connections.connect(ADMIN, "money")
connections.connect(BOB, "money")
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ADMIN, BOB, CARL)}

SECRETS = ["robin.cal.user", "pw-cal-ROBIN-1", "pw-cal-ROBIN-2", "AB77123", "pw-poalim-ROBIN", "bob.code", "pw-poalim-BOB",
           "pw-poalim-BOB-2"]
CODES = ["482913", "771144", "906512"]
RESPONSES: List[str] = []

WORKER = FakeBankWorker(config.token_file())

# --- Fixtures (made-up values in the worker's real shapes) ----------------------------------------

CAL_ACCOUNTS = [{"accountNumber": "8763", "balance": -3450.81, "balanceDate": "2026-12-14T22:00:00.000Z",
                 "cardType": "Mastercard", "txns": [
                     {"identifier": "32580593500", "type": "normal", "status": "completed",
                      "date": "2026-11-17T10:58:17.000Z", "processedDate": "2026-12-14T22:00:00.000Z",
                      "originalAmount": -350.3, "originalCurrency": "₪", "chargedAmount": -350.3,
                      "chargedCurrency": "₪", "description": "רמי לוי - יבנה", "memo": "",
                      "category": "מזון ומשקאות",
                      "rawTransaction": {"trnPurchaseDate": "2026-11-17T12:58:17", "debCrdDate": "2026-12-15T00:00:00",
                                         "branchCodeDesc": "מזון ומשקאות", "isAbroadTransaction": False,
                                         "walletProviderCode": 5}}]}]
POALIM_ACCOUNTS = [{"accountNumber": "12-345-678901", "balance": 1234.56, "txns": [
    {"type": "normal", "identifier": 8763, "date": "2026-11-14T22:00:00.000Z",
     "processedDate": "2026-11-14T22:00:00.000Z", "originalAmount": -8455.9, "originalCurrency": "ILS",
     "chargedAmount": -8455.9, "description": "כאל", "status": "completed", "memo": "",
     "rawTransaction": {"eventDate": 20261115, "valueDate": 20261115, "serialNumber": 2, "eventActivityTypeCode": 2,
                        "activityTypeCode": 491, "referenceNumber": 8763, "eventAmount": 8455.9,
                        "currentBalance": 17710.51, "englishActionDesc": "CAL"}}]},
    {"accountNumber": "12-345-99862", "txns": []}]


# --- Recorders ------------------------------------------------------------------------------------

class FakeTelegram:
    def __init__(self) -> None:
        self.sent: List[Tuple[int, str]] = []

    async def send_text(self, chat_id: int, text: str, parse_mode: Optional[str] = None) -> None:
        self.sent.append((chat_id, text))

    async def notify_whatsapp_voice(self, *args: Any) -> bool:
        return False


TELEGRAM = FakeTelegram()
notify.register_telegram(TELEGRAM)
PUSHES: List[Tuple[str, str, Dict[str, Any]]] = []
NOTICES: List[Tuple[str, str, str]] = []


async def fake_push(user, type_: str, data: Dict[str, Any], replace: bool = False) -> bool:
    PUSHES.append((user.id, type_, dict(data)))
    return True


async def fake_notice(user, title: str, text: str, tag: str = "", channel: str = "messages") -> bool:
    NOTICES.append((user.id, title, channel))
    return True


HOOK_CALLS: List[Tuple[str, Optional[str], str, int, int]] = []


def sync_hook(user, source_id: str, new_ids: List[str], changed_ids: List[str]) -> None:
    current = users.current()
    HOOK_CALLS.append((user.id, current.id if current else None, source_id, len(new_ids), len(changed_ids)))


async def async_hook(user, source_id: str, new_ids: List[str], changed_ids: List[str]) -> None:
    HOOK_CALLS.append(("async", user.id, source_id, len(new_ids), len(changed_ids)))


def broken_hook(user, source_id: str, new_ids: List[str], changed_ids: List[str]) -> None:
    raise RuntimeError("a hook that always fails")


after_sync.on_transactions(broken_hook)
after_sync.on_transactions(sync_hook)
after_sync.on_transactions(async_hook)


# --- Helpers --------------------------------------------------------------------------------------

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


def source_row(source_id: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM money_sources WHERE id = ?", (source_id,)).fetchone()
    return dict(r) if r else {}


def syncs_of(source_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM money_syncs WHERE source_id = ? ORDER BY started_at, rowid",
                                              (source_id,))]


def requests_of(source_id: str) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM money_code_requests WHERE source_id = ? "
                                              "ORDER BY created_at, rowid", (source_id,))]


def set_source(source_id: str, **values: Any) -> None:
    with get_db_connection() as conn:
        conn.execute(f"UPDATE money_sources SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                     (*values.values(), source_id))
        conn.commit()


async def idle(source_id: str, timeout: float = 5.0) -> bool:
    """Wait until the source has no running sync."""
    return await until(lambda: sync.running(source_id) is None, timeout)


def pending_request(source_id: str) -> Optional[Dict[str, Any]]:
    return next((r for r in requests_of(source_id) if r["status"] == "pending"), None)


def minutes_from_now(value: Optional[str]) -> float:
    dt = store.parse(value)
    return (dt - store.utc_now()).total_seconds() / 60 if dt else float("nan")


def all_db_text() -> str:
    conn = sqlite3.connect(DB_PATH)
    try:
        out = []
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for row in conn.execute(f"SELECT * FROM '{t}'"):
                out.extend(str(v) for v in row)
        return "\n".join(out)
    finally:
        conn.close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


IDS: Dict[str, str] = {}


# --- 1. A sync that succeeds -------------------------------------------------------------------------

async def test_success(client: TestClient) -> None:
    WORKER.script("*", accounts=CAL_ACCOUNTS)
    status, body = await call(client, "POST", "/api/money/sources", ADMIN,
                              {"company": "visaCal", "label": "Cal", "credentials": {"username": "robin.cal.user",
                                                                                     "password": "pw-cal-ROBIN-1"}})
    check("add: 201 with the payload; its first sync started at once",
          status == 201 and body["company"] == "visaCal" and body["company_name"] == "Cal" and body["label"] == "Cal"
          and body["state"] == "syncing" and body["credentials_set"] is True and body["code_request"] is None, body)
    sid = IDS["cal"] = body["id"]
    await idle(sid)
    status, listed = await call(client, "GET", "/api/money/sources", ADMIN)
    payload = next(s for s in listed["sources"] if s["id"] == sid)
    check("sync: the source is ok with its card and next bill",
          payload["state"] == "ok" and payload["last_ok_at"]
          and payload["last_sync_at"] <= (payload["accounts"] or [{}])[0].get("balance_at", "") <= payload["last_ok_at"]
          and payload["accounts"] == [
              {"id": payload["accounts"][0]["id"], "kind": "card", "number": "8763", "label": None, "balance": None,
               "balance_at": payload["accounts"][0]["balance_at"], "next_charge": 345081,
               "next_charge_date": payload["accounts"][0]["next_charge_date"]}],
          payload)
    nxt = store.parse(payload["next_sync_at"])
    check("sync: the next sync is the next regular slot",
          nxt is not None and nxt == slots.next_slot(sid, store.parse(payload["last_ok_at"]) or nxt), payload)
    job = WORKER.started[-1]
    first_year = (clock.now().date() - datetime.timedelta(days=config.FIRST_SYNC_DAYS)).isoformat()
    check("worker: the job got the decrypted credentials, the source as profile, a year back",
          job["company"] == "visaCal" and job["profile"] == sid and job["start_date"] == first_year
          and job["credentials"] == {"username": "robin.cal.user", "password": "pw-cal-ROBIN-1"}, job)
    check("worker: Dana created the token file (mode 0600) and every call carried it",
          WORKER.unauthorized == 0 and (config.token_file().stat().st_mode & 0o777) == 0o600)
    runs = syncs_of(sid)
    check("syncs: one 'connect' run, done, 1 new", [(r["trigger"], r["state"], r["added"], r["updated"])
                                                     for r in runs] == [("connect", "done", 1, 0)], runs)
    check("hooks: run after the ingest as the user (sync and async), a failing hook ignored",
          (ADMIN.id, ADMIN.id, sid, 1, 0) in HOOK_CALLS and ("async", ADMIN.id, sid, 1, 0) in HOOK_CALLS, HOOK_CALLS)
    mark = checks.get_mark(store.mark_key(ADMIN.id))
    check("health mark: money_sync:<user> ok", mark.get("ok") == 1 and "Cal" in (mark.get("detail") or ""), mark)

    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", ADMIN)
    check("sync now: 202 with the sync id", status == 202 and body.get("sync_id"), body)
    await idle(sid)
    last_ok = store.parse(source_row(sid)["last_ok_at"])
    check("sync now: the next job starts 10 days before the last good sync",
          WORKER.started[-1]["start_date"] == (last_ok.astimezone(clock.get_timezone()).date()
                                               - datetime.timedelta(days=10)).isoformat(), WORKER.started[-1])
    check("sync now: the second run is 'manual' and found nothing new",
          [(r["trigger"], r["added"], r["updated"]) for r in syncs_of(sid)][-1] == ("manual", 0, 0))
    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", ADMIN)
    check("sync now: again within 10 minutes → 429 too_soon with retry_after",
          status == 429 and body.get("error") == "too_soon" and 590 <= body.get("retry_after", 0) <= 600, body)


# --- 2. A code through the API ---------------------------------------------------------------------

async def test_code_via_api(client: TestClient) -> None:
    WORKER.script("*", code="482913", accounts=POALIM_ACCOUNTS)
    status, body = await call(client, "POST", "/api/money/sources", ADMIN,
                              {"company": "hapoalim", "credentials": {"userCode": "AB77123",
                                                                      "password": "pw-poalim-ROBIN"}})
    sid = IDS["poalim"] = body["id"]
    check("code: the sync waits for a code", await until(lambda: pending_request(sid) is not None))
    request = pending_request(sid)
    rid = request["id"]
    check("code: the phone got otp_request with only the request id",
          (ADMIN.id, "otp_request", {"request_id": rid}) in PUSHES, PUSHES)
    check("code: and a Telegram message to the user's chat", (111, "Bank Hapoalim sent you a sign-in code for Dana. "
                                                                   "Reply here with the code within 4 minutes.")
          in TELEGRAM.sent, TELEGRAM.sent)
    check("code: the request expires in 4 minutes", 3.8 < minutes_from_now(request["expires_at"]) <= 4.01, request)
    status, listed = await call(client, "GET", "/api/money/sources", ADMIN)
    payload = next(s for s in listed["sources"] if s["id"] == sid)
    check("code: the source says needs_code with the request",
          payload["state"] == "needs_code" and payload["code_request"] == {"id": rid,
                                                                          "expires_at": request["expires_at"]}, payload)
    status, body = await call(client, "GET", "/api/money/code-requests?status=pending", ADMIN)
    check("code requests: the pending one listed",
          status == 200 and body["requests"] == [{"id": rid, "source_id": sid, "company": "hapoalim",
                                                  "company_name": "Bank Hapoalim", "status": "pending",
                                                  "expires_at": request["expires_at"]}], body)
    status, body = await call(client, "GET", f"/api/money/code-requests/{rid}", ADMIN)
    check("code request: what the phone needs (senders, code regex)",
          status == 200 and body == {"id": rid, "status": "pending", "company": "hapoalim",
                                     "company_name": "Bank Hapoalim", "senders": ["BHapoalim"],
                                     "code_regex": r"קוד האימות הוא:?\s*(\d{4,8})",
                                     "expires_at": request["expires_at"]}, body)
    for who, method, expected in ((BOB, "GET", 404), (BOB, "POST", 404), (CARL, "GET", 403)):
        status, body = await call(client, method, f"/api/money/code-requests/{rid}", who, {"code": "482913"})
        check(f"code request: {who.name} {method} → {expected}", status == expected, (status, body))
    check("code request: Bob's attempt did not answer it", pending_request(sid) is not None)
    status, body = await call(client, "POST", f"/api/money/code-requests/{rid}", ADMIN, {"code": "12ab"})
    check("code: not 4–8 digits → 400 bad_code", status == 400 and body["error"] == "bad_code", body)
    status, body = await call(client, "POST", f"/api/money/code-requests/{rid}", ADMIN,
                              {"code": "482913", "via": "phone"})
    check("code: accepted", status == 200 and body == {"ok": True}, body)
    await idle(sid)
    check("code: the worker got it and the sync finished",
          WORKER.codes[-1][1] == "482913" and source_row(sid)["state"] == "ok"
          and syncs_of(sid)[-1]["state"] == "done", source_row(sid))
    answered = requests_of(sid)[-1]
    check("code: the request is done, answered by the phone", answered["status"] == "done"
          and answered["answered_by"] == "phone" and answered["answered_at"])
    check("code: a quiet 'Signed in' notice on the phone", (ADMIN.id, "Signed in to Bank Hapoalim", "digest")
          in NOTICES, NOTICES)
    status, body = await call(client, "POST", f"/api/money/code-requests/{rid}", ADMIN, {"code": "482913"})
    check("code: a second answer → 409 not_pending", status == 409 and body["error"] == "not_pending", body)
    WORKER.script(sid, code="482913", accounts=[])
    sync.start(ADMIN, sid, "schedule")
    check("wrong code: the next sync asks again", await until(lambda: pending_request(sid) is not None))
    rid = pending_request(sid)["id"]
    status, body = await call(client, "POST", f"/api/money/code-requests/{rid}", ADMIN, {"code": "1111"})
    await idle(sid)
    row = source_row(sid)
    check("wrong code: the bank's refusal is a login problem, never retried",
          status == 200 and row["state"] == "needs_attention" and row["error_type"] == "CODE_REJECTED"
          and row["next_sync_at"] is None and row["state_reason"].startswith("Bank Hapoalim refused the sign-in code"),
          row)
    try:
        schedule.sync_now(ADMIN, sid, "agent")
        refused = False
    except schedule.NeedsAttention:
        refused = True
    check("wrong code: Dana's own sync stays refused", refused)
    WORKER.script(sid, accounts=POALIM_ACCOUNTS)
    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", ADMIN)
    await idle(sid)
    check("wrong code: the user's Sync now signs in again", status == 202 and source_row(sid)["state"] == "ok",
          (status, body, source_row(sid)))
    status, body = await call(client, "PATCH", f"/api/money/sources/{sid}", ADMIN,
                              {"credentials": {"userCode": "AB77123", "password": "pw-poalim-ROBIN"}})
    await idle(sid)
    check("wrong code: entering the login again signs in once more", status == 200
          and source_row(sid)["state"] == "ok", source_row(sid))
    status, listed = await call(client, "GET", "/api/money/sources", ADMIN)
    payload = next(s for s in listed["sources"] if s["id"] == sid)
    check("bank accounts: masked numbers and the balance", sorted((a["number"], a["balance"])
                                                                   for a in payload["accounts"]) ==
          [("••••862", None), ("••••901", 123456)], payload["accounts"])


# --- 3. A code through Telegram --------------------------------------------------------------------

async def test_code_via_telegram() -> None:
    WORKER.script("*", code="771144", accounts=POALIM_ACCOUNTS)
    payload = sources.add(BOB, "hapoalim", {"userCode": "bob.code", "password": "pw-poalim-BOB"})
    sid = IDS["bob_poalim"] = payload["id"]
    check("telegram: Bob's sync waits for a code", await until(lambda: pending_request(sid) is not None))
    check("telegram: Bob's message went to his chat", any(c == 222 and "Bank Hapoalim" in t for c, t in TELEGRAM.sent))
    check("telegram: the admin's digits are not taken for Bob's request",
          otp.telegram_reply(ADMIN, "771144") != "Thanks, signing in to Bank Hapoalim…"
          and pending_request(sid) is not None)
    bot = tb.TelegramBot.__new__(tb.TelegramBot)
    bot.bot = AsyncMock()
    bot.user_memory_states = {}
    msg = SimpleNamespace(from_user=SimpleNamespace(id=222), chat=SimpleNamespace(id=222), text=" 771144 ",
                          photo=None, voice=None, message_id=7, reply=AsyncMock(), answer=AsyncMock())
    with patch.object(tb, "start_agent_task") as agent:
        await tb.AuthMiddleware()(lambda e, d: bot.handle_message(e, d["user"]), msg, {})
    check("telegram: the code is answered and never reaches the agent",
          msg.reply.await_args.args[0] == "Thanks, signing in to Bank Hapoalim…" and not agent.called,
          msg.reply.await_args)
    await idle(sid)
    check("telegram: the sync finished with the code", source_row(sid)["state"] == "ok"
          and WORKER.codes[-1][1] == "771144" and requests_of(sid)[-1]["answered_by"] == "telegram")
    late = otp.telegram_reply(BOB, "771144")
    check("telegram: the same code again (the phone was quicker) is still kept from the agent",
          late == "Thanks — Dana already has the code for Bank Hapoalim.", late)
    with store.db() as conn:  # the request is old now
        conn.execute("UPDATE money_code_requests SET created_at = ? WHERE user_id = ?",
                     (store.iso(store.utc_now() - datetime.timedelta(minutes=otp.LATE_CODE_MINUTES + 1)), BOB.id))
    check("telegram: digits with no code request in the last minutes go on to the agent as usual",
          otp.telegram_reply(BOB, "771144") is None)


# --- 4. No code in time ---------------------------------------------------------------------------------

async def test_code_timeout(client: TestClient) -> None:
    WORKER.script("*", code="906512", accounts=[])
    minutes = config.CODE_MINUTES
    config.CODE_MINUTES = 0.02  # 1.2 s
    try:
        payload = sources.add(BOB, "hapoalim", {"userCode": "bob.code", "password": "pw-poalim-BOB-2"}, "Second")
        sid = IDS["bob_poalim2"] = payload["id"]
        check("timeout: waiting for a code", await until(lambda: pending_request(sid) is not None))
        job = WORKER.started[-1]["id"]
        rid = pending_request(sid)["id"]
        await idle(sid, timeout=6)
    finally:
        config.CODE_MINUTES = minutes
    row = source_row(sid)
    check("timeout: the request expired and the worker job was cancelled",
          requests_of(sid)[-1]["status"] == "expired" and job in WORKER.cancelled, (requests_of(sid), WORKER.cancelled))
    late = otp.telegram_reply(BOB, "906512")
    check("timeout: a code that comes after is answered 'too late', never passed on",
          late == "That code came too late: the sign-in to Bank Hapoalim isn't waiting for it any more.", late)
    check("timeout: the source is failing, 'no sign-in code arrived' (not a login error)",
          row["state"] == "failing" and row["state_reason"] == "no sign-in code arrived"
          and row["error_type"] == "CODE_TIMEOUT", row)
    nxt = store.parse(row["next_sync_at"])
    check("timeout: no quick retry — it waits for the next regular slot",
          nxt == slots.next_slot(sid, store.parse(row["updated_at"])), row["next_sync_at"])
    check("timeout: the run says so", (syncs_of(sid)[-1]["state"], syncs_of(sid)[-1]["error_type"]) ==
          ("failed", "CODE_TIMEOUT"))
    status, body = await call(client, "POST", f"/api/money/code-requests/{rid}", BOB, {"code": "906512"})
    check("timeout: a late code → 409 not_pending", status == 409 and body["error"] == "not_pending", body)
    config.CODE_MINUTES = 0.02
    try:
        await asyncio.wait_for(sync.run(BOB, sid, "schedule"), timeout=6)
    finally:
        config.CODE_MINUTES = minutes
    row = source_row(sid)
    check("timeout twice in a row: Dana stops asking (each try is an SMS) until the user syncs",
          row["state"] == "needs_attention" and row["error_type"] == "CODE_TIMEOUT" and row["next_sync_at"] is None
          and row["state_reason"].startswith("Bank Hapoalim sent a sign-in code twice and none reached Dana"), row)
    check("timeout twice: a signal cannot pull it", schedule.request_soon(BOB, sid) is None)
    status, listed = await call(client, "GET", "/api/money/sources", BOB)
    check("timeout twice: the page offers Sync now", next(x for x in listed["sources"] if x["id"] == sid)["can_sync"]
          is True)
    WORKER.script(sid, accounts=[])
    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", BOB)
    check("timeout: a manual sync is allowed", status == 202, body)
    await idle(sid)
    check("timeout: and works", source_row(sid)["state"] == "ok")


async def test_replaced_credentials(client: TestClient) -> None:
    sid = IDS["bob_poalim2"]
    WORKER.script(sid, code="000000", accounts=[])
    minutes = config.CODE_MINUTES
    config.CODE_MINUTES = 0.02
    try:
        sync.start(BOB, sid, "schedule")
        check("replaced: a sync waits for a code", await until(lambda: pending_request(sid) is not None))
        WORKER.script(sid, accounts=[])
        status, body = await call(client, "PATCH", f"/api/money/sources/{sid}", BOB,
                                  {"credentials": {"userCode": "bob.code", "password": "pw-poalim-BOB"}})
        check("replaced: new credentials while it runs are saved (the running sync is the one returned)",
              status == 200, body)
        await idle(sid, timeout=6)
    finally:
        config.CODE_MINUTES = minutes
    row = source_row(sid)
    check("replaced: the old sync's failure does not stick to the new credentials",
          row["state"] == "new" and row["failures"] == 0 and row["error_type"] is None
          and minutes_from_now(row["next_sync_at"]) <= 0, row)
    schedule.tick()
    await idle(sid)
    check("replaced: the loop then syncs with the new credentials", source_row(sid)["state"] == "ok"
          and WORKER.started[-1]["credentials"]["password"] == "pw-poalim-BOB")


# --- 5. A login problem ----------------------------------------------------------------------------

async def test_login_error(client: TestClient) -> None:
    WORKER.script("*", error_type="CHANGE_PASSWORD", error="password change required")
    status, body = await call(client, "POST", "/api/money/sources", ADMIN,
                              {"company": "visaCal", "label": "Old Cal",
                               "credentials": {"username": "robin.cal.user", "password": "pw-cal-ROBIN-1"}})
    sid = IDS["old_cal"] = body["id"]
    await idle(sid)
    row = source_row(sid)
    check("login error: needs_attention with what to do, no next sync",
          row["state"] == "needs_attention" and row["error_type"] == "CHANGE_PASSWORD" and row["next_sync_at"] is None
          and row["state_reason"] == "Cal wants a new password: change it on Cal's site, then update it here", row)
    set_source(sid, next_sync_at=store.iso(store.utc_now() - datetime.timedelta(hours=1)))
    started = schedule.tick()
    check("login error: the loop never starts it, even when due", started == {} and len(syncs_of(sid)) == 1, started)
    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", ADMIN)
    check("login error: Sync now → 409 needs_attention with the reason",
          status == 409 and body == {"error": "needs_attention", "reason": row["state_reason"]}, body)
    check("login error: a signal cannot pull it", schedule.request_soon(ADMIN, sid) is None)
    status, listed = await call(client, "GET", "/api/money/sources", ADMIN)
    check("login error: the page offers no Sync now (can_sync false)",
          next(x for x in listed["sources"] if x["id"] == sid)["can_sync"] is False)
    status, body = await call(client, "PATCH", f"/api/money/sources/{sid}", ADMIN,
                              {"credentials": {"username": "robin.cal.user"}})
    check("update: a missing field → 400 naming it", status == 400 and body == {
        "error": "invalid", "field": "password", "message": "Password is required"}, body)
    WORKER.script(sid, accounts=CAL_ACCOUNTS)
    status, body = await call(client, "PATCH", f"/api/money/sources/{sid}", ADMIN,
                              {"label": "Cal 2", "credentials": {"username": "robin.cal.user",
                                                                 "password": "pw-cal-ROBIN-2"}})
    check("update: new credentials clear needs_attention and sync at once",
          status == 200 and body["label"] == "Cal 2" and body["state"] == "syncing" and body["error_type"] is None, body)
    await idle(sid)
    check("update: the new password reached the worker and the source is ok",
          WORKER.started[-1]["credentials"]["password"] == "pw-cal-ROBIN-2" and source_row(sid)["state"] == "ok"
          and source_row(sid)["failures"] == 0)
    status, body = await call(client, "PATCH", f"/api/money/sources/{sid}", ADMIN, {"label": "Spare card"})
    check("update: a label alone does not sync", status == 200 and body["label"] == "Spare card"
          and body["state"] == "ok", body)


async def test_unclear_login(client: TestClient) -> None:
    sid = IDS["old_cal"]
    WORKER.script(sid, error_type="LOGIN_UNKNOWN", error="the login did not finish: still on /connect (כניסה)")
    await sync.run(ADMIN, sid, "schedule")
    row = source_row(sid)
    check("unclear login: stopped like a login problem (a wrong password retried could lock it), no next sync",
          row["state"] == "needs_attention" and row["error_type"] == "LOGIN_UNKNOWN" and row["next_sync_at"] is None
          and row["state_reason"].startswith("Cal's sign-in ended without a clear answer, so Dana stopped"), row)
    set_source(sid, next_sync_at=store.iso(store.utc_now() - datetime.timedelta(hours=1)))
    check("unclear login: the loop never starts it", schedule.tick() == {})
    check("unclear login: a signal cannot pull it", schedule.request_soon(ADMIN, sid) is None)
    try:
        schedule.sync_now(ADMIN, sid, "agent")
        refused = False
    except schedule.NeedsAttention:
        refused = True
    check("unclear login: Dana's own sync is refused", refused)
    status, listed = await call(client, "GET", "/api/money/sources", ADMIN)
    check("unclear login: the page offers Sync now", next(x for x in listed["sources"] if x["id"] == sid)["can_sync"]
          is True)
    WORKER.script(sid, accounts=CAL_ACCOUNTS)
    status, body = await call(client, "POST", f"/api/money/sources/{sid}/sync", ADMIN)
    await idle(sid)
    check("unclear login: the user's Sync now signs in once more", status == 202 and source_row(sid)["state"] == "ok",
          (status, body, source_row(sid)))


async def test_leftover_job() -> None:
    sid = IDS["old_cal"]
    WORKER.script(sid, accounts=CAL_ACCOUNTS)
    left = await worker_client.start_job("visaCal", sid, datetime.date(2026, 11, 1), {"username": "u", "password": "p"})
    await sync.run(ADMIN, sid, "schedule")
    check("leftover job: the old job is cancelled and the sync runs", left in WORKER.cancelled
          and source_row(sid)["state"] == "ok" and syncs_of(sid)[-1]["state"] == "done", (WORKER.cancelled,
                                                                                             source_row(sid)))


# --- 6. Backoff, unreachable worker -----------------------------------------------------------------

async def test_backoff_and_unreachable() -> None:
    sid = IDS["bob_poalim"]
    WORKER.script(sid, error_type="GENERIC", error="the site changed for 100000009 card 4580123456789012")
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("errors: the worker's text is scrubbed before it is stored (ID and card numbers)",
          row["state_reason"] == "the site changed for [id] card ••••012"
          and syncs_of(sid)[-1]["error"] == "the site changed for [id] card ••••012", (row, syncs_of(sid)[-1]))
    WORKER.script(sid, error_type="GENERIC", error="the site changed")
    with store.db() as conn:
        conn.execute("UPDATE money_sources SET failures = 0 WHERE id = ?", (sid,))
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("backoff: 1st failure → failing, retry in 30 min", row["state"] == "failing" and row["failures"] == 1
          and row["state_reason"] == "the site changed" and 29 < minutes_from_now(row["next_sync_at"]) <= 30, row)
    check("backoff: a payment signal never pulls a failing source", schedule.request_soon(BOB, sid) is None
          and source_row(sid)["next_sync_at"] == row["next_sync_at"])
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("backoff: 2nd → in 2 h", row["failures"] == 2 and 119 < minutes_from_now(row["next_sync_at"]) <= 120, row)
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("backoff: 3rd → the next regular slot", row["failures"] == 3
          and store.parse(row["next_sync_at"]) == slots.next_slot(sid, store.parse(row["updated_at"])), row)
    mark = checks.get_mark(store.mark_key(BOB.id))
    check("backoff: the health mark records the failure", mark.get("ok") == 0 and "the site changed" in mark["detail"])
    WORKER.script(sid, unreachable=True)
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("unreachable: a dropped connection is an ordinary failure",
          row["state"] == "failing" and row["state_reason"] == "bank worker not reachable"
          and row["error_type"] == "GENERIC", row)
    url = os.environ["MONEY_WORKER_URL"]
    os.environ["MONEY_WORKER_URL"] = f"http://127.0.0.1:{free_port()}"
    try:
        WORKER.script(sid, accounts=POALIM_ACCOUNTS)
        await sync.run(BOB, sid, "schedule")
        row = source_row(sid)
        check("unreachable: no worker at the address → 'bank worker not reachable'",
              row["state_reason"] == "bank worker not reachable" and syncs_of(sid)[-1]["error"]
              == "bank worker not reachable", row)
        check("kind check: the addons Test says so",
              await money_addon.test(connections.require(BOB, "money")) == "bank worker not reachable")
    finally:
        os.environ["MONEY_WORKER_URL"] = url
    check("kind check: the worker answers", await money_addon.test(connections.require(BOB, "money")) is None)
    await sync.run(BOB, sid, "schedule")
    row = source_row(sid)
    check("backoff: a good sync resets the failures", row["state"] == "ok" and row["failures"] == 0
          and row["state_reason"] is None, row)


# --- 7. The loop ------------------------------------------------------------------------------------

async def test_schedule() -> None:
    sid, waiting = IDS["bob_poalim"], IDS["bob_poalim2"]
    past = store.iso(store.utc_now() - datetime.timedelta(minutes=1))
    future = store.iso(store.utc_now() + datetime.timedelta(hours=3))
    set_source(sid, next_sync_at=past)
    set_source(waiting, next_sync_at=past, state="needs_code")
    for key in ("cal", "poalim", "old_cal"):
        set_source(IDS[key], next_sync_at=future)
    before = HOOK_CALLS[:]
    started = schedule.tick()
    check("loop: only Bob's due source starts (not one waiting for a code, not the admin's)",
          list(started) == [BOB.id] and len(started[BOB.id]) == 1, started)
    await idle(sid)
    check("loop: a regular 'schedule' sync, run as Bob", syncs_of(sid)[-1]["trigger"] == "schedule"
          and any(c[:3] == (BOB.id, BOB.id, sid) for c in HOOK_CALLS[len(before):]), syncs_of(sid)[-1])
    check("loop: nothing due now", schedule.tick() == {})
    with patch.object(slots, "out_of_quiet_hours", side_effect=lambda s, w: w):
        due = schedule.request_soon(BOB, sid, minutes=0)
    check("signal: pulled to now", due is not None and minutes_from_now(due) <= 0.1, due)
    await asyncio.sleep(1.05)  # due strictly before the tick's "now"
    schedule.tick()
    await idle(sid)
    check("signal: the loop runs it as a 'signal' sync", syncs_of(sid)[-1]["trigger"] == "signal", syncs_of(sid)[-1])
    set_source(waiting, state="ok", next_sync_at=future)


# --- 8. The API per user -----------------------------------------------------------------------------

async def test_api_per_user(client: TestClient) -> None:
    routes = [("GET", "/api/money/companies"), ("GET", "/api/money/sources"), ("POST", "/api/money/sources"),
              ("PATCH", f"/api/money/sources/{IDS['cal']}"), ("DELETE", f"/api/money/sources/{IDS['cal']}"),
              ("POST", f"/api/money/sources/{IDS['cal']}/sync"), ("GET", "/api/money/syncs"),
              ("GET", "/api/money/code-requests"), ("GET", "/api/money/code-requests/x"),
              ("POST", "/api/money/code-requests/x")]
    for method, path in routes:
        status, body = await call(client, method, path, CARL, {})
        check(f"no money: {method} {path} → 403 not_connected",
              status == 403 and body == {"error": "not_connected", "addon": "money"}, (status, body))
    status, body = await call(client, "GET", "/api/money/companies", BOB)
    check("companies: all four, with their login forms", status == 200 and body["companies"] == [
        {"id": "hapoalim", "name": "Bank Hapoalim", "kind": "bank", "fields": [
            {"name": "userCode", "label": "User code", "secret": False},
            {"name": "password", "label": "Password", "secret": True}]},
        {"id": "visaCal", "name": "Cal", "kind": "card", "fields": [
            {"name": "username", "label": "Username", "secret": False},
            {"name": "password", "label": "Password", "secret": True}]},
        {"id": "psagot", "name": "Psagot Trade", "kind": "investment", "fields": [
            {"name": "username", "label": "ID number or user", "secret": False},
            {"name": "password", "label": "Password", "secret": True}]},
        {"id": "pluxee", "name": "Pluxee", "kind": "meal", "fields": [
            {"name": "phone", "label": "Mobile number", "secret": False}]}], body)
    status, body = await call(client, "GET", "/api/money/sources", BOB)
    check("sources: Bob sees only his own", {s["id"] for s in body["sources"]} ==
          {IDS["bob_poalim"], IDS["bob_poalim2"]}, body)
    status, body = await call(client, "GET", "/api/money/sources", ADMIN)
    check("sources: the admin only theirs", {s["id"] for s in body["sources"]} ==
          {IDS["cal"], IDS["poalim"], IDS["old_cal"]})
    for method, path, payload in (("PATCH", f"/api/money/sources/{IDS['cal']}", {"label": "mine now"}),
                                  ("DELETE", f"/api/money/sources/{IDS['cal']}", None),
                                  ("POST", f"/api/money/sources/{IDS['cal']}/sync", None)):
        status, body = await call(client, method, path, BOB, payload)
        check(f"isolation: Bob {method} the admin's source → 404", status == 404, (status, body))
    check("isolation: the admin's source is untouched", source_row(IDS["cal"])["label"] == "Cal")
    status, body = await call(client, "GET", f"/api/money/syncs?source={IDS['cal']}", BOB)
    check("isolation: Bob gets none of the admin's syncs", status == 200 and body["syncs"] == [], body)
    status, body = await call(client, "GET", f"/api/money/syncs?source={IDS['cal']}&limit=2", ADMIN)
    check("syncs: the admin's, newest first, limited", status == 200 and len(body["syncs"]) == 2
          and set(body["syncs"][0]) == {"id", "source_id", "trigger", "state", "error_type", "error", "added",
                                         "updated", "started_at", "finished_at"}
          and body["syncs"][0]["trigger"] == "manual", body)
    status, body = await call(client, "GET", "/api/money/code-requests", BOB)
    check("code requests: Bob's own only", status == 200 and {r["source_id"] for r in body["requests"]} <=
          {IDS["bob_poalim"], IDS["bob_poalim2"]} and body["requests"], body)
    for payload, expected in (({"company": "max", "credentials": {}}, (400, "unknown_company")),
                              ({"company": "visaCal", "credentials": {"username": "x"}}, (400, "invalid")),
                              ({"company": "visaCal", "credentials": {"username": "x", "password": "y", "pin": "1"}},
                               (400, "invalid")),
                              ({"company": "visaCal", "credentials": "x"}, (400, "invalid"))):
        status, body = await call(client, "POST", "/api/money/sources", BOB, payload)
        check(f"add: refused {payload['credentials']!r} for {payload['company']}",
              (status, body.get("error")) == expected, (status, body))
    r = await client.post("/api/money/sources", data="not json",
                          headers={"Authorization": f"Bearer {TOKENS[BOB.id]}"})
    check("add: a body that isn't JSON → 400", r.status == 400)
    check("add: nothing was stored for the refused bodies", len(store.list_sources(BOB)) == 2)


# --- 9. Removing sources, recovery, the check, disconnecting -------------------------------------------

async def test_remove(client: TestClient) -> None:
    sid = IDS["poalim"]
    accounts = store.list_accounts(ADMIN, sid)
    count = len(store.list_transactions(ADMIN))
    status, body = await call(client, "DELETE", f"/api/money/sources/{sid}", ADMIN)
    with get_db_connection() as conn:
        kept = [dict(r) for r in conn.execute("SELECT * FROM money_accounts WHERE id IN (?, ?)",
                                              tuple(a["id"] for a in accounts))]
    check("remove: the source goes, its accounts and transactions stay (no source)",
          status == 200 and body == {"ok": True} and store.get_source(ADMIN, sid) is None and len(kept) == 2
          and all(a["source_id"] is None for a in kept) and len(store.list_transactions(ADMIN)) == count, kept)
    check("remove: the worker was asked to delete its browser profile", sid in WORKER.deleted_profiles)
    sid = IDS["old_cal"]
    card = store.list_accounts(ADMIN, sid)
    status, body = await call(client, "DELETE", f"/api/money/sources/{sid}?transactions=1", ADMIN)
    check("remove with transactions: its accounts and their transactions are gone",
          status == 200 and card and store.get_account(ADMIN, card[0]["id"]) is None
          and not store.list_transactions(ADMIN, account_id=card[0]["id"]), card)
    status, body = await call(client, "DELETE", f"/api/money/sources/{sid}", ADMIN)
    check("remove: a missing source → 404", status == 404)


def test_recover() -> None:
    sid = IDS["cal"]
    with get_db_connection() as conn:
        conn.execute("UPDATE money_sources SET state = 'needs_code' WHERE id = ?", (sid,))
        conn.execute("INSERT INTO money_syncs (id, user_id, source_id, trigger, state, started_at) VALUES "
                     "('stale-sync', ?, ?, 'schedule', 'needs_code', ?)", (ADMIN.id, sid, store.now_iso()))
        conn.execute("INSERT INTO money_code_requests (id, user_id, source_id, sync_id, company, status, created_at, "
                     "expires_at) VALUES ('stale-req', ?, ?, 'stale-sync', 'visaCal', 'pending', ?, ?)",
                     (ADMIN.id, sid, store.now_iso(), store.iso(store.utc_now() + datetime.timedelta(minutes=3))))
        conn.commit()
    check("telegram: digits while a request is pending but its sync is gone are still kept from the agent",
          otp.telegram_reply(ADMIN, "1234") is not None and requests_of(sid)[-1]["status"] == "cancelled")
    with get_db_connection() as conn:
        conn.execute("UPDATE money_code_requests SET status = 'pending' WHERE id = 'stale-req'")
        conn.commit()
    schedule.recover_all()
    row = source_row(sid)
    check("recover: a source left mid-sync is failing and retries in 30 min",
          row["state"] == "failing" and row["state_reason"] == "interrupted by a restart"
          and 29 < minutes_from_now(row["next_sync_at"]) <= 30, row)
    check("recover: its run failed and its code request is cancelled",
          [r for r in syncs_of(sid) if r["id"] == "stale-sync"][0]["state"] == "failed"
          and [r for r in requests_of(sid) if r["id"] == "stale-req"][0]["status"] == "cancelled")
    set_source(sid, state="syncing")
    schedule.recover_all()
    row = source_row(sid)
    check("recover: restarts count as failures (a restart loop backs off: then 2 h)",
          row["failures"] == 2 and 119 < minutes_from_now(row["next_sync_at"]) <= 120, row)
    set_source(sid, state="ok", state_reason=None, failures=0)


def test_check() -> None:
    def money_check(user) -> Dict[str, Any]:
        return checks.compute(user=user, keys=("money",))[0]

    check("check: only for users with money", "money" in checks.keys_for(ADMIN) and "money" in checks.keys_for(BOB)
          and "money" not in checks.keys_for(CARL))
    for key in ("cal", ):
        set_source(IDS[key], state="ok", last_ok_at=store.now_iso())
    c = money_check(ADMIN)
    check("check: ok while every source synced lately", c["ok"] and c["detail"].startswith("synced"), c)
    set_source(IDS["cal"], state="needs_attention", state_reason="Cal wants a new password: change it on Cal's site, "
                                                                  "then update it here")
    c = money_check(ADMIN)
    check("check: needs_attention fails, naming the source and why",
          not c["ok"] and c["title"] == "Cal needs its login fixed"
          and c["detail"] == "Cal: Cal wants a new password: change it on Cal's site, then update it here", c)
    set_source(IDS["cal"], state="needs_code", state_reason=None)
    c = money_check(ADMIN)
    check("check: waiting for a code fails", not c["ok"] and c["title"] == "Cal is waiting for a sign-in code", c)
    set_source(IDS["cal"], state="failing", state_reason="the site changed",
               last_ok_at=store.iso(store.utc_now() - datetime.timedelta(hours=30)))
    c = money_check(ADMIN)
    check("check: no good sync for 24 h fails", not c["ok"] and c["title"] == "Cal hasn't synced for 30 h"
          and c["detail"] == "Cal: no successful sync for 30 h (the site changed)", c)
    check("check: Bob's sources are his own (ok)", money_check(BOB)["ok"], money_check(BOB))
    check("check: Carl (no money) is ok 'not connected'", money_check(CARL)["detail"] == "not connected")
    set_source(IDS["cal"], state="ok", state_reason=None, last_ok_at=store.now_iso())


async def test_disconnect() -> None:
    bob_sources = [s["id"] for s in store.list_sources(BOB)]
    admin_before = len(store.list_transactions(ADMIN))
    connections.disconnect(BOB, "money")
    with get_db_connection() as conn:
        left = {t: conn.execute(f"SELECT count(*) FROM {t} WHERE user_id = ?", (BOB.id,)).fetchone()[0]
                for t in ("money_sources", "money_accounts", "money_transactions", "money_rules", "money_aliases",
                          "money_syncs", "money_code_requests")}
    check("disconnect: every money row of Bob is deleted", not any(left.values()), left)
    check("disconnect: the worker deletes his browser profiles",
          await until(lambda: set(bob_sources) <= set(WORKER.deleted_profiles)), WORKER.deleted_profiles)
    check("disconnect: the admin's data stays", len(store.list_transactions(ADMIN)) == admin_before)


def test_secrets() -> None:
    every_response = "\n".join(RESPONSES)
    check("secrets: no credential in any API response", not [s for s in SECRETS if s in every_response])
    logged = "\n".join(LOGS.lines)
    check("secrets: the log capture saw the syncs and the worker calls",
          "Money sync" in logged and "code request" in logged and "/jobs" in logged)
    check("secrets: no credential or code in any log line", not [s for s in SECRETS + CODES if s in logged],
          [s for s in SECRETS + CODES if s in logged])
    text = all_db_text()
    check("secrets: credentials are stored only encrypted; codes nowhere",
          not [s for s in SECRETS + CODES if s in text], [s for s in SECRETS + CODES if s in text])


async def main() -> None:
    await WORKER.start()
    os.environ["MONEY_WORKER_URL"] = WORKER.url
    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    with patch.object(push, "send", fake_push), patch.object(push, "notify", fake_notice):
        async with TestClient(TestServer(app)) as client:
            await test_success(client)
            await test_code_via_api(client)
            await test_code_via_telegram()
            await test_code_timeout(client)
            await test_replaced_credentials(client)
            await test_login_error(client)
            await test_unclear_login(client)
            await test_leftover_job()
            await test_backoff_and_unreachable()
            await test_schedule()
            await test_api_per_user(client)
            await test_remove(client)
            test_recover()
            test_check()
            await test_disconnect()
    test_secrets()
    await WORKER.close()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
