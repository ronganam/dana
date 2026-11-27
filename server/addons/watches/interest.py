"""Interest hints (docs/watches.md, "Interest"): which values of a field the
user's active watches can fire on, told to ``core.events.set_interest`` so an
event source can skip producing what nobody wants (Home Assistant subscribes to
just the entities watches name).

For each pair in :data:`HINTS`, :func:`spec.pinned_values` over the user's
active watches: the union of the values their ``eq`` / ``in`` conditions on the
field pin, None (no narrowing) when any trigger on that type pins none or a
reminder takes the type as evidence, ``[]`` when none listens. Sent after
every change to the user's watches (create, change, pause, resume, delete,
expire, restore: ``store.on_change``) and at start-up (``hooks.hint_interest``).
A user without the addon gets None. Works before ``core.events`` has the API
(nothing is sent).
"""

import logging
from typing import Dict, List, Optional, Tuple

from core import events
from core.addons import registry
from core.users import User
from addons.watches import spec as S
from addons.watches import store
from addons.watches.addon import ADDON

logger = logging.getLogger(__name__)

# (event type, field) pairs whose values watches pin.
HINTS: Tuple[Tuple[str, str], ...] = (("homeassistant.state.changed", "entity_id"),)


def compute(user: User) -> Dict[Tuple[str, str], Optional[List[str]]]:
    """Per hint pair, the values ``user``'s active watches can fire on (None: anything; []: nothing)."""
    if ADDON not in registry.active_addons(user):
        return {pair: None for pair in HINTS}
    rows = store.list_for(user, [store.ACTIVE])
    return {(et, f): S.pinned_values(rows, et, f) for et, f in HINTS}


def update(user: User) -> None:
    """Send ``user``'s hints to ``core.events`` (never raises)."""
    setter = getattr(events, "set_interest", None)
    if setter is None:
        return
    try:
        for (et, f), values in compute(user).items():
            setter(user.id, et, f, values)
    except Exception:
        logger.exception(f"Watch interest hints not sent for user {user.id}")


store.on_change(update)
