"""People: server-wide tuning, normalisation and formatting every module of the kind shares.

Pure functions, no database: phone numbers and names normalised for matching,
"12 days ago" wording, birthdays, the cleaned lists people's names are kept in.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import unicodedata
from typing import Any, Iterable, List, Optional

from core import clock
from integrations.whatsapp.resolve import transliterate_he_to_en

KIND = "people"

COUNTRY_CODE = re.sub(r"\D", "", os.getenv("DEFAULT_COUNTRY_CODE", "972")) or "972"
BRIEF_INTERVAL = int(os.getenv("PEOPLE_BRIEF_INTERVAL_SECONDS", "600"))
BRIEF_BATCH = int(os.getenv("PEOPLE_BRIEF_BATCH", "6"))
DRIFT_MIN_DAYS = int(os.getenv("PEOPLE_DRIFT_MIN_DAYS", "14"))
BRIEF_STYLE = "1"
UNKNOWN_NAMES = {"", "unknown", "me", "private number", "מספר חסוי", "חסוי", "no caller id"}
HEBREW = re.compile(r"[֐-׿]")
LATIN = re.compile(r"[A-Za-z]")


def norm_phone(value: Optional[str]) -> str:
    """'+972 50-123-4567' / '050-1234567' / '972501234567@c.us' → '972501234567' ('' if not a number)."""
    raw = (value or "").split("@")[0]
    d = re.sub(r"\D", "", raw)
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) in (9, 10):
        d = COUNTRY_CODE + d[1:]
    return d if len(d) >= 7 else ""


def norm_name(value: Optional[str]) -> str:
    """Lower-case, no emoji or punctuation, single spaces: 'גל כהן 🚀❤️' → 'גל כהן'."""
    s = unicodedata.normalize("NFKC", value or "").lower()
    s = re.sub(r"[^\w\s]", " ", s).replace("_", " ")
    return " ".join(s.split())


def looks_like_number(name: str) -> bool:
    return bool(re.fullmatch(r"[\d\s+\-().*#]+", name or "")) and bool(re.search(r"\d", name or ""))


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


def ago(dt: Optional[datetime.datetime], now: Optional[datetime.datetime] = None) -> str:
    """'just now' / '3h ago' / 'yesterday' / '12 days ago' / '3 months ago'."""
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


def clean_list(values: Iterable[str], limit: int = 20) -> List[str]:
    """Trimmed, de-duplicated (by normalised name) names, at most ``limit``."""
    out: List[str] = []
    for v in values or []:
        v = re.sub(r"\s+", " ", str(v or "")).strip()[:120]
        if v and norm_name(v) not in {norm_name(x) for x in out}:
            out.append(v)
    return out[:limit]


def clean_emails(values: Iterable[str], limit: int = 10) -> List[str]:
    """Trimmed email addresses, each once (whatever the case), at most ``limit``."""
    out: List[str] = []
    for v in values or []:
        v = str(v or "").strip()[:254]
        if "@" in v and v.lower() not in {x.lower() for x in out}:
            out.append(v)
    return out[:limit]


def valid_birthday(v: str) -> str:
    """``YYYY-MM-DD`` / ``MM-DD`` / '' (ValueError otherwise)."""
    v = (v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) or re.fullmatch(r"\d{2}-\d{2}", v):
        try:
            datetime.date.fromisoformat(v if len(v) == 10 else f"2000-{v}")
            return v
        except ValueError:
            pass
    if v:
        raise ValueError("birthday must be YYYY-MM-DD or MM-DD")
    return ""


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
