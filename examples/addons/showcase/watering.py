"""The ``showcase`` live activity (a module the manifest names in ``modules=``): "water now"
starts a short watering timer on the user's phone, ended by its ``live_refresh`` once the
countdown is over, or early by its "Watered" button (``live_action``).

Dana keeps it current on the clients (server/docs/live.md); the addon only says what it shows.
"""

import datetime

from core import clock, live
from core.addons import Connection
from core.users import User
from addons.showcase import store
from addons.showcase.addon import addon

# --- reference: live ---
@addon.tool(is_write=True)
async def showcase_water_now(conn: Connection, plant_id: int, minutes: int = 2) -> str:
    """Start watering a plant now: a timer on the user's phone until it is done.

    Args:
        plant_id: The plant's id, from showcase_plants.
        minutes: How long the watering takes.
    """
    p = store.plant(conn.user, plant_id)
    if p is None:
        return "Error: no such plant."
    done = clock.now() + datetime.timedelta(minutes=max(1, min(minutes, 30)))
    await live.start(conn.user, addon, f"water:{plant_id}", live.Activity(
        title=f"Watering {p['name']}", icon="droplets", tone="good", countdown=live.Countdown(done, "Done in"),
        actions=(live.Action("watered", "Watered"), live.Action("stop", "Stop", kind="stop")),
        open="/plants", refresh_at=done, ends_at=done + datetime.timedelta(minutes=10)))
    return f"Watering {p['name']} until {done:%H:%M}."


def _plant_id(current: live.Current) -> int:
    return int(current.key.split(":", 1)[1])


@addon.live_refresh(every=60)
async def watering_done(user: User, current: live.Current) -> live.HookResult:
    """Due at the activity's refresh_at (the end of the countdown): the plant counts as watered, it ends."""
    countdown = current.activity.countdown
    if countdown is not None and clock.now() >= countdown.to:
        store.water(user, _plant_id(current), clock.now())
        return live.END
    return None                                  # unchanged


@addon.live_action
async def watering_action(user: User, current: live.Current, action: str, choice: str) -> live.HookResult:
    """"Watered" ends it early (watered now); after "stop" (Dana already ended it) nothing is left to clean up."""
    if action == "watered":
        store.water(user, _plant_id(current), clock.now())
        return live.END
    return None
# --- end ---
