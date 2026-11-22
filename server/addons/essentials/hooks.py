"""What every user gets from the essentials addon beyond its tools
(docs/signals.md):

- the ``weather`` provider (rank 10, source ``open_meteo``): Open-Meteo at
  their Home zone (addons/essentials/weather.py), asked before Home
  Assistant's weather entity; and the ``signal.weather`` source for it;
- the morning briefing's ``weather`` section, from whichever weather
  provider answers first (``core.briefing.weather``).
"""

from typing import Any, Dict, Optional

from core import briefing, zones
from core.addons import BriefingContext, Section
from core.users import User
from addons.essentials import weather
from addons.essentials.addon import addon

OPEN_METEO = {"rank": 10, "source": "open_meteo", "label": "Open-Meteo"}


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
