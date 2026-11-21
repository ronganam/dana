"""Client of the bank-worker sidecar (bank-worker/README.md; docs/money.md, "Worker").

The worker listens on the pod's localhost (``MONEY_WORKER_URL``) and checks a
bearer token read from a file on the shared volume; Dana creates that file
(mode 0600) when it is missing. Request bodies carry credentials and codes:
nothing here logs a body, and errors carry only the worker's short text. A
worker that cannot be reached raises :class:`Unreachable` (an ordinary sync
failure, ``GENERIC``)."""

import datetime
import logging
import os
import secrets
from typing import Any, Dict, List, Mapping, Optional

import httpx

from integrations.money import config

logger = logging.getLogger(__name__)

# The worker's own long-poll cap.
MAX_WAIT_SECONDS = 30
TIMEOUT_SECONDS = 15.0


class WorkerError(Exception):
    """The worker refused a request or a job failed: ``error_type`` is one of the
    README's (``GENERIC`` when the worker gave none)."""

    def __init__(self, message: str, error_type: str = "GENERIC") -> None:
        super().__init__(message)
        self.error_type = error_type


class Unreachable(WorkerError):
    """No answer from the worker (not running, wrong address)."""

    def __init__(self) -> None:
        super().__init__("bank worker not reachable")


class Busy(WorkerError):
    """The profile already has a running job (``job``)."""

    def __init__(self, job: str) -> None:
        super().__init__("the bank worker is already running a job for this source")
        self.job = job


def token() -> str:
    """The token the worker checks, created (random, mode 0600) when the file is missing."""
    path = config.token_file()
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
    os.chmod(path, 0o600)
    logger.info("Bank worker token file created")
    return value


async def _request(method: str, path: str, *, json: Optional[Mapping[str, Any]] = None,
                   params: Optional[Mapping[str, Any]] = None, timeout: float = TIMEOUT_SECONDS) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token()}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, config.worker_url() + path, json=json, params=params,
                                        headers=headers)
    except httpx.TransportError:
        raise Unreachable() from None


def _error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    return str(body.get("error") or f"HTTP {response.status_code}")[:200] if isinstance(body, dict) \
        else f"HTTP {response.status_code}"


async def health() -> Optional[Dict[str, Any]]:
    """``GET /health``; None when the worker cannot be reached."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(config.worker_url() + "/health")
    except httpx.TransportError:
        return None
    if r.status_code != 200:
        return None
    body = r.json()
    return body if isinstance(body, dict) else None


async def start_job(company: str, profile: str, start_date: datetime.date, credentials: Mapping[str, str],
                    device: Optional[Mapping[str, str]] = None) -> str:
    """Start a scrape job; returns its id. ``device``: the login's device key from an earlier job
    (Psagot's quick view, README "Psagot").

    Raises:
        Busy: the profile has a running job.
        Unreachable: no answer.
        WorkerError: any other refusal (at the browser limit, a bad body).
    """
    body: Dict[str, Any] = {"company": company, "profile": profile, "start_date": start_date.isoformat(),
                            "credentials": dict(credentials)}
    if device:
        body["device"] = dict(device)
    r = await _request("POST", "/jobs", json=body)
    if r.status_code == 201:
        return str(r.json()["id"])
    if r.status_code == 409:
        raise Busy(str(r.json().get("job") or ""))
    if r.status_code == 429:
        raise WorkerError("the bank worker is busy with other sign-ins")
    raise WorkerError(f"the bank worker refused the job: {_error(r)}")


async def get_job(job_id: str, *, wait: int = 25, seen: Optional[str] = None) -> Dict[str, Any]:
    """The job's state, after a long poll of up to ``wait`` seconds (answered
    early once the state differs from ``seen``).

    Raises:
        Unreachable: no answer.
        WorkerError: the worker no longer knows the job (restarted, or finished long ago).
    """
    wait = max(0, min(int(wait), MAX_WAIT_SECONDS))
    params: Dict[str, Any] = {"wait": wait}
    if seen:
        params["seen"] = seen
    r = await _request("GET", f"/jobs/{job_id}", params=params, timeout=wait + TIMEOUT_SECONDS)
    if r.status_code == 200:
        return r.json()
    if r.status_code == 404:
        raise WorkerError("the bank worker lost the job (it may have restarted)")
    raise WorkerError(f"the bank worker did not answer for the job: {_error(r)}")


async def post_code(job_id: str, code: str) -> bool:
    """Give a waiting job its SMS code. False when the job no longer waits for one.

    Raises:
        Unreachable: no answer.
        WorkerError: the worker refused the code.
    """
    r = await _request("POST", f"/jobs/{job_id}/code", json={"code": code})
    if r.status_code == 202:
        return True
    if r.status_code in (404, 409):
        return False
    raise WorkerError(f"the bank worker refused the code: {_error(r)}")


async def cancel_job(job_id: str) -> None:
    """Cancel a running job (best-effort: a failure is logged)."""
    try:
        r = await _request("DELETE", f"/jobs/{job_id}")
        if r.status_code not in (200, 404):
            logger.warning(f"Bank worker: cancelling job {job_id} answered {r.status_code}")
    except WorkerError as e:
        logger.warning(f"Bank worker: cancelling job {job_id} failed: {e}")


async def delete_profile(profile: str) -> bool:
    """Delete a source's browser profile (best-effort). True when the worker did."""
    try:
        r = await _request("DELETE", f"/profiles/{profile}")
    except WorkerError as e:
        logger.warning(f"Bank worker: deleting profile {profile} failed: {e}")
        return False
    if r.status_code not in (200, 404):
        logger.warning(f"Bank worker: deleting profile {profile} answered {r.status_code}")
    return r.status_code == 200


async def delete_profiles(profiles: List[str]) -> None:
    """Delete several profiles (best-effort)."""
    for profile in profiles:
        await delete_profile(profile)
