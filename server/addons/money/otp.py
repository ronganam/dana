"""The SMS sign-in code broker (docs/money.md, "Sign-in codes").

When the worker reports ``needs_code``, the sync calls :func:`ask`: a request
row (expires in :data:`config.CODE_MINUTES`), the source's state
``needs_code``, a push ``otp_request {request_id}`` to the user's phone (it
fetches the request, reads the SMS and posts the code) and a Telegram message
the user can answer with the code. :func:`submit` (the API, the Telegram
handler) hands the code to the waiting sync through an in-memory future; the
code is never stored or logged. Nothing within the time: the request
expires and :func:`ask` returns None."""

import asyncio
import datetime
import logging
import re
import uuid
from typing import Any, Dict, Mapping, Optional

from core import notify
from core.users import User
from addons.money import companies, config, store
from addons.phone import push

logger = logging.getLogger(__name__)

CODE_RE = re.compile(r"^\d{4,8}$")
# A digits-only Telegram message this long after a code request is still taken as its (late) code.
LATE_CODE_MINUTES = 15
VIA = ("phone", "web", "telegram")

# Request id → the future the waiting sync awaits (resolved with the code).
_waiters: Dict[str, "asyncio.Future[str]"] = {}


def _minutes_text() -> str:
    return f"{config.CODE_MINUTES:g} minute" + ("" if config.CODE_MINUTES == 1 else "s")


def _create(user: User, source: Mapping[str, Any], sync_id: str) -> Dict[str, Any]:
    now = store.utc_now()
    request = {"id": uuid.uuid4().hex, "company": source["company"], "created_at": store.iso(now),
               "expires_at": store.iso(now + datetime.timedelta(minutes=config.CODE_MINUTES))}
    name = companies.name_of(source["company"])
    with store.db() as conn:
        conn.execute("UPDATE money_code_requests SET status = 'cancelled' WHERE user_id = ? AND source_id = ? "
                     "AND status = 'pending'", (user.id, source["id"]))
        conn.execute("INSERT INTO money_code_requests (id, user_id, source_id, sync_id, company, status, created_at, "
                     "expires_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                     (request["id"], user.id, source["id"], sync_id, source["company"], request["created_at"],
                      request["expires_at"]))
        conn.execute("UPDATE money_sources SET state = 'needs_code', state_reason = ?, updated_at = ? "
                     "WHERE id = ? AND user_id = ?",
                     (f"{name} sent a sign-in code by SMS", request["created_at"], source["id"], user.id))
        conn.execute("UPDATE money_syncs SET state = 'needs_code' WHERE id = ? AND user_id = ?", (sync_id, user.id))
    return request


def _finish(user: User, request_id: str, status: str) -> None:
    """Close a still-pending request as ``expired`` / ``cancelled``."""
    with store.db() as conn:
        conn.execute("UPDATE money_code_requests SET status = ? WHERE id = ? AND user_id = ? AND status = 'pending'",
                     (status, request_id, user.id))


async def _announce(user: User, company: str, request_id: str) -> None:
    """Tell the user's phone and Telegram (each best-effort)."""
    name = companies.name_of(company)
    try:
        await push.send(user, "otp_request", {"request_id": request_id})
    except Exception as e:
        logger.warning(f"Money code request {request_id}: push failed: {e}")
    try:
        await notify.notify(user, f"{name} sent you a sign-in code for Dana. Reply here with the code within "
                                  f"{_minutes_text()}.")
    except Exception as e:
        logger.warning(f"Money code request {request_id}: Telegram message failed: {e}")


async def ask(user: User, source: Mapping[str, Any], sync_id: str) -> Optional[str]:
    """Ask the user for the SMS code a sync of ``source`` waits for and wait
    up to :data:`config.CODE_MINUTES`. Returns the code, or None when none
    came in time (the request is then ``expired``)."""
    request = _create(user, source, sync_id)
    request_id = request["id"]
    future: "asyncio.Future[str]" = asyncio.get_running_loop().create_future()
    _waiters[request_id] = future
    logger.info(f"Money: source {source['id']} of user {user.id} waits for a sign-in code (request {request_id})")
    try:
        await _announce(user, source["company"], request_id)
        remaining = (store.parse(request["expires_at"]) - store.utc_now()).total_seconds()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=max(0.0, remaining))
        except asyncio.TimeoutError:
            _finish(user, request_id, "expired")
            logger.info(f"Money: code request {request_id} expired")
            return None
    except asyncio.CancelledError:
        _finish(user, request_id, "cancelled")
        raise
    finally:
        _waiters.pop(request_id, None)


def _resolve(future: "asyncio.Future[str]", code: str) -> None:
    if not future.done():
        future.set_result(code)


def submit(user: User, request_id: str, code: str, via: str) -> bool:
    """Hand ``code`` to the sync waiting on one of the user's requests. False
    when the code is not 4–8 digits or the request is missing, another
    user's, no longer pending, expired, or has no sync waiting any more."""
    code = (code or "").strip()
    if not CODE_RE.match(code) or via not in VIA:
        return False
    request = store.get_code_request(user, request_id)
    if request is None or request["status"] != "pending":
        return False
    future = _waiters.get(request_id)
    if future is None or future.done():
        _finish(user, request_id, "cancelled")  # its sync is gone (a restart)
        return False
    with store.db() as conn:
        done = conn.execute("UPDATE money_code_requests SET status = 'done', answered_by = ?, answered_at = ? "
                            "WHERE id = ? AND user_id = ? AND status = 'pending'",
                            (via, store.now_iso(), request_id, user.id)).rowcount
    if not done:
        return False
    future.get_loop().call_soon_threadsafe(_resolve, future, code)
    logger.info(f"Money: code request {request_id} answered via {via}")
    return True


def telegram_reply(user: User, text: str) -> Optional[str]:
    """For a Telegram message: when it is only 4–8 digits and the user has a
    pending code request, submit it and return the reply to send (the
    message must then not reach the agent); None for any other message."""
    code = (text or "").strip()
    if not CODE_RE.match(code):
        return None
    pending = store.code_requests(user, status="pending")
    if not pending:
        # A code that comes after the phone already handed one over, or too late: still never
        # passed on to the agent, history or logs.
        since = store.iso(store.utc_now() - datetime.timedelta(minutes=LATE_CODE_MINUTES))
        recent = [r for r in store.code_requests(user) if (r.get("created_at") or "") >= since]
        if not recent:
            return None
        name = companies.name_of(recent[0]["company"])
        if recent[0]["status"] == "done":
            return f"Thanks — Dana already has the code for {name}."
        return f"That code came too late: the sign-in to {name} isn't waiting for it any more."
    request = pending[0]
    name = companies.name_of(request["company"])
    if submit(user, request["id"], code, "telegram"):
        return f"Thanks, signing in to {name}…"
    return f"That code came too late: the sign-in to {name} isn't waiting for it any more."


async def signed_in(user: User, source: Mapping[str, Any]) -> None:
    """The quiet "Signed in" notice on the user's phone after a code worked (best-effort)."""
    name = companies.name_of(source["company"])
    try:
        await push.notify(user, f"Signed in to {name}", "Dana is reading your transactions.",
                          tag=f"money-{source['id']}", channel="digest")
    except Exception as e:
        logger.warning(f"Money: the signed-in notice for source {source['id']} failed: {e}")


def cancel_for_source(user: User, source_id: str) -> None:
    """Close the source's pending requests (the source is being removed)."""
    with store.db() as conn:
        conn.execute("UPDATE money_code_requests SET status = 'cancelled' WHERE user_id = ? AND source_id = ? "
                     "AND status = 'pending'", (user.id, source_id))
