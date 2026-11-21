"""Item names: the comparison key, and typed text split into items with
their quantities (docs/shopping.md, "Adding").

``name_key`` decides when two names are the same item: lower case, Hebrew
vowel marks removed, punctuation removed (dashes and slashes count as
spaces), spaces collapsed. ``parse_text`` turns what someone typed into
items: one per line or comma, a leading count (``2 חלב``, ``3x eggs``), a
leading amount with a unit (``1 kg tomatoes``, ``2 ליטר חלב``) or a trailing
``x2`` / ``×2`` becomes the quantity; everything else stays in the name.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, List, Optional

from addons.shopping.values import MAX_ITEMS, MAX_NAME, MAX_NOTE, MAX_QUANTITY


def name_key(name: str) -> str:
    """The key names are compared by (the same item when equal)."""
    out = []
    for c in unicodedata.normalize("NFKC", name or "").casefold():
        cat = unicodedata.category(c)
        if cat.startswith("M") and "֑" <= c <= "ׇ":
            continue  # Hebrew vowel and cantillation marks
        if cat == "Pd" or c == "/":
            out.append(" ")  # dashes and slashes separate words
        elif not cat.startswith("P"):
            out.append(c)
    return re.sub(r"\s+", " ", "".join(out)).strip()


@dataclass(frozen=True)
class ItemInput:
    """One item to add: a name, optional quantity and note, and optionally the
    client's own id for it (a UUID: a repeated request adds nothing twice)."""
    name: str
    quantity: str = ""
    note: str = ""
    id: Optional[str] = None


# Units after a leading amount (English and Hebrew, as people type them).
_UNITS = (
    "kg", "kgs", "kilo", "kilos", "g", "gr", "gram", "grams", "l", "lt", "ltr", "liter", "liters", "litre",
    "litres", "ml", "lb", "lbs", "oz", "pack", "packs", "pk", "box", "boxes", "bottle", "bottles", "can", "cans",
    "bag", "bags", "dozen", "jar", "jars", "pcs", "pieces", "piece", "units", "unit", "rolls", "roll", "tray",
    "trays", "bunch", "bunches", "loaf", "loaves",
    'ק"ג', "ק״ג", "קג", "קילו", "גרם", "גר'", "גר", "ליטר", "ליטרים", "ל'", 'מ"ל', "מ״ל", "מל", "יחידות", "יחידה",
    "יח'", "חבילה", "חבילות", "בקבוק", "בקבוקים", "קופסה", "קופסאות", "קופסא", "שקית", "שקיות", "מגש", "מגשים",
    "תריסר", "זוג", "זוגות", "צרור", "צרורות", "פחית", "פחיות", "גביע", "גביעים", "כיכר", "כיכרות", "רול", "גלילים",
)
_NUMBER = r"\d+(?:[.,]\d+)?|½|¼|¾"
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
# "1 kg tomatoes", "1kg tomatoes", "2 ליטר חלב" (the unit is a whole word: "2 gala apples" keeps "gala").
_LEADING_AMOUNT = re.compile(rf"^({_NUMBER})\s*({_UNIT_RE})(?=\s)\s+(.+)$", re.IGNORECASE)
# "2 חלב", "3x eggs", "3 x eggs", "3× eggs"
_LEADING_COUNT = re.compile(rf"^({_NUMBER})\s*(?:[x×]\s*)?\s(?=\S)(.+)$|^({_NUMBER})\s*[x×]\s*(\S.*)$", re.IGNORECASE)
# "eggs x2", "eggs ×2", "eggs x 2", "eggs×2"
_TRAILING_COUNT = re.compile(rf"^(.*?\S)\s*(?:\s[x×]|×)\s*({_NUMBER})$", re.IGNORECASE)
_BULLET = re.compile(r"^[\s\-*•·▪›>]+")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")


def clip(text: Any, limit: int) -> str:
    """``text`` as one trimmed line of at most ``limit`` characters."""
    return " ".join(str(text or "").split())[:limit]


def parse_line(line: str) -> Optional[ItemInput]:
    """One typed line as an item (None: nothing in it)."""
    text = _BULLET.sub("", _NUMBERED.sub("", line or "")).strip()
    if not text:
        return None
    quantity = ""
    m = _LEADING_AMOUNT.match(text)
    if m:
        quantity, text = f"{m.group(1)} {m.group(2)}", m.group(3)
    else:
        m = _LEADING_COUNT.match(text)
        if m:
            quantity, text = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        else:
            m = _TRAILING_COUNT.match(text)
            if m:
                text, quantity = m.group(1), m.group(2)
    name = clip(text, MAX_NAME)
    if not name:
        return None
    return ItemInput(name=name, quantity=clip(quantity, MAX_QUANTITY))


def parse_text(text: str) -> List[ItemInput]:
    """Typed text as items: split on line breaks and commas (empty parts skipped)."""
    out = []
    for part in re.split(r"[\n\r,،]+", text or ""):
        item = parse_line(part)
        if item is not None:
            out.append(item)
    return out


_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def item_input(value: Any) -> Optional[ItemInput]:
    """One entry of an add request (a name, or ``{name, quantity, note, id}``)
    as an item; a plain string is parsed like a typed line. None: no name.

    Raises:
        ValueError: an entry that is neither, or an id that is not a client id.
    """
    if isinstance(value, str):
        return parse_line(value)
    if not isinstance(value, dict):
        raise ValueError("Each item is a name or {name, quantity, note, id}.")
    name = clip(value.get("name"), MAX_NAME)
    if not name:
        return None
    client_id = value.get("id")
    if client_id is not None and (not isinstance(client_id, str) or not _CLIENT_ID.match(client_id)):
        raise ValueError("An item id is 8 to 64 letters, digits, '-' or '_' (a UUID).")
    return ItemInput(name=name, quantity=clip(value.get("quantity"), MAX_QUANTITY),
                     note=clip(value.get("note"), MAX_NOTE), id=client_id)


class TooMany(ValueError):
    """More than :data:`MAX_ITEMS` items in one add."""


def check_count(items: List[ItemInput]) -> List[ItemInput]:
    """``items`` when there are at most :data:`MAX_ITEMS`.

    Raises:
        TooMany: more than that.
    """
    if len(items) > MAX_ITEMS:
        raise TooMany(f"At most {MAX_ITEMS} items at a time.")
    return items
