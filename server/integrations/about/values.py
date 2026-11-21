"""About you: sections, limits, where a fact came from, and the text rules
every writer shares (clean, duplicate, "do not re-add", secrets, section
guess). No database access here.
"""
from __future__ import annotations

import datetime
import difflib
import os
import re
from typing import Any, Dict, List, Optional, Tuple


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# --- Configuration ------------------------------------------------------------

SECTIONS: List[Tuple[str, str]] = [
    ("people", "People"),
    ("home", "Home"),
    ("habits", "Habits"),
    ("other", "Other"),
]
SECTION_KEYS = [k for k, _ in SECTIONS]
SECTION_LABELS = dict(SECTIONS)

FACT_MAX_CHARS = 280                      # one fact, as typed or learned
MAX_CHARS = env_int("ABOUT_MAX_CHARS", 4000)   # all active facts of a user together
MAX_FACTS = env_int("ABOUT_MAX_FACTS", 80)
KEEP_INACTIVE = 300                       # forgotten / dropped rows kept per user for "do not re-add"

# Where a fact came from. Learned kinds are dropped first by the cap and are the
# only ones the learner may rewrite.
SOURCE_LABELS = {
    "chat": "a chat",
    "call": "a call",
    "told": "you told Dana",
    "owner": "you added this",
    "edited": "you edited this",
    "imported": "your old Memory list",
}
LEARNED_KINDS = ("chat", "call")
DUP_RATIO = 0.80        # same fact: characters this close ...
DUP_WORDS = 0.75        # ... and this share of content words
FORGOTTEN_WORDS = 0.70  # share of content words that makes it a removed fact again

# Credentials and one-time codes never become facts, whoever proposes them (the
# user can still type anything on the About page).
_SENSITIVE = re.compile(
    r"(password|passcode|passwd|\bpin\b|\botp\b|one[- ]time code|verification code|security code|"
    r"\bcvv\b|credit card|card number|\biban\b|api[ _-]?key|access token|secret|"
    r"סיסמ|קוד אימות|קוד חד|קוד סודי|כרטיס אשראי|מספר כרטיס)",
    re.IGNORECASE,
)
_DIGIT_RUN = re.compile(r"(?<![\w@])\d[\d \-]{10,}\d(?![\d@])")

# Cheap section guess when none is given.
_HOME_WORDS = re.compile(
    r"\b(gate|door|light|lights|lamp|tv|ac|air ?con|boiler|heater|blind|blinds|shutter|camera|vacuum|"
    r"thermostat|living[- ]room|bedroom|kitchen|car|garage|router|wifi)\b|שער|דלת|אור|מנורה|טלוויזיה|מזגן|"
    r"דוד|תריס|מצלמה|שואב|סלון|חדר שינה|מטבח|רכב|אוטו|חניה",
    re.IGNORECASE,
)
_PEOPLE_WORDS = re.compile(
    r"\b(mom|mum|mother|dad|father|brother|sister|wife|husband|girlfriend|boyfriend|partner|son|daughter|"
    r"friend|boss|colleague|cousin|uncle|aunt|grandma|grandpa|neighbou?r|is my|are my)\b|אמא|אבא|אח |אחי|"
    r"אחות|אשתי|בעלי|חבר|חברה|בן דוד|בת דודה|סבתא|סבא|שכן|הבוס|קולגה",
    re.IGNORECASE,
)
_HABIT_WORDS = re.compile(
    r"\b(prefer|prefers|usually|always|never|every|likes|dislikes|wakes|routine|weekdays|weekends|"
    r"mornings?|evenings?)\b|מעדיף|מעדיפה|בדרך כלל|תמיד|אף פעם|כל יום|כל בוקר|אוהב|לא אוהב|קם|בבוקר|בערב",
    re.IGNORECASE,
)


# --- Small helpers --------------------------------------------------------------

def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def clean_text(text: Any) -> str:
    """One line: collapsed whitespace, no bullet prefix."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s.lstrip("-•*· ").strip()


def normalize(text: str) -> str:
    s = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


_STOP = {
    "the", "and", "his", "her", "him", "she", "was", "are", "has", "have", "for", "with", "from", "that",
    "this", "who", "owner", "של", "את", "הוא", "היא", "עם", "גם", "זה", "זו", "שלו", "שלה", "שלי",
}


def _content_words(text: str, name: str) -> set:
    """Words that carry the fact: no stopwords, no 1-2 letter words, not the
    name of the person the facts are about (``name``)."""
    own = set(normalize(name).split())
    return {w for w in normalize(text).split() if len(w) > 2 and w not in _STOP and w not in own}


def _jaccard(a: str, b: str, name: str) -> Optional[float]:
    wa, wb = _content_words(a, name), _content_words(b, name)
    if not wa or not wb:
        return None
    return len(wa & wb) / len(wa | wb)


def same_fact(a: str, b: str, name: str) -> bool:
    """The same fact, maybe reworded or sharpened. Names matter: "Dan is
    Maya's brother" and "Eli is Maya's brother" are different facts. ``name``:
    whose facts they are (their name does not count as a content word)."""
    if normalize(a) == normalize(b):
        return True
    j = _jaccard(a, b, name)
    r = similarity(a, b)
    return r >= 0.9 if j is None else (j >= DUP_WORDS and r >= DUP_RATIO)


def near_forgotten(a: str, b: str, name: str) -> bool:
    """Close enough to a removed fact that it must not come back, in any wording."""
    if normalize(a) == normalize(b):
        return True
    j = _jaccard(a, b, name)
    return similarity(a, b) >= 0.9 or (j is not None and j >= FORGOTTEN_WORDS)


def looks_sensitive(text: str) -> bool:
    """Credentials, one-time codes, card/account-length numbers."""
    if _SENSITIVE.search(text or ""):
        return True
    for m in _DIGIT_RUN.finditer(text or ""):
        if sum(c.isdigit() for c in m.group(0)) >= 13:
            return True
    return False


def guess_section(text: str) -> str:
    if _PEOPLE_WORDS.search(text or ""):
        return "people"
    if _HOME_WORDS.search(text or ""):
        return "home"
    if _HABIT_WORDS.search(text or ""):
        return "habits"
    return "other"


def section_for_category(category: Optional[str], text: str = "") -> str:
    """Old memory category / loose section name → a section key."""
    c = (category or "").strip().lower()
    if c in SECTION_KEYS:
        return c
    if c in ("contact", "contacts", "person", "family", "friends", "relationship"):
        return "people"
    if c in ("device", "devices", "house", "smart_home", "smarthome"):
        return "home"
    if c in ("preference", "preferences", "habit", "routine", "routines"):
        return "habits"
    return guess_section(text) if text else "other"


def _source_label(kind: str, label: Optional[str]) -> str:
    return label or SOURCE_LABELS.get(kind, kind)


def fact_dict(row: Any) -> Dict[str, Any]:
    """An ``about_facts`` row → the fact shape every reader uses."""
    d = dict(row)
    return {
        "id": d["id"],
        "text": d["text"],
        "section": d["section"],
        "source": _source_label(d["source_kind"], d.get("source_label")),
        "source_kind": d["source_kind"],
        "source_ref": d.get("source_ref"),
        "learned_at": d["created_at"],
        "updated_at": d["updated_at"],
        "status": d["status"],
    }
