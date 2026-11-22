"""Addons an admin disabled on this server, for everyone (table ``addon_state``;
../docs/addons.md, "Where addons come from", level 2).

A disabled addon stays loaded (its tables, its users' connections and their
settings are kept) but offers nothing: :func:`core.addons.store.connected_addons`
leaves it out for every user, so its tools, prompt parts, prompt section,
event handlers, notify channels, composer hooks and jobs are gone; its
services are stopped (core/addons/scheduler.py), its routes answer 404
``addon_disabled`` (core/server.py) and its web files are not served
(core/addons_api.py). An addon that ``depends`` on a disabled addon is
disabled with it. ``everyone`` addons (``essentials``) cannot be disabled.

The set is kept in memory and read again whenever the database changed since
the last lookup (:class:`core.db.ChangeWatch`), so a change made here or by
another process (``bin/dana addons disable``) applies to the next lookup.
"""

import datetime
import logging
import sqlite3
from typing import FrozenSet, Optional, Tuple

from core.db import ChangeWatch, get_db_connection
from core.users import User
from core.addons.addon import all_addons, get_addon, version

logger = logging.getLogger(__name__)

# (addon registry version, explicitly disabled, disabled with what they need)
_cache: Optional[Tuple[int, FrozenSet[str], FrozenSet[str]]] = None
_watch = ChangeWatch()


class CannotDisable(ValueError):
    """The addon cannot be disabled (every user has it)."""


class AddonDisabled(Exception):
    """A change to a user's connection of an addon that is disabled on the
    server: refused until an admin enables it (the API answers 409
    ``{"error": "addon_disabled", "addon": name}``)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"The {name} addon is disabled on this server; an admin can enable it.")
        self.addon = name


def _read() -> FrozenSet[str]:
    try:
        with get_db_connection() as conn:
            return frozenset(r["addon"] for r in conn.execute("SELECT addon FROM addon_state WHERE disabled = 1"))
    except sqlite3.OperationalError as e:  # a database that is not migrated yet: nothing is disabled
        logger.debug(f"addon_state not readable: {e}")
        return frozenset()


def _with_dependents(explicit: FrozenSet[str]) -> FrozenSet[str]:
    """``explicit`` plus every addon that ``depends`` (transitively) on one of them."""
    out = set(explicit)
    changed = True
    while changed:
        changed = False
        for a in all_addons():
            if a.name not in out and not a.everyone and any(n in out for n in a.depends):
                out.add(a.name)
                changed = True
    return frozenset(out)


def _current() -> Tuple[FrozenSet[str], FrozenSet[str]]:
    global _cache
    c = _cache
    if _watch.changed() or c is None or c[0] != version():
        explicit = _read()
        c = _cache = (version(), explicit, _with_dependents(explicit))
    return c[1], c[2]


def invalidate() -> None:
    """Read the table again on the next lookup."""
    global _cache
    _cache = None


def disabled() -> FrozenSet[str]:
    """Every disabled addon: disabled by an admin, or needing one that is."""
    return _current()[1]


def disabled_by_admin() -> FrozenSet[str]:
    """The addons an admin disabled themselves (not those disabled through ``depends``)."""
    return _current()[0]


def is_enabled(name: str) -> bool:
    """Addon ``name`` is not disabled on this server."""
    return name not in _current()[1]


def set_disabled(name: str, value: bool, *, by: Optional[User] = None) -> None:
    """Disable (or enable again) addon ``name`` for everyone; ``by``: the admin
    (None: the server's CLI).

    Raises:
        KeyError: no addon of that name is loaded.
        CannotDisable: every user has it (``everyone=True``).
    """
    addon = get_addon(name)
    if value and addon.everyone:
        raise CannotDisable(f"The {name} addon cannot be disabled: every user has it.")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        conn.execute("INSERT INTO addon_state (addon, disabled, changed_by, changed_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(addon) DO UPDATE SET disabled = excluded.disabled, "
                     "changed_by = excluded.changed_by, changed_at = excluded.changed_at",
                     (name, 1 if value else 0, by.id if by is not None else None, now))
        conn.commit()
    invalidate()
    logger.info(f"Addon {name} {'disabled' if value else 'enabled'} on this server"
                f" by {by.id if by is not None else 'the CLI'}")
