"""The ``phone`` addon's name, its users, and the self-check marks it writes.

Background work of the addon (notification retention, digest, re-nudges,
triage resumed at startup) runs once per user who has ``phone`` connected, as
that user, one user's failure not stopping the others: :func:`for_each_user`.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Awaitable, Callable, Dict, List

from core import users
from core.db import get_db_connection
from core.users import User
from core.addons import store

logger = logging.getLogger(__name__)

ADDON = "phone"


def phone_users() -> List[User]:
    """Every user with ``phone`` connected (stable order)."""
    out = []
    for uid in store.users_with(ADDON):
        user = users.get(uid)
        if user is not None:
            out.append(user)
    return out


async def for_each_user(label: str, job: Callable[[User], Awaitable[Any]]) -> Dict[str, Any]:
    """Run ``job(user)`` for every user with ``phone``, each as that user, one
    after another. Returns user id → result (a user whose job raised is left
    out and logged; the next one still runs)."""
    out: Dict[str, Any] = {}
    for user in phone_users():
        try:
            with users.acting_as(user):
                out[user.id] = await job(user)
        except Exception:
            logger.exception(f"{label} for user {user.id} failed")
    return out


def mark_key(name: str, user_id: str) -> str:
    """The ``system_marks`` key of the user's ``push`` / ``notification_sync`` mark."""
    return f"{name}:{user_id}"


def mark(name: str, user_id: str, ok: bool = True, detail: str = "") -> None:
    """Record that the user's signal ``name`` arrived (``ok``) or an attempt
    failed (read by core/checks.py). Never raises."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (mark_key(name, user_id), now, 1 if ok else 0, now if ok else None, None if ok else now,
                 (detail or "")[:300]))
            conn.commit()
    except Exception as e:
        logger.debug(f"Phone mark {name} for {user_id} not written: {e}")
