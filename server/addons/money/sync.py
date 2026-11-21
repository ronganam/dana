"""One sync of one source (docs/money.md, "Sync").

:func:`start` writes a ``money_syncs`` row, marks the source ``syncing`` and
runs the sync in the background as the user: decrypt the credentials, start
a worker job (from the last good sync minus :data:`config.OVERLAP_DAYS`, or
:data:`config.FIRST_SYNC_DAYS` back), follow it with long polls, hand
``needs_code`` to ``otp.py``, store the result (``ingest.apply`` in a
thread), run the after-sync hooks (``after_sync.py``) and mark the source ``ok``. One sync per source at a
time: starting another returns the running one's id.

Failures: a login problem (``INVALID_PASSWORD``, ``CHANGE_PASSWORD``,
``ACCOUNT_BLOCKED``, ``CODE_REJECTED``, ``LOGIN_UNKNOWN``) marks the source
``needs_attention`` and Dana never runs it again on its own (new credentials
or, for some, the user's "Sync now" do: ``store.HAND_RETRY_ERRORS``); no code
in time marks it ``failing`` until the next regular slot, and a second one in
a row ``needs_attention``; anything else marks it ``failing`` and retries
after 30 minutes, then 2 hours, then at the regular slots."""

import asyncio
import datetime
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import clock, users
from core.users import User
from core.addons import crypto
from addons.money import after_sync, companies, config, ingest, otp, slots, store, worker_client

logger = logging.getLogger(__name__)

# Login problems the worker reports: never retried (repeating them can lock the login).
# LOGIN_UNKNOWN: the password went out and the site neither let the worker in nor said why.
LOGIN_ERRORS = ("INVALID_PASSWORD", "CHANGE_PASSWORD", "ACCOUNT_BLOCKED", "CODE_REJECTED", "LOGIN_UNKNOWN")
CODE_TIMEOUT = "CODE_TIMEOUT"
POLL_SECONDS = 25
# A profile still busy from before a restart: its old job is cancelled, then the start is tried this
# many times, a second apart (the worker frees the profile once the old browser has closed).
BUSY_TRIES = 10
# A bank asking again after a code was given (a wrong code): ask at most this many times per sync.
MAX_CODE_ROUNDS = 3
# Jobs Dana runs on the worker at once (its browser limit).
MAX_PARALLEL = 2


def login_reason(error_type: str, company: str) -> str:
    """What the user must do about a login problem, in plain words."""
    name = companies.name_of(company)
    if error_type == "CHANGE_PASSWORD":
        return f"{name} wants a new password: change it on {name}'s site, then update it here"
    if error_type == "ACCOUNT_BLOCKED":
        return f"{name} blocked the login: unblock it with {name}, then update it here"
    if error_type == "CODE_REJECTED":
        return (f"{name} refused the sign-in code. Dana won't try again on its own (repeated tries can lock the "
                f"login): press Sync now to sign in once more")
    if error_type == "LOGIN_UNKNOWN":
        return (f"{name}'s sign-in ended without a clear answer, so Dana stopped: if the password was wrong, "
                f"another try could lock the login. Check that it still works on {name}'s site, then press Sync now "
                f"(or update it here)")
    if error_type == CODE_TIMEOUT:
        return (f"{name} sent a sign-in code twice and none reached Dana, so it stopped asking: press Sync now when "
                f"your phone is at hand")
    return f"{name} refused the login details: check them and update them here"


