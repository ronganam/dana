"""Small helpers addons (and core's composers) share instead of each keeping a
copy (docs/writing-an-addon.md, "Helpers"). Plain functions of their
arguments and the server's clock (core/clock.py); nothing here touches the
database or imports an addon.

Times:

- :func:`parse_time` — a stored time (ISO with or without a zone, or SQLite's
  ``CURRENT_TIMESTAMP``; naive = UTC) as an aware local datetime;
- :func:`ago` — "just now" / "12 min ago" / "yesterday" / "3 months ago";
- :func:`when_phrase` — a moment as "19:40" / "tonight 20:00" / "tomorrow
  09:00" / "Thu 18:00" / "3 Dec 10:00" (the Now screen's wording);
- :func:`fmt_date` — a date as "today" / "tomorrow" / "Thu" / "3 Dec";
- :func:`age_days` — whole days since a stored time;
- :func:`next_weekday` / :func:`valid_future_date` — dates the weekly review proposes.

Search:

- :func:`like_escape` / :func:`like_pattern` — text for ``LIKE ? ESCAPE '\\'``;
- :func:`search_terms` / :func:`match_score` — the words of a query and how
  well a title matches it (the global search's order).
"""

import datetime
import re
from typing import Any, List, Optional

from core import clock


# --- Times ------------------------------------------------------------------------------

def parse_time(value: Any) -> Optional[datetime.datetime]:
    """ISO with or without zone, or SQLite's CURRENT_TIMESTAMP (naive = UTC) →
    aware local time; None when empty or unparsable."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(clock.get_timezone())


def ago(dt: Optional[datetime.datetime], now: Optional[datetime.datetime] = None) -> str:
    """'just now' / '3h ago' / 'yesterday' / '12 days ago' / '3 months ago' ("" without a time)."""
    if not dt:
        return ""
    now = now or clock.now()
    secs = (now - dt).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    days = (now.date() - dt.date()).days
    if days <= 0:
        return f"{int(secs // 3600)}h ago"
    if days == 1:
        return "yesterday"
    if days < 45:
        return f"{days} days ago"
    if days < 365:
        return f"{round(days / 30)} months ago"
    return f"{round(days / 365, 1):g} years ago"


def when_phrase(dt: datetime.datetime, now: Optional[datetime.datetime] = None) -> str:
    """'19:40' (today) / 'tonight 20:00' / 'tomorrow 09:00' / 'Thu 18:00' / '3 Dec 10:00'."""
    now = now or clock.now()
    dt = dt.astimezone(now.tzinfo)
    hm = dt.strftime("%H:%M")
    days = (dt.date() - now.date()).days
    if days == 0:
        return f"tonight {hm}" if dt.hour >= 18 and now.hour < 18 else hm
    if days == 1:
        return f"tomorrow {hm}"
    if 1 < days < 7:
        return f"{dt.strftime('%a')} {hm}"
    return f"{dt.day} {dt.strftime('%b')} {hm}"


def fmt_date(d: Optional[str]) -> str:
    """An ISO date (or the date part of a time) as 'today' / 'tomorrow' / 'Thu' / '3 Dec'; as given when unparsable."""
    try:
        x = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d or "")
    today = clock.now().date()
    if x == today:
        return "today"
    if x == today + datetime.timedelta(days=1):
        return "tomorrow"
    if 0 < (x - today).days < 7:
        return x.strftime("%a")
    return f"{x.day} {x.strftime('%b')}"


def age_days(ts: Optional[str], now: datetime.datetime) -> Optional[int]:
    """Whole days from a stored time (naive = UTC, SQLite's CURRENT_TIMESTAMP) to ``now``; None when unparsable."""
    if not ts:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (now - dt).days


def next_weekday(base: datetime.date, weekday: int) -> datetime.date:
    """The next ``weekday`` (Monday 0 … Sunday 6) after ``base`` (a week on when ``base`` is one)."""
    return base + datetime.timedelta(days=(weekday - base.weekday()) % 7 or 7)


def valid_future_date(v: Any, today: datetime.date) -> Optional[str]:
    """``v`` as an ISO date when it is one after ``today``, else None."""
    try:
        d = datetime.date.fromisoformat(str(v or "")[:10])
        return d.isoformat() if d > today else None
    except ValueError:
        return None


# --- Search -----------------------------------------------------------------------------

def like_escape(text: str) -> str:
    """``text`` with ``\\``, ``%`` and ``_`` escaped for ``LIKE ? ESCAPE '\\'``."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(q: str) -> str:
    """A ``LIKE ? ESCAPE '\\'`` pattern matching ``q`` anywhere."""
    return "%" + like_escape(q) + "%"


def search_terms(q: str) -> List[str]:
    """The words of a query (at most 8), Hebrew and English."""
    return [t for t in re.findall(r"\w+", q or "", flags=re.UNICODE) if t][:8]


def match_score(title: str, q: str) -> float:
    """How well ``title`` matches ``q``: exact 4, prefix 3, a word starts with it
    2.5, contains 2, every word in it 1.5, else 0.5 (0 for an empty query)."""
    t, s = (title or "").casefold().strip(), q.casefold().strip()
    if not s:
        return 0.0
    if t == s:
        return 4.0
    if t.startswith(s):
        return 3.0
    if any(w.startswith(s) for w in re.findall(r"\w+", t, flags=re.UNICODE)):
        return 2.5
    if s in t:
        return 2.0
    terms = search_terms(s)
    if terms and all(x in t for x in terms):
        return 1.5
    return 0.5
