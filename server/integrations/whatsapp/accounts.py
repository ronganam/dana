"""Which WhatsApp account each connection is, and which connection an inbound
WAHA event is for (docs/whatsapp.md, docs/access.md "Inbound").

The account is the session's own WhatsApp id as WAHA reports it (``me.id``,
e.g. ``972501234567@c.us``), stored in the ``integrations.account`` column.
It is learned from WAHA itself: right after the integration is connected or
its credentials change (``on_connect`` / ``on_credentials_change``), by
:func:`account_loop` for connections that have none yet (at startup, then
every :data:`REFRESH_SECONDS`), and lazily when an event names an account no
connection has. Learning never blocks and tolerates WAHA being down (a
connection is asked again at most every :data:`RETRY_SECONDS`).

An account belongs to at most one connection: the one that claimed it first
(:func:`integrations.framework.store.claim_account`, checked and written in one
transaction). A connection whose WAHA reports an account another user's
connection holds gets no account (the reported one is kept in
``integrations.account_conflict`` for an admin to see), is never routed to,
and its tools refuse (:func:`blocked`) until it reports a different account
(it keeps being asked like any connection without an account).

An event is routed by the ``me.id`` of WAHA's webhook body to the one
connection holding that account; without ``me``, by its ``session`` name
when exactly one connection without a conflict uses that session.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Mapping, Optional, Set

from core import users
from core.users import User
from integrations.framework import Connection, InvalidValue, addresses, store
from integrations.whatsapp.client import base_url, fetch_me, session
from integrations.whatsapp.config import DEFAULT_SESSION, KIND

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 60.0
RETRY_SECONDS = 60.0

TAKEN = "This WhatsApp account is already connected by another user, so WhatsApp can't be used here."

# user id -> monotonic time of the last attempt to learn their account.
_last_attempt: Dict[str, float] = {}
_tasks: Set["asyncio.Task[Any]"] = set()


def account_key(jid: Optional[str]) -> str:
    """The comparable part of a WhatsApp id: the number before ``@`` and any
    ``:device`` suffix (``972501234567:12@s.whatsapp.net`` →
    ``972501234567``); "" for nothing."""
    local = str(jid or "").strip().split("@", 1)[0]
    return local.split(":", 1)[0]


def connections() -> List[Connection]:
    """Every user's ``whatsapp`` connection."""
    out: List[Connection] = []
    for uid in store.users_with(KIND):
        user = users.get(uid)
        conn = store.get(user, KIND) if user else None
        if conn is not None:
            out.append(conn)
    return out


async def learn(user: User) -> Optional[str]:
    """Ask the user's WAHA for its account and claim it for their connection.
    Returns the account (None when WAHA did not say — unreachable, an error,
    not logged in — and when another user's connection already holds it)."""
    conn = store.get(user, KIND)
    if conn is None:
        return None
    _last_attempt[user.id] = time.monotonic()
    me = await fetch_me(conn)
    account = str((me or {}).get("id") or "").strip()
    if not account:
        logger.info(f"WhatsApp account of user {user.id} not known yet (WAHA did not report the session's me)")
        return None
    if account == conn.account and not conn.account_conflict:
        return account
    holder = store.claim_account(user, KIND, account, account_key)
    if holder is not None:
        logger.warning(f"WhatsApp account reported for user {user.id} is already connected by user {holder}; "
                       f"user {user.id}'s connection is not used until it reports another account")
        return None
    logger.info(f"WhatsApp account of user {user.id} learned")
    return account


def blocked(conn: Connection) -> Optional[str]:
    """``Integration(blocked=)`` of ``whatsapp``: why the connection's tools
    cannot run (its WAHA reports an account another user's connection
    holds), or None."""
    return TAKEN if conn.account_conflict else None


