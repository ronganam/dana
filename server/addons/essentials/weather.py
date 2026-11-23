"""The weather at each user's Home zone from Open-Meteo (free, no key; docs/signals.md).

``GET {OPEN_METEO_URL}?latitude=&longitude=&current=temperature_2m,weather_code,is_day
&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max
&timezone=auto&forecast_days=1``. The answer is the ``weather`` provider's
shape: ``{condition, temperature, unit, high, low, rain_chance}``, the
condition one of Home Assistant's weather words (:data:`WMO_CONDITIONS`), which
the clients draw icons for. Cached
per place (the zone's rounded coordinates) for :data:`CACHE_SECONDS`, a failure
for :data:`FAIL_SECONDS`. ``OPEN_METEO_URL``: another Open-Meteo server (it can
run on your own); only the coordinates of Home zones are ever sent.
"""

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from core import zones
from core.users import User

logger = logging.getLogger(__name__)

URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
CACHE_SECONDS = 30 * 60
FAIL_SECONDS = 5 * 60
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
# The transport requests go through (tests set an httpx.MockTransport; None: the network).
transport: Optional[httpx.AsyncBaseTransport] = None

# WMO weather interpretation codes → Home Assistant's condition words ("-" as a space).
WMO_CONDITIONS: Dict[int, str] = {
    0: "sunny", 1: "partlycloudy", 2: "partlycloudy", 3: "cloudy", 45: "fog", 48: "fog",
    51: "rainy", 53: "rainy", 55: "rainy", 56: "snowy rainy", 57: "snowy rainy",
    61: "rainy", 63: "rainy", 65: "pouring", 66: "snowy rainy", 67: "snowy rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy", 80: "rainy", 81: "rainy", 82: "pouring",
    85: "snowy", 86: "snowy", 95: "lightning rainy", 96: "hail", 99: "hail",
}

# (rounded latitude, longitude) → (monotonic time, answer or None)
_cache: Dict[Tuple[float, float], Tuple[float, Optional[Dict[str, Any]]]] = {}


def condition(code: Any, is_day: Any = 1) -> str:
    """The condition word of WMO ``code`` ("clear night" for a clear sky at night)."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return "exceptional"
    if c == 0 and not is_day:
        return "clear night"
    return WMO_CONDITIONS.get(c, "exceptional")


def _first(values: Any) -> Any:
    return values[0] if isinstance(values, list) and values else None


def parse(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Open-Meteo's answer as the weather provider's shape (None without a current temperature)."""
    cur = data.get("current") or {}
    daily = data.get("daily") or {}
    if cur.get("temperature_2m") is None:
        return None
    unit = (data.get("current_units") or {}).get("temperature_2m") or "°C"
    return {"condition": condition(cur.get("weather_code"), cur.get("is_day", 1)),
            "temperature": cur.get("temperature_2m"), "unit": unit,
            "high": _first(daily.get("temperature_2m_max")), "low": _first(daily.get("temperature_2m_min")),
            "rain_chance": _first(daily.get("precipitation_probability_max"))}


async def fetch(latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
    """Today's weather at a place (cached); None when Open-Meteo does not answer."""
    key = (round(latitude, 2), round(longitude, 2))
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < (CACHE_SECONDS if hit[1] is not None else FAIL_SECONDS):
        return hit[1]
    params = {"latitude": key[0], "longitude": key[1], "current": "temperature_2m,weather_code,is_day",
              "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
              "timezone": "auto", "forecast_days": 1}
    value: Optional[Dict[str, Any]] = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport, follow_redirects=False) as client:
            r = await client.get(URL, params=params)
        if r.status_code == 200:
            value = parse(r.json())
        else:
            logger.warning(f"Open-Meteo answered {r.status_code}")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"Open-Meteo request failed ({type(e).__name__})")
    _cache[key] = (now, value)
    return value


async def for_user(user: User) -> Optional[Dict[str, Any]]:
    """The weather at ``user``'s Home zone (None without one or when Open-Meteo fails)."""
    home = zones.home(user)
    if home is None:
        return None
    return await fetch(home.latitude, home.longitude)


def clear_cache() -> None:
    """Forget every cached answer (tests)."""
    _cache.clear()
