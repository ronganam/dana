"""The dashboard widgets every user has (docs/dashboard.md): the clock with
the weather (through the ``weather`` provider, when an addon of the user's
gives one) and Now (what needs the user and the household). Drawn by
``web/widgets.js``."""

import asyncio
import inspect
import logging
from typing import Any, Dict, Optional

from core import now as now_screen
from core.addons import WidgetContext, registry
from core.users import User
from addons.essentials.addon import addon

logger = logging.getLogger(__name__)

# How many Now items each size lists.
NOW_ITEMS = {"small": 3, "medium": 5, "large": 8}


async def _weather(user: User) -> Optional[Dict[str, Any]]:
    hook = registry.provider("weather", user)
    if hook is None:
        return None
    try:
        if inspect.iscoroutinefunction(hook.func):
            return await hook.func(user)
        return await asyncio.to_thread(hook.func, user)
    except Exception as e:
        logger.debug(f"Clock widget weather failed: {e}")
        return None


@addon.widget("clock", title="Clock and weather", script="widgets.js", icon="clock", refresh=900,
              sizes=("medium", "small", "large"),
              description="The time and date in large type, with the weather when one of your add-ons gives it")
async def clock_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The weather (``condition``, ``temperature``, ``unit``, ``high``, ``low``,
    ``rain_chance``) or None; the page draws the time from its own clock."""
    return {"weather": await _weather(user)}


@addon.widget("now", title="Now", script="widgets.js", icon="inbox", refresh=60,
              description="What needs you: reminders that fired, approvals, suggestions")
async def now_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The top of the user's Now screen: its lede, the first items and how many there are."""
    screen = await now_screen.build(user)
    items = screen.get("items") or []
    limit = NOW_ITEMS.get(ctx.size, 5)
    return {"lede": screen.get("lede"), "total": len(items),
            "items": [{"id": i.get("id"), "kind": i.get("kind"), "title": i.get("title"), "source": i.get("source"),
                       "why": i.get("why") or ""} for i in items[:limit]]}
