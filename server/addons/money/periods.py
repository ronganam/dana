"""Named periods for money questions, resolved in code (docs/money.md, "Page API and insights").

:func:`resolve` turns a period name (``this_month``, ``last_week``, ``2026-10``…)
or explicit dates into a :class:`Period`: the local dates it covers (``start``
inclusive, ``end_exclusive``), a label, and the periods to compare it with. A
period still in progress ends after today and compares with the same elapsed
span of the previous one ("at this point last month"); a whole past period
compares with the whole one before it. Weeks start on the server's first day of
the week (``core.clock.week_start``: Sunday by default). Every "today" comes from
``core.clock``.
"""

import calendar
import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core import clock

NAMES: Tuple[str, ...] = ("today", "yesterday", "this_week", "last_week", "this_month", "last_month",
                          "last_30_days", "this_year", "last_year")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_YEAR = re.compile(r"^(\d{4})$")


@dataclass(frozen=True)
class Period:
    """Local dates ``start`` ≤ d < ``end_exclusive``; ``key`` is the name it came
    from (``this_month``, ``2026-10``, ``custom``…); ``in_progress`` while it
    includes today."""
    start: datetime.date
    end_exclusive: datetime.date
    label: str
    key: str = "custom"
    in_progress: bool = False
    compare_to: Tuple["Period", ...] = field(default=())

    @property
    def end(self) -> datetime.date:
        """The last day it covers (inclusive)."""
        return self.end_exclusive - datetime.timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end_exclusive - self.start).days

    def payload(self) -> Dict[str, Any]:
        """``{key, label, start, end, in_progress}`` — ``end`` is the last day (inclusive)."""
        return {"key": self.key, "label": self.label, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "in_progress": self.in_progress}


def today() -> datetime.date:
    """The local date now (``core.clock``)."""
    return clock.now().date()


# --- Calendar arithmetic --------------------------------------------------------------------

def month_start(d: datetime.date) -> datetime.date:
    return d.replace(day=1)


def add_months(d: datetime.date, months: int) -> datetime.date:
    """``d`` moved by whole months, the day clamped to the target month's length."""
    index = d.year * 12 + d.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return datetime.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def month_end_exclusive(d: datetime.date) -> datetime.date:
    return add_months(month_start(d), 1)


def short_date(d: datetime.date, year: bool = True) -> str:
    """``16 Nov 2026`` (``16 Nov`` without the year)."""
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if year else "")


def month_label(d: datetime.date) -> str:
    """``November 2026``."""
    return f"{d.strftime('%B')} {d.year}"


def span_label(start: datetime.date, end: datetime.date) -> str:
    """``1–18 Nov 2026``, ``28 Oct – 3 Nov 2026``, ``16 Nov 2026`` (``end`` inclusive)."""
    if start == end:
        return short_date(start)
    if start.year != end.year:
        return f"{short_date(start)} – {short_date(end)}"
    if start.month != end.month:
        return f"{short_date(start, False)} – {short_date(end)}"
    return f"{start.day}–{short_date(end)}"


def _whole(start: datetime.date, end_exclusive: datetime.date, label: str, key: str = "custom") -> Period:
    return Period(start, end_exclusive, label, key)


def _month(first: datetime.date, now: datetime.date, key: str, label: Optional[str] = None) -> Period:
    """A calendar month: in progress (so far, compared with the same span of
    the month before and all of it) or whole (compared with the month before)."""
    end = month_end_exclusive(first)
    prev = add_months(first, -1)
    label = label or month_label(first)
    if first <= now < end:
        so_far = now + datetime.timedelta(days=1)
        same_day = min(add_months(now, -1) + datetime.timedelta(days=1), first)
        return Period(first, so_far, label, key, True,
                      (_whole(prev, same_day, "at this point last month"),
                       _whole(prev, first, "all of last month")))
    return Period(first, end, label, key, False, (_whole(prev, first, month_label(prev)),))


# --- Resolving -----------------------------------------------------------------------------

