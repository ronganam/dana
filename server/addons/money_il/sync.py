"""Reading one source through the bank worker: the ``sync`` of every money_il company
(docs/money-il.md, "Sync"; money runs everything around it: docs/money.md, "Sync").

:func:`sync` starts a worker job for the source (its profile is the source id;
a profile still busy from before a restart gets its old job cancelled and is
started again), follows it with long polls, hands ``needs_code`` to money's
code broker (``SyncJob.ask_code``) and posts the code to the job, and returns
the job's accounts in money's ingest format (``convert.py``) with the device
key the job registered (Psagot's quick view). At most :data:`MAX_PARALLEL`
jobs run at once (the worker's browser limit). A cancelled sync (money's
timeout, a removed source) cancels its job. The worker's refusals become
``providers.SyncError`` with the worker's error type."""

import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from addons.money import providers
from addons.money.providers import SyncError, SyncJob, SyncResult
from addons.money_il import convert, worker_client

logger = logging.getLogger(__name__)

POLL_SECONDS = 25
# A profile still busy from before a restart: its old job is cancelled, then the start is tried this
# many times, a second apart (the worker frees the profile once the old browser has closed).
BUSY_TRIES = 10
# A bank asking again after a code was given (a wrong code): ask at most this many times per sync.
MAX_CODE_ROUNDS = 3
# Jobs Dana runs on the worker at once (its browser limit).
MAX_PARALLEL = 2
DEVICE_KEYS = ("user", "udid", "key")

_parallel: Optional[Tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = None


def _slot_limit() -> asyncio.Semaphore:
    global _parallel
    loop = asyncio.get_running_loop()
    if _parallel is None or _parallel[0] is not loop:
        _parallel = (loop, asyncio.Semaphore(MAX_PARALLEL))
    return _parallel[1]


async def _start_job(job: SyncJob) -> str:
    for attempt in range(BUSY_TRIES):
        try:
            return await worker_client.start_job(job.company, job.source_id, job.start_date, job.credentials,
                                                 job.device)
        except worker_client.Busy as e:  # a job left over from before a restart: cancel it, wait for its browser
            if attempt == BUSY_TRIES - 1:
                raise
            if e.job:
                await worker_client.cancel_job(e.job)
            await asyncio.sleep(1)
    raise AssertionError("unreachable")


async def _follow(job: SyncJob, job_id: str) -> Dict[str, Any]:
    """Follow the worker job to ``done``; returns its final state."""
    seen: Optional[str] = None
    code_rounds = 0
    code_given = False
    while True:
        state_ = await worker_client.get_job(job_id, wait=POLL_SECONDS, seen=seen)
        previous, state = seen, str(state_.get("state") or "")
        seen = state
        if state == "needs_code" and previous == "needs_code":
            continue  # the long poll ran out: the worker still has the code we gave it, or will time out itself
        if state == "needs_code":
            code_rounds += 1
            if code_rounds > MAX_CODE_ROUNDS:
                await worker_client.cancel_job(job_id)
                raise SyncError("GENERIC", "the bank kept asking for a sign-in code")
            try:
                code = await job.ask_code()
            except SyncError:
                await worker_client.cancel_job(job_id)
                raise
            code_given = await worker_client.post_code(job_id, code) or code_given
            continue
        if code_given and state in ("scraping", "done"):
            await job.signed_in()
        if state == "done":
            return state_
        if state == "failed":
            raise SyncError(str(state_.get("error_type") or "GENERIC"), str(state_.get("error") or "the sign-in failed"))
        if state == "cancelled":
            raise SyncError("GENERIC", "the bank worker cancelled the sync")


def _device(done: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """The device key a job registered (Psagot's quick view), when complete."""
    device = done.get("device")
    if isinstance(device, dict) and all(isinstance(device.get(k), str) and device.get(k) for k in DEVICE_KEYS):
        return {k: device[k] for k in DEVICE_KEYS}
    return None


async def sync(job: SyncJob) -> SyncResult:
    """``Company.sync`` of every money_il company: one worker job for the source.

    Raises:
        providers.SyncError: the worker refused the job, the site refused the login
            (the worker's ``error_type``), no code came in time (``CODE_TIMEOUT``), or the worker is down.
    """
    company = providers.company(job.company)
    if company is None:
        raise SyncError("GENERIC", f"unknown company {job.company!r}")
    async with _slot_limit():
        try:
            job_id = await _start_job(job)
            try:
                done = await _follow(job, job_id)
            except asyncio.CancelledError:
                await worker_client.cancel_job(job_id)
                raise
        except worker_client.WorkerError as e:
            raise SyncError(e.error_type or "GENERIC", str(e)) from None
    raw = done.get("accounts")
    return SyncResult(accounts=convert.accounts(company, raw if isinstance(raw, list) else []),
                      device=_device(done), device_dropped=bool(done.get("device_dropped")))


async def forget(source_ids: List[str]) -> None:
    """``Company.forget``: delete the removed sources' browser profiles on the worker (best-effort)."""
    await worker_client.delete_profiles(source_ids)

