"""The ``showcase`` background work: a daily reminder job, a service, an event handler and a notify channel.

Jobs, handlers and channels run for (and as) one user who has the addon; the
service runs once for the server.
"""

import asyncio
from typing import Any, Dict

from core import clock, events, notify
from core.addons import hours
from core.users import User
from addons.showcase import store
from addons.showcase.addon import addon

# --- reference: job ---
@addon.job(daily="09:00")
async def remind(user: User) -> int:
    """Each morning: tell the user which plants need water (an event other addons may react to)."""
    due = store.due(user, clock.now())
    if due:
        events.emit("showcase.plants.thirsty", user, names=[p["name"] for p in due])
        await notify.send(user, notify.Message(text=f"Water today: {', '.join(p['name'] for p in due)}",
                                               kind="showcase", data={"count": len(due)}))
    return len(due)
# --- end ---


# --- reference: service ---
TICKS = {"count": 0}


@addon.service
async def tick(app: Any) -> None:
    """Once per server, for as long as it runs (restarted with backoff if it ends): counts hours."""
    while True:
        TICKS["count"] += 1
        await asyncio.sleep(hours(1).total_seconds())
# --- end ---


# --- reference: event ---
@addon.on("tasks.task.created")
async def task_created(user: User, data: Dict[str, Any]) -> None:
    """A task titled "Water <name>" adds that plant, when the user does not have it yet."""
    title = str(data.get("title") or "")
    name = title[len("Water "):].strip() if title.startswith("Water ") else ""
    if name and all(p["name"].lower() != name.lower() for p in store.plants(user)):
        store.add_plant(user, name, every_days=3)
# --- end ---


# --- reference: channel ---
@addon.channel("showcase")
async def keep_message(user: User, message: notify.Message) -> bool:
    """A delivery channel of notify.send: keeps every message for the Plants page; True: delivered."""
    if not message.text:
        return False
    store.keep(user, message.text)
    return True
# --- end ---
