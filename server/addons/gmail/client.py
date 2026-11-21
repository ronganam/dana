"""One user's Gmail, over that user's own connection: the refresh token from
the connection's credentials, the account's address from its ``account``. The
OAuth client is the server's (``addons/google_account/oauth.py``, which
also caches the credentials per connection).

Every function takes the user's :class:`~core.addons.Connection`.
The Google client blocks, so callers on the event loop go through :func:`run`;
a service object is built per call (they are not thread-safe).
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Mapping, Optional, Set, Tuple, TypeVar

from googleapiclient.discovery import build

from core.users import User
from core.addons import Connection, NotConnected, store
from addons.gmail.config import ADDON, SERVICE
from addons.google_account import oauth

logger = logging.getLogger(__name__)

# Gmail's name for "the account these credentials belong to".
ME = "me"

T = TypeVar("T")

_tasks: Set["asyncio.Task[Any]"] = set()


def service(conn: Connection) -> Any:
    """A Gmail API client for the connection (blocking calls: use :func:`run`).

    Raises:
        ValueError: the server has no OAuth client or the connection no refresh token.
    """
    return build("gmail", "v1", credentials=oauth.credentials(conn))


async def run(fn: Callable[..., T], *args: Any) -> T:
    """Run blocking Gmail work in a worker thread."""
    return await asyncio.to_thread(fn, *args)


def status_of(error: Exception) -> Optional[int]:
    """The HTTP status of a failed Gmail request (None when it never got an answer)."""
    return getattr(getattr(error, "resp", None), "status", None)


def missing(error: Exception) -> bool:
    """Gmail says there is no such message / thread / draft in this account."""
    return status_of(error) in (400, 404)


def failure(error: Exception) -> str:
    """A short reason for a failed request, safe to show (never a token)."""
    return oauth.failure_reason(error, SERVICE)


def batch(svc: Any, requests: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Exception]]:
    """Send ``requests`` (key → a Gmail request) in one round trip.

    Returns:
        (key → response, key → the error of each request Gmail refused).
    """
    done: Dict[str, Any] = {}
    failed: Dict[str, Exception] = {}
    if not requests:
        return done, failed

    def collect(key: str, response: Any, error: Optional[Exception]) -> None:
        if error is not None:
            failed[key] = error
        else:
            done[key] = response

    together = svc.new_batch_http_request(callback=collect)
    for key, request in requests.items():
        together.add(request, request_id=key)
    together.execute()
    return done, failed


def profile_address(conn: Connection, svc: Optional[Any] = None) -> Optional[str]:
    """The account's address as Gmail reports it (one cheap request)."""
    profile = (svc or service(conn)).users().getProfile(userId=ME).execute()
    return str(profile.get("emailAddress") or "").strip() or None


def own_address(conn: Connection, svc: Optional[Any] = None) -> str:
    """The address the connection sends from: the stored account, else what
    Gmail reports ("" when it cannot be told)."""
    if conn.account:
        return conn.account
    try:
        return profile_address(conn, svc) or ""
    except Exception as e:
        logger.info(f"Gmail address of user {conn.user.id} not known: {failure(e)}")
        return ""


def _check_blocking(conn: Connection) -> Optional[str]:
    try:
        service(conn).users().getProfile(userId=ME).execute()
    except Exception as e:
        return failure(e)
    return None


async def check(conn: Connection) -> Optional[str]:
    """``Addon(check=)``: one cheap request (the account's profile,
    refreshing the access token): None when it works, else a short reason
    (never a token)."""
    return await run(_check_blocking, conn)


# --- The account's address ------------------------------------------------------------------------

async def learn_account(user: User) -> Optional[str]:
    """Ask Gmail which address ``user``'s connection is and store it as the
    connection's account when it differs. Returns the address (None when
    Gmail did not say, or the addon is not connected)."""
    conn = store.get(user, ADDON)
    if conn is None:
        return None
    try:
        address = await run(profile_address, conn)
    except Exception as e:
        logger.info(f"Gmail address of user {user.id} not learned: {failure(e)}")
        return None
    if address and address != conn.account:
        try:
            store.update(user, ADDON, account=address)
        except NotConnected:
            return None
        logger.info(f"Gmail address of user {user.id} learned")
    return address


def _schedule_learn(user: User) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop (a script): the tools ask Gmail when they need the address
    task = loop.create_task(learn_account(user))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def on_connect(user: User) -> None:
    """``Addon(on_connect=)``: learn the account's address when the
    connection came without one (the Google sign-in gives it)."""
    conn = store.get(user, ADDON)
    if conn is not None and not conn.account:
        _schedule_learn(user)


def on_credentials_change(user: User) -> None:
    """``Addon(on_credentials_change=)``: another token may be another
    account: ask Gmail and correct the stored address."""
    _schedule_learn(user)