async def refresh_missing(*, force: bool = False) -> int:
    """Learn the account of every connection that has none (not one asked
    within :data:`RETRY_SECONDS`, unless ``force``). Returns how many were learned."""
    learned = 0
    now = time.monotonic()
    for conn in connections():
        if conn.account:
            continue
        if not force and now - _last_attempt.get(conn.user.id, -RETRY_SECONDS) < RETRY_SECONDS:
            continue
        try:
            if await learn(conn.user):
                learned += 1
        except Exception as e:
            logger.warning(f"Learning the WhatsApp account of user {conn.user.id} failed: {e}")
    return learned


async def account_loop() -> None:
    """Learn missing accounts now, then every :data:`REFRESH_SECONDS` (cheap
    when every connection has one)."""
    while True:
        try:
            await refresh_missing()
        except Exception as e:
            logger.error(f"WhatsApp account refresh failed: {e}", exc_info=True)
        await asyncio.sleep(REFRESH_SECONDS)


def _schedule_learn(user: User) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop (startup import, a script): account_loop learns it
    task = loop.create_task(learn(user))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def on_connect(user: User) -> None:
    """``Integration(on_connect=)``: learn the new connection's account in the background."""
    _last_attempt.pop(user.id, None)
    _schedule_learn(user)


def on_credentials_change(user: User) -> None:
    """``Integration(on_credentials_change=)``: the stored account may be
    another one now: forget it (and any conflict) and learn it again."""
    _last_attempt.pop(user.id, None)
    conn = store.get(user, KIND)
    if conn is not None and (conn.account or conn.account_conflict):
        store.clear_account(user, KIND)
    _schedule_learn(user)


def validate(user: User, changes: Mapping[str, Any], credentials: Mapping[str, Any], settings: Mapping[str, Any],
             by: Optional[User]) -> None:
    """``Integration(validate=)`` of ``whatsapp``: a user without
    ``manage_users`` cannot connect a WAHA session another user's connection
    already uses on the same WAHA (a WAHA API key opens every session of its
    instance, and the session's account routes its inbound voice notes;
    docs/access.md, "Security rules").

    Raises:
        InvalidValue: the session is taken.
    """
    if store.admin_change(by) or not ({"url", "session"} & set(changes)):
        return
    try:
        mine = addresses.origin(str(credentials.get("url") or ""))
    except ValueError:
        return  # the address itself is refused by its own rule
    wanted = str(credentials.get("session") or DEFAULT_SESSION)
    for conn in connections():
        if conn.user.id == user.id or session(conn) != wanted:
            continue
        try:
            same = addresses.origin(base_url(conn)) == mine
        except ValueError:
            continue
        if same:
            raise InvalidValue("session", f"The WAHA session {wanted!r} is already connected by another user; "
                                          "use your own session.")


def _matching(event_me: str) -> Optional[Connection]:
    key = account_key(event_me)
    found = [c for c in connections() if c.account and account_key(c.account) == key]
    if len(found) > 1:  # only possible for an account set by hand (store.update): nobody gets it
        logger.error(f"{len(found)} WhatsApp connections hold the same account; the event is dropped")
        return None
    return found[0] if found else None


async def route(data: Dict[str, Any]) -> Optional[Connection]:
    """The connection a WAHA webhook body is for: the one holding the
    event's ``me.id`` account (after one refresh of the connections with no
    account when none does); without ``me``, the one connection without an
    account conflict using the event's ``session`` (none when several or none
    do). None: drop it (logged here, without the message)."""
    me = data.get("me") if isinstance(data.get("me"), dict) else {}
    event_me = str(me.get("id") or "").strip()
    sess = str(data.get("session") or "").strip()
    event = str(data.get("event") or "").strip()
    if event_me and account_key(event_me):
        found = _matching(event_me)
        if found is None and await refresh_missing():
            found = _matching(event_me)
        if found is None:
            logger.warning(f"WAHA event '{event}' (session '{sess}') for a WhatsApp account no user has; dropped")
        return found
    candidates = [c for c in connections() if session(c) == sess and not c.account_conflict] if sess else []
    if len(candidates) == 1:
        return candidates[0]
    logger.warning(f"WAHA event '{event}' without me for session '{sess}': {len(candidates)} connections use "
                   "that session; dropped")
    return None
