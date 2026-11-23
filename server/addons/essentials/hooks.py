"""What every user gets from the essentials addon beyond its tools:

- the ``weather`` provider (rank 10, source ``open_meteo``): Open-Meteo at
  their Home zone (addons/essentials/weather.py); and the
  ``signal.weather`` source for it (docs/signals.md);
- the morning briefing's ``weather`` section, from whichever weather
  provider answers first (``core.briefing.weather``);
- the dashboard widgets every user has (docs/dashboard.md): the clock with
  the weather, and Now (what needs the user and the household), drawn by
  ``web/widgets.js``.
"""

from typing import Any, Dict, Optional

from core import briefing, now as now_screen, zones
from core.addons import BriefingContext, Section, WidgetContext
from core.users import User
from addons.essentials import weather
from addons.essentials.addon import addon

OPEN_METEO = {"rank": 10, "source": "open_meteo", "label": "Open-Meteo"}

# How many Now items each widget size lists.
NOW_ITEMS = {"small": 3, "medium": 5, "large": 8}


@addon.provide("weather", **OPEN_METEO)
async def open_meteo_weather(user: User) -> Optional[Dict[str, Any]]:
    """Today's weather at ``user``'s Home zone (None without one or when Open-Meteo fails)."""
    return await weather.for_user(user)


@addon.provide("signal.weather", **OPEN_METEO)
def open_meteo_source(user: User) -> str:
    """What Open-Meteo needs before it can tell ``user``'s weather ("" when nothing)."""
    return "" if zones.home(user) is not None else "Set your Home zone (Settings → Zones)"


@addon.briefing("weather", default=None, rank=10)
async def briefing_weather(user: User, ctx: BriefingContext) -> Section:
    w = await briefing.weather(user)
    points = []
    if w and w.get("temperature") is not None:
        hi = f", high {w['high']}°" if w.get("high") is not None else ""
        points.append(f"{w['condition'].capitalize()}, {w['temperature']}°{hi}")
    return Section(w, points=points)


@addon.widget("clock", title="Clock and weather", script="widgets.js", icon="clock", refresh=900,
              sizes=("medium", "small", "large"),
              description="The time and date in large type, with the weather at your Home zone")
async def clock_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The weather (``condition``, ``temperature``, ``unit``, ``high``, ``low``,
    ``rain_chance``) or None; the page draws the time from its own clock."""
    return {"weather": await briefing.weather(user)}


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
