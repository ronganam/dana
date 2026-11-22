"""The demo household: made-up people with sample data, loaded by an admin
to see Dana filled in, removed in one go (docs/household.md).

The demo is two **demo users** (``users.create(..., demo=True)``: members,
marked in ``system_marks[users.DEMO_MARK]``), each given the default addons
and then every addon's ``@addon.demo`` sample data (people, calls,
notifications, tasks, shopping, money, … : the hooks write only rows that
belong to the user). Nothing of a real user is touched, and the demo users
are invisible to everything that walks the household (``users.list_users``
leaves them out: background jobs, composers, sharing targets, the household
page), so they cost nothing and reach no one.

Removing it deletes the demo users: every per-user table cascades from
``users`` (``user_id ... ON DELETE CASCADE``), so exactly their rows go;
their usage rows (kept for a removed real user, as server cost history) are
deleted too, and the mark is cleared. Loading twice changes nothing.

An admin views the demo as one of its users (``POST /api/demo/view``,
core/demo_api.py; the auth middleware swaps the request's user while the
``dana_demo_view`` cookie names a demo user and the caller may manage users).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from core import clock, users
from core.addons import registry, state, store
from core.addons.addon import all_addons
from core.addons.hooks import DemoContext
from core.db import get_db_connection
from core.user_rows import Profile
from core.users import User

logger = logging.getLogger(__name__)

# Who lives in the demo household (made up), the first is the one an admin views first.
PEOPLE: Tuple[Tuple[str, Profile], ...] = (
    ("Sam Carter", Profile(pronoun="they", prompt_name="Sam",
                           context="Sam is a sample person in Dana's demo household. Sam lives with Jamie and "
                                   "their dog Biscuit, works as a designer and cycles to work.",
                           languages="en", currency="USD")),
    ("Jamie Carter", Profile(pronoun="she", prompt_name="Jamie",
                             context="Jamie is a sample person in Dana's demo household. Jamie lives with Sam "
                                     "and teaches music.",
                             languages="en", currency="USD")),
)


@dataclass(frozen=True)
class Status:
    """Whether the demo is loaded, its users and the rows each addon wrote (on load)."""
    loaded: bool
    users: Tuple[User, ...]
    rows: Dict[str, int]

    def to_json(self) -> Dict[str, Any]:
        return {"loaded": self.loaded, "users": [{"id": u.id, "name": u.name} for u in self.users],
                "rows": self.rows}


def status() -> Status:
    """Whether the demo household is loaded now."""
    found = tuple(users.demo_users())
    return Status(loaded=bool(found), users=found, rows={})


def _connect(user: User) -> None:
    """Turn on, for a demo user, the default addons and every enabled addon
    with demo data that needs no credentials."""
    store.connect_defaults(user)
    for addon in all_addons():
        if (not any(h.kind == "demo" for h in addon.hooks) or addon.everyone or not addon.allowed_for(user)
                or store.is_connected(user, addon.name) or not state.is_enabled(addon.name)
                or any(f.required for f in addon.credentials)):
            continue
        store.connect(user, addon.name)


def _fill(user: User, ctx: DemoContext) -> Dict[str, int]:
    """Give ``user`` their addons and every enabled addon's sample data."""
    _connect(user)
    rows: Dict[str, int] = {}
    with users.acting_as(user):
        for hook in registry.hooks("demo", user):
            try:
                rows[hook.addon] = rows.get(hook.addon, 0) + int(hook.func(user, ctx) or 0)
            except Exception:
                logger.exception(f"The {hook.addon} addon's demo data failed for demo user {user.id}")
    return rows


def _load() -> Status:
    existing = users.demo_users()
    if existing:
        return Status(loaded=True, users=tuple(existing), rows={})
    made = [users.create(name, demo=True, profile=profile) for name, profile in PEOPLE]
    now = clock.now()
    rows: Dict[str, int] = {}
    for i, user in enumerate(made):
        for addon, n in _fill(user, DemoContext(now=now, household=tuple(made), index=i)).items():
            rows[addon] = rows.get(addon, 0) + n
    logger.info(f"Demo household loaded: {', '.join(u.name for u in made)} "
                f"({', '.join(f'{k} {v}' for k, v in sorted(rows.items())) or 'no rows'})")
    return Status(loaded=True, users=tuple(made), rows=rows)


_lock = asyncio.Lock()


async def load() -> Status:
    """Load the demo household (nothing happens when it is already loaded)."""
    async with _lock:
        return await asyncio.to_thread(_load)


def _remove() -> List[str]:
    ids = [u.id for u in users.demo_users()]
    with get_db_connection() as conn:
        for user_id in ids:
            # scoped: only the demo users' own rows (their ids come from the demo mark)
            conn.execute("DELETE FROM usage_events WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM usage_daily WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        users.write_demo_ids(conn, [])
        conn.commit()
    users.invalidate()
    store.invalidate()
    if ids:
        logger.info(f"Demo household removed ({len(ids)} users)")
    return ids


async def remove() -> List[str]:
    """Remove the demo household and everything it owns. Returns the removed user ids."""
    async with _lock:
        return await asyncio.to_thread(_remove)


def is_demo_user(user_id: str) -> bool:
    """Whether ``user_id`` is one of the demo household's users."""
    found = users.get(user_id)
    return bool(found and found.demo)
