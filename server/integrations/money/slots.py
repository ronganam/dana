"""When a source syncs: the regular slots with a stable per-source jitter,
and the quiet hours (docs/money.md, "Schedule"). Pure functions of the time."""

import datetime
import hashlib

from core import clock
from integrations.money import config


def jitter(source_id: str) -> int:
    """The source's stable offset in minutes, within ± :data:`config.JITTER_MINUTES`."""
    span = 2 * config.JITTER_MINUTES + 1
    return int(hashlib.sha1(source_id.encode()).hexdigest()[:8], 16) % span - config.JITTER_MINUTES


def next_slot(source_id: str, after: datetime.datetime) -> datetime.datetime:
    """The source's first regular sync time (``MONEY_SYNC_TIMES`` + its jitter, local) later than ``after``."""
    tz = clock.get_timezone()
    local = after.astimezone(tz)
    shift = datetime.timedelta(minutes=jitter(source_id))
    for days in range(3):
        day = local.date() + datetime.timedelta(days=days)
        for t in config.sync_times():
            candidate = datetime.datetime.combine(day, t, tzinfo=tz) + shift
            if candidate > after:
                return candidate
    raise RuntimeError("no sync slot found")  # unreachable: every day has slots


def out_of_quiet_hours(source_id: str, when: datetime.datetime) -> datetime.datetime:
    """``when``, or, inside the quiet hours (00:00–06:00 local), the end of
    them plus the source's jitter (always after 06:00)."""
    tz = clock.get_timezone()
    local = when.astimezone(tz)
    start, end = config.QUIET_HOURS
    if start <= local.hour < end:
        return datetime.datetime.combine(local.date(), datetime.time(end), tzinfo=tz) + \
            datetime.timedelta(minutes=abs(jitter(source_id)))
    return when
