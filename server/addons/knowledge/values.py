"""Knowledge vocabulary, value normalisation and row → API shape, shared by
projects, sections, entries, files, media processing and the tools."""

import json
import re
import uuid
from typing import Any, Dict, List, Optional

# Kinds are rendering hints only; the entry schema is identical for all of them.
KINDS = ("note", "record", "link")
STATUSES = ("open", "in_progress", "done", "archived")
PRIORITIES = ("low", "medium", "high")
# A field's / column's type. money and number are compared as numbers (a value that is
# not one, e.g. "380-390", is kept as text); phone offers call / WhatsApp; rating is 1-5.
FIELD_TYPES = ("text", "number", "money", "phone", "checkbox", "date", "url", "select", "rating")
# A section is a decision among options sharing columns, or a place for free notes.
SECTION_KINDS = ("options", "notes")
# Where an option stands ('' = just an option).
DECISIONS = ("", "shortlist", "chosen", "rejected")
# A column's role: "price" counts toward the project's budget when the option is chosen.
COLUMN_ROLES = ("", "price")
# Which value of a column is the best one in a comparison ("" = none).
COLUMN_BEST = ("", "low", "high")

# Columns that never leave the server (who owns, created or set the link of a row).
OWNER_COLUMNS = ("user_id", "created_by", "url_set_by")


def clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def loads_json(value: Any, default: Any) -> Any:
    """Parse a JSON column value, falling back to `default` for null/invalid data."""
    if not value:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def norm_kind(value: Any) -> str:
    kind = clean_str(value).lower() or "note"
    return kind if kind in KINDS else "note"


def norm_status(value: Any) -> str:
    status = clean_str(value).lower() or "open"
    return status if status in STATUSES else "open"


def norm_priority(value: Any) -> str:
    priority = clean_str(value).lower() or "medium"
    return priority if priority in PRIORITIES else "medium"


def clean_tags(tags: Any) -> str:
    """Normalize tags (list or JSON text) into a JSON array string."""
    parsed = loads_json(tags, [])
    if not isinstance(parsed, list):
        return "[]"
    cleaned = [str(t).strip() for t in parsed if isinstance(t, str) and t.strip()]
    return json.dumps(cleaned)


def norm_decision(value: Any) -> str:
    decision = clean_str(value).lower()
    return decision if decision in DECISIONS else ""


def norm_section_kind(value: Any) -> str:
    kind = clean_str(value).lower()
    return kind if kind in SECTION_KINDS else "notes"


def norm_type(value: Any) -> str:
    ftype = clean_str(value).lower() or "text"
    return ftype if ftype in FIELD_TYPES else "text"


def as_number(value: Any) -> Optional[float]:
    """``value`` as a number (int when whole): 4500, "4,500", "₪ 13,400" → 4500 / 13400;
    None when it is not one number ("380-390", "", None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = re.sub(r"[\s,₪$€£]|ש\"?ח|nis|ils", "", clean_str(value), flags=re.IGNORECASE)
        if not re.fullmatch(r"-?\d+(\.\d+)?", text):
            return None
        number = float(text)
    return int(number) if number.is_integer() else number


def clean_value(ftype: str, value: Any) -> Any:
    """One field value normalised for its type. A number / money / rating value that is
    not a number is kept as text rather than lost."""
    if ftype in ("number", "money", "rating"):
        number = as_number(value)
        if number is not None:
            return max(0, min(5, number)) if ftype == "rating" else number
        return clean_str(value) or None
    if ftype == "checkbox":
        return value is True or clean_str(value).lower() in ("1", "true", "yes", "כן")
    if ftype == "date":
        return clean_str(value) or None
    return clean_str(value)


def clean_fields(fields: Any) -> str:
    """Normalize structured fields into a JSON array of {name, type, value} objects."""
    parsed = loads_json(fields, [])
    cleaned = []
    for raw in parsed if isinstance(parsed, list) else []:
        if not isinstance(raw, dict):
            continue
        name = clean_str(raw.get("name"))
        if not name:
            continue
        ftype = norm_type(raw.get("type"))
        cleaned.append({"name": name, "type": ftype, "value": clean_value(ftype, raw.get("value"))})
    return json.dumps(cleaned)


def clean_columns(columns: Any, previous: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """A section's columns as [{key, name, type, role?, best?, options?}]. A column keeps
    its ``key`` across renames (a column given without one takes the key of the previous
    column with its name, else a new one); names are unique (later duplicates dropped)."""
    parsed = loads_json(columns, [])
    by_name = {c["name"].lower(): c["key"] for c in previous or [] if isinstance(c, dict) and c.get("name")}
    known = {c.get("key") for c in previous or [] if isinstance(c, dict)}
    cleaned: List[Dict[str, Any]] = []
    seen = set()
    for raw in parsed if isinstance(parsed, list) else []:
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            continue
        name = clean_str(raw.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        key = clean_str(raw.get("key"))
        if not key or (previous is not None and key not in known):
            key = by_name.get(name.lower()) or key or uuid.uuid4().hex[:8]
        column: Dict[str, Any] = {"key": key, "name": name, "type": norm_type(raw.get("type"))}
        role = clean_str(raw.get("role")).lower()
        if role in COLUMN_ROLES and role:
            column["role"] = role
        best = clean_str(raw.get("best")).lower()
        if best in COLUMN_BEST and best:
            column["best"] = best
        if column["type"] == "select":
            options = [clean_str(o) for o in raw.get("options") or [] if clean_str(o)]
            if options:
                column["options"] = options
        cleaned.append(column)
    return cleaned


def _public(row: Any) -> Dict[str, Any]:
    return {k: v for k, v in dict(row).items() if k not in OWNER_COLUMNS}


def serialize_entry(row: Any) -> Dict[str, Any]:
    """An entry row as API payloads show it (owner columns left out)."""
    entry = _public(row)
    entry["tags"] = loads_json(entry.get("tags"), [])
    entry["fields"] = loads_json(entry.get("fields"), [])
    entry["comments"] = loads_json(entry.get("comments"), [])
    return entry


def serialize_project(row: Any) -> Dict[str, Any]:
    """A project row as API payloads show it (owner columns left out)."""
    project = _public(row)
    project["tags"] = loads_json(project.get("tags"), [])
    return project


def serialize_section(row: Any) -> Dict[str, Any]:
    """A section row as API payloads show it."""
    section = dict(row)
    section["columns"] = loads_json(section.get("columns"), [])
    return section


def serialize_file(row: Any) -> Dict[str, Any]:
    """An entry file row as API payloads show it: no stored name, plus its ``url``."""
    item = {k: v for k, v in dict(row).items() if k not in ("stored", "created_by")}
    item["url"] = f"/api/entries/{item['entry_id']}/files/{item['id']}"
    return item


def with_share(payload: Dict[str, Any], shared_by: Optional[str], level: Optional[str]) -> Dict[str, Any]:
    """``payload`` plus ``shared_by`` / ``access`` when it is seen through a share."""
    if shared_by is not None and level:
        payload["shared_by"] = shared_by
        payload["access"] = level
    return payload
