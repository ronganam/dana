"""Adding, fixing and removing a user's bank / card logins (docs/money.md, "Sources").

A source is one login at one company of a provider addon the user has
(``providers.py``). Its credentials are checked against the company's form,
stored encrypted (``CREDENTIALS_KEY``) and decrypted only by ``sync.py``;
nothing returns or logs them. A new source, and a source given new
credentials, syncs at once (``trigger="connect"``); new credentials also clear
``needs_attention`` and the source's Reconnect (``store.reconnected``). Removing a source asks its provider to forget what it
keeps for it (``Company.forget``: browser profiles)."""

import asyncio
import logging
import uuid
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

from core.users import User
from core.addons import crypto, store as connections
from addons.money import config, otp, providers, schedule, store, sync

logger = logging.getLogger(__name__)

MAX_LABEL = 60
MAX_VALUE = 200

_tasks: Set["asyncio.Task[Any]"] = set()


class InvalidCredentials(ValueError):
    """A login field missing or malformed (``field`` names it; the API answers 400)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _checked(company: providers.Company, credentials: Any) -> Dict[str, str]:
    if not isinstance(credentials, Mapping):
        raise InvalidCredentials("credentials", "credentials must be an object")
    names = {f.name for f in company.fields}
    unknown = sorted(set(credentials) - names)
    if unknown:
        raise InvalidCredentials(unknown[0], f"{company.name} has no field {unknown[0]!r}")
    out: Dict[str, str] = {}
    for f in company.fields:
        value = credentials.get(f.name)
        if not isinstance(value, str) or not value.strip():
            raise InvalidCredentials(f.name, f"{f.label} is required")
        if len(value) > MAX_VALUE:
            raise InvalidCredentials(f.name, f"{f.label} is too long")
        out[f.name] = value if f.secret else value.strip()
    return out


def _label(label: Optional[str]) -> Optional[str]:
    text = (label or "").strip()[:MAX_LABEL]
    return text or None


def _sync_at_once(user: User, source_id: str) -> None:
    """Start the source's sync now when an event loop runs (else the loop finds it due)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        schedule.sync_now(user, source_id, "connect")
    except Exception as e:
        logger.warning(f"Money source {source_id}: the first sync did not start: {e}")


def add(user: User, company: str, credentials: Mapping[str, Any], label: Optional[str] = None) -> Dict[str, Any]:
    """Add a login for the user and start its first sync; returns its payload.

    Raises:
        UnknownCompany: no loaded provider brings the company.
        NotConnected: the company's provider addon is not on for the user.
        InvalidCredentials: a field missing or malformed.
        CredentialsKeyError: no usable ``CREDENTIALS_KEY``.
    """
    c = providers.get(user, company)
    token = crypto.encrypt(_checked(c, credentials))
    source_id = uuid.uuid4().hex
    now = store.now_iso()
    with store.db() as conn:
        conn.execute("INSERT INTO money_sources (id, user_id, company, label, credentials, state, next_sync_at, "
                     "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?)",
                     (source_id, user.id, c.id, _label(label), token, now, now, now))
    logger.info(f"Money source {source_id} ({c.id}) added for user {user.id}")
    _sync_at_once(user, source_id)
    return store.source_payload(user, store.get_source(user, source_id) or {})


