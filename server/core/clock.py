"""The user's local clock.

The container runs in UTC, but everything user-facing — reminder times, the
date the agent believes it is, WhatsApp/call timestamps — is the user's wall
clock. Always take "now" from :func:`now` instead of ``datetime.now()``.

One timezone for the whole server (a server setting). Resolution (first
match wins, resolved once and cached): ``TIMEZONE`` env var → the one an
admin chose in the app (the wizard's Home step: ``server_settings['timezone']``,
:func:`set_server_timezone`) → the first admin's Home Assistant
(``/api/config``, when they have it connected) → ``TZ`` env var → UTC.

The week (money's "this week", the weekly review's week) starts on one day
for the whole server too (:func:`week_start_day`): ``WEEK_START`` ("mon",
"sun", …), else the day after the weekly review's day (``WEEKLY_REVIEW_AT``,
default "sat 20:00": the review closes the week, so weeks start on Sunday).
"""

import datetime
import logging
import os
import zoneinfo
from typing import Callable, Optional

from core import server_settings, users
from core.users import User

logger = logging.getLogger(__name__)

# Weekday names as settings write them (their first three letters), Monday first like ``date.weekday()``.
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEK_START_ENV = "WEEK_START"
REVIEW_AT_ENV = "WEEKLY_REVIEW_AT"
DEFAULT_REVIEW_AT = "sat 20:00"

TIMEZONE_SETTING = "timezone"
_timezone: Optional[zoneinfo.ZoneInfo] = None
# How to ask a user's Home Assistant for its zone: registered by the
# homeassistant addon (addons/homeassistant/client.py) when it is imported.
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
            or _zone(server_settings.get(TIMEZONE_SETTING), "the server's settings")
            or _zone(_home_assistant_timezone(), "Home Assistant")
            or _zone(os.getenv("TZ"), "TZ env")
            or zoneinfo.ZoneInfo("UTC")
        )
    return _timezone


def valid_timezone(name: str) -> bool:
    """Whether ``name`` is a timezone this server knows (``Europe/Paris``)."""
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return bool(name)


def set_server_timezone(name: str, *, by: Optional[str] = None) -> bool:
    """Store the server's timezone (an admin's choice); it applies at once
    unless ``TIMEZONE`` is set in env. Returns whether it applies now.

    Raises:
        ValueError: not a timezone.
    """
    global _timezone
    if not valid_timezone(name):
        raise ValueError(f"{name!r} is not a timezone (like Europe/Paris).")
    server_settings.put(TIMEZONE_SETTING, name, by=by)
    if os.getenv("TIMEZONE", "").strip():
        return False
    _timezone = None
    get_timezone()
    return True


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


def weekday_of(spec: str) -> Optional[int]:
    """The weekday (Monday 0 … Sunday 6) a setting starts with ("sun", "Sunday", "sat 20:00"), None for none."""
    word = (spec or "").strip().lower()[:3]
    return DAYS.index(word) if word in DAYS else None


def week_start_day() -> int:
    """The weekday weeks start on: ``WEEK_START``, else the day after ``WEEKLY_REVIEW_AT``'s day (Sunday by default)."""
    own = weekday_of(os.getenv(WEEK_START_ENV, ""))
    if own is not None:
        return own
    review = weekday_of(os.getenv(REVIEW_AT_ENV, DEFAULT_REVIEW_AT))
    return ((review if review is not None else DAYS.index("sat")) + 1) % 7


def week_start(d: datetime.date) -> datetime.date:
    """The first day of the week holding ``d`` (:func:`week_start_day`)."""
    return d - datetime.timedelta(days=(d.weekday() - week_start_day()) % 7)
