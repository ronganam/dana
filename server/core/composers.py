"""Who a composer's background work runs for (docs/access.md, "The current user").

Composers (Now, the wake flow, the briefing, meeting cards, the weekly review,
search, the self-checks) combine several addons for one user. Their loops run
once per user who has the addons the work needs, as that user, one after
another; one user's failure is logged and the next one still runs.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List

from core import users
from core.users import User
from core.addons import store

logger = logging.getLogger(__name__)


def has_all(user: User, addons: Iterable[str]) -> bool:
    """Whether ``user`` has every addon in ``addons`` connected."""
    mine = store.connected_addons(user)
    return all(k in mine for k in addons)


def eligible(rule: Callable[[User], bool]) -> List[User]:
    """Every user (oldest first) for whom ``rule`` holds; a rule that raises counts as False."""
    out = []
    for user in users.list_users():
        try:
            if rule(user):
                out.append(user)
        except Exception:
            logger.exception(f"Eligibility check for user {user.id} failed")
    return out


async def for_each(label: str, rule: Callable[[User], bool],
                   job: Callable[[User], Awaitable[Any]]) -> Dict[str, Any]:
    """Run ``job(user)`` for every user for whom ``rule`` holds, each as that
    user, one after another. Returns user id → result (a user whose job
    raised is logged and left out; the next one still runs)."""
    out: Dict[str, Any] = {}
    for user in eligible(rule):
        try:
            with users.acting_as(user):
                out[user.id] = await job(user)
        except Exception:
            logger.exception(f"{label} for user {user.id} failed")
    return out
