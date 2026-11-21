"""The fixed label catalog for phone calls.

Calls get 1-3 labels from this list (picked by the analysis model, editable by
the user). A fixed list keeps filters stable; edit it here. ``group`` is the
task group a suggested task from such a call lands in when the model doesn't
pick one.

``{first}`` in a hint is the user's own first name; who their partner or
pet is comes from their profile context, which the analysis prompt includes.
Ids are the same for everyone.
"""
from typing import Dict, List, Optional

from core import user_profile
from core.users import User

CALL_LABELS: List[Dict] = [
    {"id": "family", "emoji": "👨‍👩‍👧", "en": "Family", "he": "משפחה", "group": "Home",
     "hint": "parents, siblings, relatives"},
    {"id": "partner", "emoji": "❤️", "en": "Partner", "he": "בן/בת זוג", "group": "Personal",
     "hint": "calls with {first}'s partner"},
    {"id": "friends", "emoji": "🧑‍🤝‍🧑", "en": "Friends", "he": "חברים", "group": "Personal",
     "hint": "friends and acquaintances, social plans"},
    {"id": "work", "emoji": "💼", "en": "Work", "he": "עבודה", "group": "Work",
     "hint": "colleagues, customers, outages, work projects"},
    {"id": "wedding", "emoji": "💍", "en": "Wedding", "he": "חתונה", "group": "Personal",
     "hint": "a wedding being planned: venues, vendors, guest list"},
    {"id": "home", "emoji": "🏠", "en": "Home", "he": "בית", "group": "Home",
     "hint": "repairs, technicians, furniture, installations, the apartment"},
    {"id": "purchases", "emoji": "🛒", "en": "Purchases", "he": "קניות", "group": "Shopping",
     "hint": "buying products, price checks, stores, orders, deliveries"},
    {"id": "travel", "emoji": "✈️", "en": "Travel & leisure", "he": "חופשות ופנאי", "group": "Vacation",
     "hint": "trips, hotels, flights, spa, restaurants, events"},
    {"id": "car", "emoji": "🚗", "en": "Car", "he": "רכב", "group": "Personal",
     "hint": "car repairs, insurance, garage, driving course"},
    {"id": "health", "emoji": "🩺", "en": "Health", "he": "בריאות", "group": "Health",
     "hint": "doctors, clinics, tests, medical forms, treatments"},
    {"id": "pets", "emoji": "🐶", "en": "Pets", "he": "חיות מחמד", "group": "Personal",
     "hint": "pets: vet, vaccines, grooming"},
    {"id": "admin", "emoji": "💰", "en": "Admin & money", "he": "סידורים וכספים", "group": "Personal",
     "hint": "bills, invoices, banks, insurance policies, registrations, bureaucracy"},
    {"id": "security", "emoji": "🔐", "en": "Security", "he": "אבטחה", "group": "Personal",
     "hint": "hacked accounts, fraud, scams, suspicious calls"},
    {"id": "tech_help", "emoji": "🛠", "en": "Tech help", "he": "עזרה טכנית", "group": "Personal",
     "hint": "{first} helping someone with phones, computers, TV, apps"},
    {"id": "logistics", "emoji": "📍", "en": "Quick logistics", "he": "תיאום קצר", "group": "Personal",
     "hint": "short coordination: where are you, parking, on my way"},
    {"id": "automated", "emoji": "🤖", "en": "Automated", "he": "מענה אוטומטי", "group": "General",
     "hint": "IVR menus, recorded messages, voicemail, no human"},
]

LABEL_IDS = [l["id"] for l in CALL_LABELS]
_BY_ID = {l["id"]: l for l in CALL_LABELS}


def get_label(label_id: str) -> Optional[Dict]:
    return _BY_ID.get(label_id)


def _as_seen_by(user: Optional[User], label: Dict) -> Dict[str, str]:
    """``label``'s id, emoji, names and hint as ``user`` sees them (``{first}`` filled in)."""
    out = {k: label[k] for k in ("id", "emoji", "en", "he", "group", "hint")}
    who = user_profile.for_user(user)
    out["hint"] = out["hint"].replace("{first}", who.first_name or "the user")
    return out


def clean_labels(raw) -> List[str]:
    """Keep known ids, in order, without duplicates, at most 3."""
    out: List[str] = []
    for item in raw or []:
        lid = str(item or "").strip().lower()
        if lid in _BY_ID and lid not in out:
            out.append(lid)
    return out[:3]


def label_details(user: Optional[User], ids: List[str]) -> List[Dict[str, str]]:
    """The labels' ids, emoji and names (English / Hebrew) as ``user`` sees them, unknown ids left out."""
    out = []
    for lid in ids or []:
        l = get_label(lid)
        if l:
            l = _as_seen_by(user, l)
            out.append({"id": l["id"], "emoji": l["emoji"], "name": l["en"], "name_he": l["he"]})
    return out


def label_text(user: Optional[User], label_id: str) -> str:
    l = _BY_ID.get(label_id)
    if not l:
        return label_id
    l = _as_seen_by(user, l)
    return f"{l['emoji']} {l['en']}"


def labels_catalog(user: Optional[User]) -> List[Dict[str, str]]:
    """``GET /api/calls/labels``: every label as ``user`` sees it."""
    return [{"id": l["id"], "emoji": l["emoji"], "name": l["en"], "name_he": l["he"], "group": l["group"]}
            for l in (_as_seen_by(user, x) for x in CALL_LABELS)]


def group_for_labels(labels: List[str]) -> Optional[str]:
    for lid in labels or []:
        l = _BY_ID.get(lid)
        if l and l["id"] not in ("logistics", "automated"):
            return l["group"]
    return None


def catalog_for_prompt(user: Optional[User]) -> str:
    return "\n".join(f"- {l['id']}: {l['en']} — {l['hint']}" for l in (_as_seen_by(user, x) for x in CALL_LABELS))
