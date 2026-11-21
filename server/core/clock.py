"""The user's local clock.

The container runs in UTC, but everything user-facing — reminder times, the
date the agent believes it is, WhatsApp/call timestamps — is the user's wall
clock. Always take "now" from :func:`now` instead of ``datetime.now()``.

One timezone for the whole server (a server setting). Resolution (first
match wins, resolved once and cached): ``TIMEZONE`` env var → the first
admin's Home Assistant (``/api/config``, when they have it connected) →
``TZ`` env var → UTC.
"""

import datetime
import logging
import os
import zoneinfo
from typing import Callable, Optional

from core import users
from core.users import User

logger = logging.getLogger(__name__)

_timezone: Optional[zoneinfo.ZoneInfo] = None
# How to ask a user's Home Assistant for its zone: registered by the
# homeassistant kind (integrations/homeassistant/client.py) when it is imported.
_home_assistant_zone: Optional[Callable[[User], Optional[str]]] = None


def set_home_assistant_zone(lookup: Callable[[User], Optional[str]]) -> None:
    """Register how a user's Home Assistant is asked for its timezone."""
    global _home_assistant_zone
    _home_assistant_zone = lookup


def _zone(name: Optional[str], source: str) -> Optional[zoneinfo.ZoneInfo]:
    if not name:
        return None
    try:
        tz = zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
        logger.warning(f"Invalid timezone '{name}' from {source}: {e}")
        return None
    logger.info(f"Using timezone {name} (from {source})")
    return tz


def _home_assistant_timezone() -> Optional[str]:
    # The server's zone is a server setting: it comes from the first admin's
    # Home Assistant on purpose (one timezone for the whole server).
    admin = users.first_admin()
    if admin is None or _home_assistant_zone is None:
        return None
    return _home_assistant_zone(admin)


def get_timezone() -> zoneinfo.ZoneInfo:
    """The user's timezone. The first call may query Home Assistant (blocking),
    so ``main`` resolves it once at startup."""
    global _timezone
    if _timezone is None:
        _timezone = (
            _zone(os.getenv("TIMEZONE"), "TIMEZONE env")
            or _zone(_home_assistant_timezone(), "Home Assistant")
            or _zone(os.getenv("TZ"), "TZ env")
            or zoneinfo.ZoneInfo("UTC")
        )
    return _timezone


def now() -> datetime.datetime:
    """Current time as a timezone-aware datetime in the user's timezone."""
    return datetime.datetime.now(get_timezone())


def from_timestamp(ts: float) -> datetime.datetime:
    """Convert a Unix timestamp to an aware datetime in the user's timezone."""
    return datetime.datetime.fromtimestamp(ts, get_timezone())


def describe_now() -> str:
    """Human-readable current local time for LLM prompts, including the zone."""
    current = now()
    return f"{current.strftime('%A, %B %d, %Y, %I:%M:%S %p')} ({current.tzinfo}, UTC{current.strftime('%z')[:3]}:{current.strftime('%z')[3:]})"