class _Failed(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass
class _Run:
    sync_id: str
    user_id: str
    started_at: str
    task: Optional["asyncio.Task[None]"] = None
    job_id: Optional[str] = None
    token: Optional[str] = None  # the encrypted credentials the job was started with
    done: asyncio.Event = field(default_factory=asyncio.Event)


# Source id → its running sync.
_running: Dict[str, _Run] = {}
_parallel: Optional[Tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = None


def _slot_limit() -> asyncio.Semaphore:
    global _parallel
    loop = asyncio.get_running_loop()
    if _parallel is None or _parallel[0] is not loop:
        _parallel = (loop, asyncio.Semaphore(MAX_PARALLEL))
    return _parallel[1]


def running(source_id: str) -> Optional[str]:
    """The id of the source's running sync, None when it has none."""
    run_ = _running.get(source_id)
    return run_.sync_id if run_ else None


def start(user: User, source_id: str, trigger: str) -> str:
    """Start a sync of one of the user's sources in the background (needs a
    running event loop); returns its id, or the running sync's id.

    Raises:
        LookupError: no such source of the user.
    """
    current = _running.get(source_id)
    if current is not None:
        return current.sync_id
    source = store.get_source(user, source_id)
    if source is None:
        raise LookupError(f"unknown money source {source_id!r}")
    now = store.now_iso()
    sync_id = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("INSERT INTO money_syncs (id, user_id, source_id, trigger, state, started_at) "
                     "VALUES (?, ?, ?, ?, 'running', ?)", (sync_id, user.id, source_id, trigger, now))
        conn.execute("UPDATE money_sources SET state = 'syncing', last_sync_at = ?, updated_at = ? "
                     "WHERE id = ? AND user_id = ?", (now, now, source_id, user.id))
    run_ = _Run(sync_id=sync_id, user_id=user.id, started_at=now)
    _running[source_id] = run_
    with users.acting_as(user):
        run_.task = asyncio.get_running_loop().create_task(_follow(user, source, run_))
    logger.info(f"Money sync {sync_id} of source {source_id} (user {user.id}) started ({trigger})")
    return sync_id


async def run(user: User, source_id: str, trigger: str) -> str:
    """Start a sync of the source (or join the running one) and wait for it to end; returns its id.

    Raises:
        LookupError: no such source of the user.
    """
    sync_id = start(user, source_id, trigger)
    run_ = _running.get(source_id)
    if run_ is not None and run_.sync_id == sync_id:
        await run_.done.wait()
    return sync_id


async def cancel(source_id: str) -> None:
    """Stop the source's running sync (its worker job is cancelled too)."""
    run_ = _running.get(source_id)
    if run_ is None or run_.task is None:
        return
    run_.task.cancel()
    await run_.done.wait()


def _start_date(source: Mapping[str, Any]) -> datetime.date:
    today = clock.now().date()
    last_ok = store.parse(source.get("last_ok_at"))
    if last_ok is None:
        return today - datetime.timedelta(days=config.FIRST_SYNC_DAYS)
    return last_ok.astimezone(clock.get_timezone()).date() - datetime.timedelta(days=config.OVERLAP_DAYS)


async def _start_job(source: Mapping[str, Any], credentials: Mapping[str, str],
                     device: Optional[Mapping[str, str]] = None) -> str:
    start = _start_date(source)
    for attempt in range(BUSY_TRIES):
        try:
            return await worker_client.start_job(source["company"], source["id"], start, credentials, device)
        except worker_client.Busy as e:  # a job left over from before a restart: cancel it, wait for its browser
            if attempt == BUSY_TRIES - 1:
                raise
            if e.job:
                await worker_client.cancel_job(e.job)
            await asyncio.sleep(1)
    raise AssertionError("unreachable")


async def _scrape(user: User, source: Dict[str, Any], run_: _Run) -> List[Dict[str, Any]]:
    """Run the worker job to its end; returns its accounts."""
    token = run_.token = store.credentials_token(user, source["id"])
    if not token:
        raise _Failed("GENERIC", "no login details saved")
    device_token = store.device_token(user, source["id"])
    device = crypto.decrypt(device_token) if device_token else None
    job_id = run_.job_id = await _start_job(source, crypto.decrypt(token), device)
    seen: Optional[str] = None
    code_rounds = 0
    code_given = told = False
    while True:
        job = await worker_client.get_job(job_id, wait=POLL_SECONDS, seen=seen)
        previous, state = seen, str(job.get("state") or "")
        seen = state
        if state == "needs_code" and previous == "needs_code":
            continue  # the long poll ran out: the worker still has the code we gave it, or will time out itself
        if state == "needs_code":
            code_rounds += 1
            if code_rounds > MAX_CODE_ROUNDS:
                await worker_client.cancel_job(job_id)
                raise _Failed("GENERIC", "the bank kept asking for a sign-in code")
            code = await otp.ask(user, source, run_.sync_id)
            if code is None:
                await worker_client.cancel_job(job_id)
                raise _Failed(CODE_TIMEOUT, "no sign-in code arrived")
            with store.db() as conn:
                conn.execute("UPDATE money_sources SET state = 'syncing', state_reason = NULL, updated_at = ? "
                             "WHERE id = ? AND user_id = ?", (store.now_iso(), source["id"], user.id))
                # scoped: this source's own sync run (created for the user's source)
                conn.execute("UPDATE money_syncs SET state = 'running' WHERE id = ?", (run_.sync_id,))
            code_given = await worker_client.post_code(job_id, code) or code_given
            continue
        if code_given and not told and state in ("scraping", "done"):
            told = True
            await otp.signed_in(user, source)
        if state == "done":
            _keep_device(user, source, job)
            accounts = job.get("accounts")
            return list(accounts) if isinstance(accounts, list) else []
        if state == "failed":
            raise _Failed(str(job.get("error_type") or "GENERIC"), str(job.get("error") or "the sign-in failed"))
        if state == "cancelled":
            raise _Failed("GENERIC", "the bank worker cancelled the sync")


def _keep_device(user: User, source: Mapping[str, Any], job: Mapping[str, Any]) -> None:
    """A device key the job registered is kept (encrypted); one the site refused is forgotten."""
    device = job.get("device")
    if isinstance(device, dict) and all(isinstance(device.get(k), str) and device.get(k) for k in ("user", "udid", "key")):
        store.set_device(user, source["id"], crypto.encrypt({k: device[k] for k in ("user", "udid", "key")}))
        logger.info(f"Money source {source['id']}: a device key was registered")
    elif job.get("device_dropped"):
        store.set_device(user, source["id"], None)
        logger.info(f"Money source {source['id']}: the site refused the device key; it is forgotten")


async def _follow(user: User, source: Dict[str, Any], run_: _Run) -> None:
    source_id = source["id"]
    try:
        async with _slot_limit():
            accounts = await asyncio.wait_for(_scrape(user, source, run_),
                                              timeout=config.SYNC_TIMEOUT_MINUTES * 60)
        result = await asyncio.to_thread(ingest.apply, user, source, accounts)
        await after_sync.run(user, source_id, result["new"], result["changed"])
        _succeeded(user, source, run_, result)
    except asyncio.CancelledError:
        if run_.job_id:
            await worker_client.cancel_job(run_.job_id)
        _ended(user, source_id, run_.sync_id, "cancelled", None, "cancelled")
        raise
    except asyncio.TimeoutError:
        if run_.job_id:
            await worker_client.cancel_job(run_.job_id)
        _failed(user, source, run_, "TIMEOUT", "the sync took too long")
    except _Failed as e:
        _failed(user, source, run_, e.error_type, str(e))
    except worker_client.WorkerError as e:
        _failed(user, source, run_, e.error_type if e.error_type else "GENERIC", str(e))
    except crypto.CredentialsKeyError:
        _failed(user, source, run_, "GENERIC", "the saved login details can't be read (CREDENTIALS_KEY)")
    except Exception as e:
        logger.error(f"Money sync {run_.sync_id} of source {source_id} failed: {e}", exc_info=True)
        _failed(user, source, run_, "GENERIC", "unexpected error while syncing")
    finally:
        if _running.get(source_id) is run_:
            del _running[source_id]
        run_.done.set()


def _ended(user: User, source_id: str, sync_id: str, state: str, error_type: Optional[str], error: Optional[str],
           added: Optional[int] = None, updated: Optional[int] = None) -> None:
    with store.db() as conn:
        conn.execute("UPDATE money_syncs SET state = ?, error_type = ?, error = ?, added = ?, updated = ?, "
                     "finished_at = ? WHERE id = ? AND user_id = ?",
                     (state, error_type, ingest.scrub(error or "")[:300] or None, added, updated, store.now_iso(), sync_id,
                      user.id))


def _credentials_changed(conn, user: User, source_id: str, run_: _Run) -> bool:
    """The user saved other credentials while this sync ran (with the old ones)."""
    row = conn.execute("SELECT credentials FROM money_sources WHERE id = ? AND user_id = ?",
                       (source_id, user.id)).fetchone()
    return bool(run_.token) and row is not None and row["credentials"] != run_.token


def _succeeded(user: User, source: Dict[str, Any], run_: _Run, result: Mapping[str, List[str]]) -> None:
    now = store.utc_now()
    next_at = slots.next_slot(source["id"], now)
    with store.db() as conn:
        row = conn.execute("SELECT next_sync_at FROM money_sources WHERE id = ? AND user_id = ?",
                           (source["id"], user.id)).fetchone()
        pulled = store.parse(row["next_sync_at"]) if row else None
        if pulled and store.parse(run_.started_at) < pulled < next_at:
            next_at = pulled  # a signal asked for a sooner sync while this one ran
        if _credentials_changed(conn, user, source["id"], run_):
            next_at = now  # try the new credentials at the loop's next look
        conn.execute("UPDATE money_sources SET state = 'ok', state_reason = NULL, error_type = NULL, failures = 0, "
                     "last_ok_at = ?, next_sync_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                     (store.iso(now), store.iso(next_at), store.iso(now), source["id"], user.id))
    added, updated = len(result["new"]), len(result["changed"])
    _ended(user, source["id"], run_.sync_id, "done", None, None, added, updated)
    store.mark(user.id, True, f"{store.source_name(source)}: {added} new, {updated} changed")
    logger.info(f"Money sync {run_.sync_id} of source {source['id']} done: {added} new, {updated} changed")


def _retry_at(source_id: str, failures: int, now: datetime.datetime) -> datetime.datetime:
    """When to try again after the ``failures``-th failure in a row (not a login problem)."""
    if failures <= len(config.RETRY_MINUTES):
        return now + datetime.timedelta(minutes=config.RETRY_MINUTES[failures - 1])
    return slots.next_slot(source_id, now)


def _failed(user: User, source: Dict[str, Any], run_: _Run, error_type: str, message: str) -> None:
    # The worker's text may quote the site (an error page, a URL): no ID, card or account numbers kept.
    message = ingest.scrub(message or "")[:300]
    now = store.utc_now()
    replaced = False
    with store.db() as conn:
        if _credentials_changed(conn, user, source["id"], run_):
            # The outcome is about the old credentials: the new ones run at the loop's next look.
            replaced = True
            conn.execute("UPDATE money_sources SET state = 'new', state_reason = NULL, error_type = NULL, "
                         "failures = 0, next_sync_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (store.iso(now), store.iso(now), source["id"], user.id))
        else:
            row = conn.execute("SELECT failures, error_type FROM money_sources WHERE id = ? AND user_id = ?",
                               (source["id"], user.id)).fetchone()
            failures = (row["failures"] if row else 0) + 1
            next_at: Optional[datetime.datetime]
            # A code that did not come twice in a row: each try sends the user an SMS, so stop asking.
            again = error_type == CODE_TIMEOUT and row is not None and row["error_type"] == CODE_TIMEOUT
            if error_type in LOGIN_ERRORS or again:
                state, reason, next_at = "needs_attention", login_reason(error_type, source["company"]), None
            elif error_type == CODE_TIMEOUT:
                state, reason, next_at = "failing", message, slots.next_slot(source["id"], now)
            else:
                state, reason, next_at = "failing", message, _retry_at(source["id"], failures, now)
            conn.execute("UPDATE money_sources SET state = ?, state_reason = ?, error_type = ?, failures = ?, "
                         "next_sync_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (state, reason, error_type, failures, store.iso(next_at) if next_at else None,
                          store.iso(now), source["id"], user.id))
    _ended(user, source["id"], run_.sync_id, "failed", error_type, message)
    if replaced:
        logger.info(f"Money sync {run_.sync_id} of source {source['id']} failed with replaced credentials")
        return
    store.mark(user.id, False, f"{store.source_name(source)}: {reason}")
    logger.warning(f"Money sync {run_.sync_id} of source {source['id']} failed: {error_type} ({message})")


def recover(user: User) -> None:
    """After a restart: syncs, code requests and sources left mid-sync by the
    previous process end as interrupted; such a source counts a failure and
    retries like one (30 minutes, 2 hours, then the regular slots), so a
    restart loop cannot sign in to the bank over and over."""
    running_ids = list(_running)
    marks = ",".join("?" * len(running_ids))
    skip = f" AND id NOT IN ({marks})" if running_ids else ""
    skip_source = f" AND source_id NOT IN ({marks})" if running_ids else ""
    now = store.utc_now()
    with store.db() as conn:
        conn.execute("UPDATE money_syncs SET state = 'failed', error_type = 'GENERIC', "
                     "error = 'interrupted by a restart', finished_at = ? "
                     f"WHERE user_id = ? AND state IN ('running', 'needs_code'){skip_source}",
                     (store.iso(now), user.id, *running_ids))
        conn.execute(f"UPDATE money_code_requests SET status = 'cancelled' WHERE user_id = ? AND status = 'pending'"
                     f"{skip_source}", (user.id, *running_ids))
        rows = conn.execute(f"SELECT id, failures FROM money_sources WHERE user_id = ? "
                            f"AND state IN ('syncing', 'needs_code'){skip}", (user.id, *running_ids)).fetchall()
        for row in rows:
            failures = (row["failures"] or 0) + 1
            conn.execute("UPDATE money_sources SET state = 'failing', state_reason = 'interrupted by a restart', "
                         "failures = ?, next_sync_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (failures, store.iso(_retry_at(row["id"], failures, now)), store.iso(now), row["id"],
                          user.id))
