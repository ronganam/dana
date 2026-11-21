"""Keeping each user's contacts copy in step with Google (docs/contacts.md, "Sync").

``people.connections.list`` with a sync token: the first read is everything,
later ones only what changed (deleted contacts included); a sync token Google
no longer accepts starts over with one full read. A user is synced right
after the addon is connected for them (in the background), again from scratch
when their refresh token changes (it may be another account), and by
:func:`sync_loop` every :data:`~addons.contacts.config.SYNC_MINUTES`:
every user with the addon, each as that user, one user's failure not stopping
the next. After each sync the user's people are filled in
(``addons/contacts/enrich.py``). Disconnecting deletes the copy.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from core import users
from core.users import User
from addons.contacts import client, enrich, store
from addons.contacts.config import ADDON, SYNC_MINUTES
from core.addons import Connection, store as connections
from addons.google_account import oauth

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 20

# One sync at a time per user (a second request waits for the first).
_locks: Dict[str, asyncio.Lock] = {}
_tasks: Set["asyncio.Task[Any]"] = set()


def _still_current(conn: Connection) -> bool:
    """The user still has the connection the sync read with (not disconnected,
    not another refresh token since)."""
    now = connections.get(conn.user, ADDON)
    return now is not None and oauth.refresh_token(now) == oauth.refresh_token(conn)


def _sync_blocking(conn: Connection) -> Optional[Dict[str, int]]:
    user = conn.user
    token = store.sync_token(user)
    try:
        people, next_token = client.list_changes(conn, token)
    except client.SyncTokenExpired:
        logger.info(f"Contacts sync token of user {user.id} expired; reading everything again")
        token = ""
        people, next_token = client.list_changes(conn, "")
    if not _still_current(conn):
        return None  # disconnected or re-connected meanwhile: what was read is not theirs any more
    result = store.apply_sync(user, people, next_token, full=not token)
    result["full"] = int(not token)
    result["people"] = enrich.fill_people(user)
    return result


async def sync_user(user: User) -> Optional[Dict[str, int]]:
    """Bring ``user``'s contacts copy up to date and fill their people from it.

    Returns:
        ``{"changed", "removed", "contacts", "full", "people"}``; None when the
        user has no ``contacts`` (or lost / replaced the connection meanwhile).

    Raises:
        Exception: Google refused or could not be reached (the copy is left as it was).
    """
    lock = _locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        conn = connections.get(user, ADDON)
        if conn is None:
            return None
        result = await asyncio.to_thread(_sync_blocking, conn)
    if result is not None:
        logger.info(f"Contacts of user {user.id} synced: " + ", ".join(f"{k} {v}" for k, v in result.items()))
    return result


def _log_failure(user: User, error: Exception) -> None:
    # The reason only: Google's own message carries the request URL (the sync token).
    logger.error(f"Contacts sync for user {user.id} failed: {client.failure_reason(error)}",
                 exc_info=None if getattr(error, "resp", None) is not None else error)


async def sync_all() -> Dict[str, Optional[Dict[str, int]]]:
    """Sync every user who has ``contacts``, each as that user, one after
    another. Returns user id → result (a user whose sync failed is left out
    and logged; the next one still runs)."""
    out: Dict[str, Optional[Dict[str, int]]] = {}
    for uid in connections.users_with(ADDON):
        user = users.get(uid)
        if user is None:
            continue
        try:
            with users.acting_as(user):
                out[user.id] = await sync_user(user)
        except Exception as e:
            _log_failure(user, e)
    return out


async def sync_loop(interval_minutes: int = SYNC_MINUTES) -> None:
    """Background loop started with the app (core/server.py)."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            await sync_all()
        except Exception as e:
            logger.error(f"Contacts sync loop error: {e}", exc_info=True)
        await asyncio.sleep(interval_minutes * 60)


async def _sync_in_background(user: User) -> None:
    try:
        with users.acting_as(user):
            await sync_user(user)
    except Exception as e:
        _log_failure(user, e)


def schedule(user: User) -> None:
    """Sync ``user`` soon without waiting for it (nothing without a running
    event loop — a script, startup: :func:`sync_loop` gets to them)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_sync_in_background(user))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def on_connect(user: User) -> None:
    """``Addon(on_connect=)``: read the user's contacts in the background."""
    schedule(user)


def on_credentials_change(user: User) -> None:
    """``Addon(on_credentials_change=)``: the refresh token may belong
    to another Google account: the next sync reads everything again (and
    replaces the copy)."""
    store.forget_sync_token(user)
    schedule(user)


def on_change(user: User) -> None:
    """``Addon(on_change=)``: once the addon is disconnected, delete the
    user's copy and sync token and forget which contact each person came from."""
    if not connections.is_connected(user, ADDON):
        store.clear(user)
        enrich.forget(user)