def _parse_date(value: Any, what: str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError(f"{what} must be a date (YYYY-MM-DD), not {value!r}") from None


def resolve(name: Optional[str] = None, *, start: Any = None, end: Any = None,
            now: Optional[datetime.date] = None) -> Period:
    """The period a name or explicit dates mean, as of ``now`` (default: today).

    Args:
        name: One of :data:`NAMES`, ``YYYY-MM`` or ``YYYY`` (spaces and dashes
            in a name work too: "this month", "last-week"). Ignored when
            ``start`` is given.
        start: The first day (``YYYY-MM-DD``); with it, ``end`` is the last day
            (inclusive, default today).
        end: See ``start``.
        now: The local date to resolve against.

    Raises:
        ValueError: an unknown name or a malformed / reversed date.
    """
    now = now or today()
    one = datetime.timedelta(days=1)
    if start not in (None, ""):
        first = _parse_date(start, "start")
        last = _parse_date(end, "end") if end not in (None, "") else now
        if last < first:
            raise ValueError("end must not be before start")
        length = last - first + one
        before = first - length
        return Period(first, last + one, span_label(first, last), "custom", first <= now <= last,
                      (_whole(before, first, f"the {length.days} days before"),))
    raw = (name or "this_month").strip().lower()
    key = raw if _MONTH.match(raw) or _YEAR.match(raw) else re.sub(r"[\s\-]+", "_", raw)
    tomorrow = now + one
    if key == "today":
        return Period(now, tomorrow, "today", key, True, (_whole(now - one, now, "yesterday"),))
    if key == "yesterday":
        y = now - one
        return Period(y, now, "yesterday", key, False, (_whole(y - one, y, "the day before"),))
    if key == "this_week":
        first = clock.week_start(now)
        return Period(first, tomorrow, "this week", key, True,
                      (_whole(first - 7 * one, tomorrow - 7 * one, "at this point last week"),))
    if key == "last_week":
        first = clock.week_start(now) - 7 * one
        return Period(first, first + 7 * one, "last week", key, False,
                      (_whole(first - 7 * one, first, "the week before"),))
    if key == "this_month":
        return _month(month_start(now), now, key, "this month")
    if key == "last_month":
        return _month(add_months(month_start(now), -1), now, key, "last month")
    if key == "last_30_days":
        first = tomorrow - 30 * one
        return Period(first, tomorrow, "the last 30 days", key, True,
                      (_whole(first - 30 * one, first, "the 30 days before"),))
    if key == "this_year":
        first = datetime.date(now.year, 1, 1)
        prev = datetime.date(now.year - 1, 1, 1)
        same_day = prev.replace(month=now.month, day=min(now.day, calendar.monthrange(now.year - 1, now.month)[1]))
        return Period(first, tomorrow, "this year", key, True,
                      (_whole(prev, same_day + one, "at this point last year"),
                       _whole(prev, first, f"all of {now.year - 1}")))
    if key == "last_year":
        first = datetime.date(now.year - 1, 1, 1)
        return Period(first, datetime.date(now.year, 1, 1), f"last year ({now.year - 1})", key, False,
                      (_whole(datetime.date(now.year - 2, 1, 1), first, str(now.year - 2)),))
    m = _MONTH.match(key)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"no month {month} in {key!r}")
        return _month(datetime.date(year, month, 1), now, key)
    y = _YEAR.match(key)
    if y:
        year = int(y.group(1))
        if year == now.year:
            p = resolve("this_year", now=now)
            return Period(p.start, p.end_exclusive, str(year), key, True, p.compare_to)
        first = datetime.date(year, 1, 1)
        return Period(first, datetime.date(year + 1, 1, 1), str(year), key, False,
                      (_whole(datetime.date(year - 1, 1, 1), first, str(year - 1)),))
    raise ValueError(f"unknown period {name!r}: use one of {', '.join(NAMES)}, YYYY-MM, YYYY or start/end dates")