def update(user: User, source_id: str, *, label: Optional[str] = None,
           credentials: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Change a source's label and / or credentials (every field of the
    company's form); new credentials make it ``new`` again and sync it at once.
    Returns its payload.

    Raises:
        LookupError: no such source of the user.
        UnknownCompany / NotConnected: new credentials for a company the user cannot use now.
        InvalidCredentials: a field missing or malformed.
        CredentialsKeyError: no usable ``CREDENTIALS_KEY``.
    """
    source = store.get_source(user, source_id)
    if source is None:
        raise LookupError(f"unknown money source {source_id!r}")
    now = store.now_iso()
    with store.db() as conn:
        if label is not None:
            conn.execute("UPDATE money_sources SET label = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                         (_label(label), now, source_id, user.id))
        if credentials is not None:
            token = crypto.encrypt(_checked(providers.get(user, source["company"]), credentials))
            conn.execute("UPDATE money_sources SET credentials = ?, state = 'new', state_reason = NULL, "
                         "error_type = NULL, failures = 0, next_sync_at = ?, updated_at = ? "
                         "WHERE id = ? AND user_id = ?", (token, now, now, source_id, user.id))
    if credentials is not None:
        logger.info(f"Money source {source_id} of user {user.id}: new login details")
        connections.reconnected(user, config.ADDON, source_id)
        _sync_at_once(user, source_id)
    return store.source_payload(user, store.get_source(user, source_id) or {})


async def remove(user: User, source_id: str, delete_transactions: bool = False) -> bool:
    """Remove a source: its running sync stops, its provider forgets it (its
    browser profile, best-effort) and, with ``delete_transactions``, its
    accounts and their transactions go; otherwise its accounts stay (without a
    source). False when no such source of the user."""
    source = store.get_source(user, source_id)
    if source is None:
        return False
    await sync.cancel(source_id)
    otp.cancel_for_source(user, source_id)
    with store.db() as conn:
        if delete_transactions:
            conn.execute("DELETE FROM money_accounts WHERE user_id = ? AND source_id = ?", (user.id, source_id))
        conn.execute("DELETE FROM money_sources WHERE id = ? AND user_id = ?", (source_id, user.id))
    connections.reconnected(user, config.ADDON, source_id)
    logger.info(f"Money source {source_id} of user {user.id} removed"
                + (" with its transactions" if delete_transactions else ""))
    await _provider_forget({source["company"]: [source_id]})
    return True


async def _provider_forget(by_company: Mapping[str, List[str]]) -> None:
    """Ask each company's provider to forget its removed sources (best-effort)."""
    for company_id, source_ids in by_company.items():
        company = providers.company(company_id)
        if company is None or company.forget is None:
            continue
        try:
            await company.forget(list(source_ids))
        except Exception as e:
            logger.warning(f"Money: {company_id} could not forget {len(source_ids)} source(s): {e}")


async def _forget(by_company: Mapping[str, List[str]]) -> None:
    for source_ids in by_company.values():
        for source_id in source_ids:
            await sync.cancel(source_id)
    await _provider_forget(by_company)


def _in_background(by_company: Mapping[str, List[str]]) -> None:
    """Stop the sources' syncs and let their providers forget them, when an event loop runs."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_forget(by_company))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _grouped(sources: Iterable[Mapping[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = defaultdict(list)
    for s in sources:
        out[s["company"]].append(s["id"])
    return dict(out)


def on_change(user: User) -> None:
    """``Addon(on_change=)``: once ``money`` is disconnected, delete all of
    the user's money data and ask the worker to delete their browser profiles."""
    if connections.is_connected(user, config.ADDON):
        return
    removed = store.delete_all(user)
    logger.info(f"Money disconnected for user {user.id}: {len(removed)} source(s) and their data deleted")
    if removed:
        _in_background(_grouped(removed))


def provider_changed(user: User, addon: str) -> None:
    """For a provider's ``Addon(on_change=)``: once ``addon`` is off for the
    user, their logins at its companies are removed (credentials, device keys,
    code requests; their running syncs stop and the provider forgets them).
    Their accounts and transactions stay, without a source."""
    if connections.is_connected(user, addon):
        return
    gone = [s for s in store.list_sources(user)
            if (c := providers.company(s["company"])) is not None and c.addon == addon]
    if not gone:
        return
    with store.db() as conn:
        for s in gone:
            conn.execute("UPDATE money_code_requests SET status = 'cancelled' WHERE user_id = ? AND source_id = ? "
                         "AND status = 'pending'", (user.id, s["id"]))
            conn.execute("DELETE FROM money_sources WHERE id = ? AND user_id = ?", (s["id"], user.id))
    for s in gone:
        connections.reconnected(user, config.ADDON, s["id"])
    logger.info(f"{addon} turned off for user {user.id}: {len(gone)} money source(s) removed")
    _in_background(_grouped(gone))
