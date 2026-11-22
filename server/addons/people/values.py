"""People: server-wide tuning, normalisation and formatting every module of the addon shares.

Pure functions, no database: phone numbers and names normalised for matching,
birthdays, the cleaned lists people's names are kept in.
"""
from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Iterable, List, Optional

from core import clock
from core.text import (HEBREW, clean_list, looks_like_number, norm_name, norm_phone, shown_phone,
                       transliterate_he_to_en, valid_birthday)

ADDON = "people"

BRIEF_INTERVAL = int(os.getenv("PEOPLE_BRIEF_INTERVAL_SECONDS", "600"))
BRIEF_BATCH = int(os.getenv("PEOPLE_BRIEF_BATCH", "6"))
DRIFT_MIN_DAYS = int(os.getenv("PEOPLE_DRIFT_MIN_DAYS", "14"))
BRIEF_STYLE = "1"
UNKNOWN_NAMES = {"", "unknown", "me", "private number", "מספר חסוי", "חסוי", "no caller id"}


def now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")


def loads(raw: Any, default: Any) -> Any:
    """JSON ``raw`` when it decodes to the type of ``default``, else ``default``."""
    if not raw:
        return default
    try:
        v = json.loads(raw)
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def parse_time(value: Optional[str]) -> Optional[datetime.datetime]:
    """A stored timestamp (local when it has no zone) as an aware local datetime."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(str(value).strip()[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.get_timezone())
    return dt.astimezone(clock.get_timezone())


def initials(name: str) -> str:
    words = [w for w in norm_name(name).split() if w]
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def translit(text: str) -> str:
    """Hebrew → Latin letters for matching ('דן' → 'dan'); the text itself when that fails."""
    try:
        return transliterate_he_to_en(text)
    except Exception:
        return text


def clean_emails(values: Iterable[str], limit: int = 10) -> List[str]:
    """Trimmed email addresses, each once (whatever the case), at most ``limit``."""
    out: List[str] = []
    for v in values or []:
        v = str(v or "").strip()[:254]
        if "@" in v and v.lower() not in {x.lower() for x in out}:
            out.append(v)
    return out[:limit]


def upcoming_birthday(bday: str, today: Optional[datetime.date] = None, within: int = 14) -> Optional[int]:
    """Days until the next birthday when it's within ``within`` days."""
    if not bday:
        return None
    today = today or clock.now().date()
    mm_dd = bday[-5:]
    try:
        nxt = datetime.date.fromisoformat(f"{today.year}-{mm_dd}")
    except ValueError:
        return None
    if nxt < today:
        try:
            nxt = datetime.date.fromisoformat(f"{today.year + 1}-{mm_dd}")
        except ValueError:
            return None
    d = (nxt - today).days
    return d if d <= within else None
