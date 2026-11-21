"""Small text, phone-number, name and redaction helpers shared by core and addons."""
from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Iterable, List, Optional

from core import user_locale

HEBREW = re.compile(r"[֐-׿]")

_HE_EN = {
    "א": "a", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z", "ח": "kh",
    "ט": "t", "י": "y", "כ": "k", "ל": "l", "מ": "m", "נ": "n", "ס": "s", "ע": "a",
    "פ": "p", "צ": "ts", "ק": "k", "ר": "r", "ש": "sh", "ת": "t",
    "ך": "k", "ם": "m", "ן": "n", "ף": "p", "ץ": "ts",
}

_OTP_CONTEXT = re.compile(r"(code|otp|password|passcode|verification|verify|pin|קוד|סיסמ|אימות)", re.I)
_OTP_NUMBER = re.compile(r"(?<!\d)(\d[\d\- ]{2,8}\d)(?!\d)")


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram's legacy Markdown mode."""
    if not text:
        return ""
    for c in ("\\", "_", "*", "`", "["):
        text = text.replace(c, f"\\{c}")
    return text


def transliterate_he_to_en(text: str) -> str:
    """Convert Hebrew characters to a rough Latin phonetic equivalent."""
    return "".join(_HE_EN.get(ch, ch) for ch in text).lower()


def redact_otp(text: str) -> str:
    """Hide one-time codes in notification / SMS text."""
    if not text or not _OTP_CONTEXT.search(text):
        return text
    return _OTP_NUMBER.sub(lambda m: "[code]" if sum(c.isdigit() for c in m.group(1)) >= 4 else m.group(1), text)


def norm_phone(value: Optional[str]) -> str:
    """'+972 50-123-4567' / '972501234567@c.us' → '972501234567'; '' if not a number."""
    d = user_locale.international_digits((value or "").split("@")[0])
    return d if len(d) >= 7 else ""


def shown_phone(digits: str) -> str:
    """A stored number as people read it: '+972501234567', or local '0501234567' as is."""
    return digits if not digits or digits.startswith("0") else "+" + digits


def norm_name(value: Optional[str]) -> str:
    """Lower-case, no emoji or punctuation, single spaces."""
    s = unicodedata.normalize("NFKC", value or "").lower()
    s = re.sub(r"[^\w\s]", " ", s).replace("_", " ")
    return " ".join(s.split())


def looks_like_number(name: str) -> bool:
    return bool(re.fullmatch(r"[\d\s+\-().*#]+", name or "")) and bool(re.search(r"\d", name or ""))


def clean_list(values: Iterable[str], limit: int = 20) -> List[str]:
    """Trimmed, de-duplicated (by normalised name) names, at most ``limit``."""
    out: List[str] = []
    for v in values or []:
        v = re.sub(r"\s+", " ", str(v or "")).strip()[:120]
        if v and norm_name(v) not in {norm_name(x) for x in out}:
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
