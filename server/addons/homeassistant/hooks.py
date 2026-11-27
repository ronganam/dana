"""The Home Assistant addon's background work (../docs/addons.md, "The
manifest"): at start, every user's home map fetched in the background for
their prompt; and the service that follows each user's Home Assistant state
changes as ``homeassistant.state.changed`` events (:mod:`.states`)."""

from typing import Any

from core.addons import store
from core.users import User
from addons.homeassistant import home_map, states
from addons.homeassistant.addon import addon
from addons.homeassistant.config import ADDON


@addon.job(at_start=True)
async def warm_home_map(user: User) -> None:
    """Fetch ``user``'s home map into the prompt cache."""
    conn = store.get(user, ADDON)
    task = home_map.warm_home_map(conn) if conn is not None else None
    if task is not None:
        await task


@addon.service
async def follow_state_changes(app: Any) -> None:
    """One WebSocket subscription per user connection, to the entities wanted (:func:`states.run`)."""
    await states.run(app)
