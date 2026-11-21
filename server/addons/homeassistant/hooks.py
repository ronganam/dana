"""The Home Assistant addon's background work (../docs/addons.md, "The manifest").

- the entity watchers (a service): one per user with a person entity or a
  car sensor set, over their own connection; each transition is emitted as
  the event ``homeassistant.location.changed`` (``action``: ``arrive`` /
  ``leave``, ``target``: the zone) or ``homeassistant.car.changed``
  (``action``: ``enter`` / ``leave``) for that user;
- at start: every user's home map fetched in the background for their prompt
  (Home Assistant being down only logs a warning).
"""

from typing import Any

from core import events
from core.users import User
from core.addons import store
from addons.homeassistant import home_map
from addons.homeassistant.addon import addon
from addons.homeassistant.config import ADDON
from addons.homeassistant.watcher import Watchers


async def emit_transition(user: User, trigger_type: str, *, action: str = "", target: str = "") -> None:
    """A watcher saw ``user`` arrive / leave a zone or enter / leave the car."""
    events.emit(f"homeassistant.{trigger_type}.changed", user, action=action, target=target)


@addon.service
async def watch_entities(app: Any) -> None:
    """Keep every user's entity watcher running (reconciled with their connections)."""
    await Watchers(emit_transition).run_forever()


@addon.job(at_start=True)
async def warm_home_map(user: User) -> None:
    conn = store.get(user, ADDON)
    task = home_map.warm_home_map(conn) if conn is not None else None
    if task is not None:
        await task
