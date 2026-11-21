"""Each user's copy of their Google contacts (tables ``google_contacts``,
``google_contact_phones``, ``google_contacts_sync``; migration
``m0023_google_contacts``).

The copy is written only from what Google returned (a sync, or the answer to
a contact Dana added or changed) and read by the tools, the caller-name
lookup and the people fill. Every function answers for one user: another
user's contact id answers like an unknown one.

A contact here is a dict: ``id`` (Google's resource name, ``people/c…``),
``etag``, ``name``, ``other_names``, ``phones`` (``{number, value, type}``;
``number`` is normalised like a person's numbers, '' when it is not one),
``emails`` (``{value, type}``), ``birthday`` (``YYYY-MM-DD`` / ``MM-DD``),
``organisation``, ``job_title``, ``updated_at``.
"""

import difflib
import json
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import clock
from core.cards import loads
from core.db import get_db_connection
from core.text import (HEBREW, clean_list, looks_like_number, norm_name, norm_phone,
                       transliterate_he_to_en as translit, valid_birthday)
from core.users import User
from addons.contacts.config import ADDON
from core.addons import store as connections


def now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")

Contact = Dict[str, Any]

RESOURCE_PREFIX = "people/"
FUZZY_MIN = 0.8


def has_contacts(user: Optional[User]) -> bool:
    return user is not None and connections.is_connected(user, ADDON)


def contact_id(value: Any) -> str:
    """A contact id as given by a tool caller (``people/c12`` or ``c12``) as the resource name."""
    raw = str(value or "").strip()
    return raw if not raw or raw.startswith(RESOURCE_PREFIX) else RESOURCE_PREFIX + raw


# --- From Google ------------------------------------------------------------------------

def _birthday(person: Dict[str, Any]) -> str:
    for entry in person.get("birthdays") or []:
        date = entry.get("date") or {}
        month, day, year = date.get("month"), date.get("day"), date.get("year")
        if not month or not day:
            continue
        try:
            return valid_birthday(f"{int(year):04d}-{int(month):02d}-{int(day):02d}" if year
                                  else f"{int(month):02d}-{int(day):02d}")
        except (TypeError, ValueError):
            continue
    return ""


def parse(person: Dict[str, Any]) -> Contact:
    """A People API person as a contact (see the module docstring). A contact
    without a name is called by its organisation."""
    names = [str(n.get("displayName") or " ".join(x for x in (n.get("givenName"), n.get("familyName")) if x)).strip()
             for n in person.get("names") or []]
    nicknames = [str(n.get("value") or "").strip() for n in person.get("nicknames") or []]
    org = next((o for o in person.get("organizations") or [] if o.get("name") or o.get("title")), {})
    organisation = str(org.get("name") or "").strip()
    named = clean_list([*names, *nicknames])
    name = named[0] if named else organisation
    phones = []
    for p in person.get("phoneNumbers") or []:
        value = str(p.get("value") or "").strip()
        if value:
            phones.append({"number": norm_phone(p.get("canonicalForm")) or norm_phone(value), "value": value,
                           "type": str(p.get("type") or "")})
    emails = [{"value": str(e.get("value") or "").strip(), "type": str(e.get("type") or "")}
              for e in person.get("emailAddresses") or [] if str(e.get("value") or "").strip()]
    return {"id": str(person.get("resourceName") or ""), "etag": str(person.get("etag") or ""), "name": name[:200],
            "other_names": named[1:], "phones": phones, "emails": emails, "birthday": _birthday(person),
            "organisation": organisation[:200], "job_title": str(org.get("title") or "").strip()[:200]}


# --- Rows -------------------------------------------------------------------------------

def _contact(r: sqlite3.Row) -> Contact:
    return {"id": r["resource_name"], "etag": r["etag"], "name": r["name"], "other_names": loads(r["other_names"], []),
            "phones": loads(r["phones"], []), "emails": loads(r["emails"], []), "birthday": r["birthday"],
            "organisation": r["organisation"], "job_title": r["job_title"], "updated_at": r["updated_at"]}


def numbers_of(contact: Contact) -> List[str]:
    """The contact's normalised numbers, in order, each once."""
    out: List[str] = []
    for p in contact["phones"]:
        if p.get("number") and p["number"] not in out:
            out.append(p["number"])
    return out


def _upsert(conn: sqlite3.Connection, user: User, contact: Contact) -> None:
    conn.execute(
        "INSERT INTO google_contacts (user_id, resource_name, etag, name, other_names, phones, emails, birthday, "
        "organisation, job_title, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, resource_name) DO UPDATE SET etag = excluded.etag, name = excluded.name, "
        "other_names = excluded.other_names, phones = excluded.phones, emails = excluded.emails, "
        "birthday = excluded.birthday, organisation = excluded.organisation, job_title = excluded.job_title, "
        "updated_at = excluded.updated_at",
        (user.id, contact["id"], contact["etag"], contact["name"],
         json.dumps(contact["other_names"], ensure_ascii=False), json.dumps(contact["phones"], ensure_ascii=False),
         json.dumps(contact["emails"], ensure_ascii=False), contact["birthday"], contact["organisation"],
         contact["job_title"], now_iso()))
    conn.execute("DELETE FROM google_contact_phones WHERE user_id = ? AND resource_name = ?", (user.id, contact["id"]))
    conn.executemany("INSERT INTO google_contact_phones (user_id, phone, resource_name) VALUES (?, ?, ?)",
                     [(user.id, number, contact["id"]) for number in numbers_of(contact)])


def _delete(conn: sqlite3.Connection, user: User, resource_name: str) -> int:
    conn.execute("DELETE FROM google_contact_phones WHERE user_id = ? AND resource_name = ?",
                 (user.id, resource_name))
    return conn.execute("DELETE FROM google_contacts WHERE user_id = ? AND resource_name = ?",
                        (user.id, resource_name)).rowcount


def get(user: User, contact: Any) -> Optional[Contact]:
    """``user``'s contact with this id (None for anyone else's or an unknown id)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM google_contacts WHERE user_id = ? AND resource_name = ?",
                         (user.id, contact_id(contact))).fetchone()
    return _contact(r) if r else None


def all_contacts(user: User, conn: Optional[sqlite3.Connection] = None) -> List[Contact]:
    """Every contact of ``user``'s copy, by name."""
    db = conn or get_db_connection()
    return [_contact(r) for r in db.execute(
        "SELECT * FROM google_contacts WHERE user_id = ? ORDER BY name COLLATE NOCASE, resource_name", (user.id,))]


def count(user: User) -> int:
    with get_db_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM google_contacts WHERE user_id = ?", (user.id,)).fetchone()[0]


def save(user: User, person: Dict[str, Any]) -> Contact:
    """Store one contact as Google returned it (after Dana added or changed it)."""
    contact = parse(person)
    with get_db_connection() as conn:
        _upsert(conn, user, contact)
        conn.commit()
    stored = get(user, contact["id"])
    assert stored is not None
    return stored


def remove(user: User, contact: Any) -> bool:
    """Drop one contact from ``user``'s copy (Google no longer has it)."""
    with get_db_connection() as conn:
        removed = _delete(conn, user, contact_id(contact))
        conn.commit()
    return bool(removed)


# --- Sync state -------------------------------------------------------------------------

def sync_token(user: User) -> str:
    """The People API sync token of ``user``'s copy ('' before the first sync)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT sync_token FROM google_contacts_sync WHERE user_id = ?", (user.id,)).fetchone()
    return r["sync_token"] if r else ""


def synced_at(user: User) -> Optional[str]:
    """When ``user``'s copy was last brought up to date (None: never)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT synced_at FROM google_contacts_sync WHERE user_id = ?", (user.id,)).fetchone()
    return r["synced_at"] if r else None


def forget_sync_token(user: User) -> None:
    """Make ``user``'s next sync a full one (their Google account may be another one now)."""
    with get_db_connection() as conn:
        conn.execute("UPDATE google_contacts_sync SET sync_token = '' WHERE user_id = ?", (user.id,))
        conn.commit()


def apply_sync(user: User, people: Iterable[Dict[str, Any]], token: str, *, full: bool) -> Dict[str, int]:
    """Write one sync result into ``user``'s copy: contacts changed are
    stored, deleted ones removed and, after a ``full`` read, every contact
    Google did not return removed too; then the sync token and time.

    Returns:
        ``{"changed", "removed", "contacts"}`` (stored now, removed now, in the copy).
    """
    changed = removed = 0
    seen: List[str] = []
    with get_db_connection() as conn:
        for person in people:
            resource = str(person.get("resourceName") or "")
            if not resource:
                continue
            if (person.get("metadata") or {}).get("deleted"):
                removed += _delete(conn, user, resource)
                continue
            _upsert(conn, user, parse(person))
            seen.append(resource)
            changed += 1
        if full:
            kept = set(seen)
            stale = [r["resource_name"] for r in conn.execute(
                "SELECT resource_name FROM google_contacts WHERE user_id = ?", (user.id,))
                if r["resource_name"] not in kept]
            for resource in stale:
                removed += _delete(conn, user, resource)
        conn.execute("INSERT INTO google_contacts_sync (user_id, sync_token, synced_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET sync_token = excluded.sync_token, "
                     "synced_at = excluded.synced_at", (user.id, token, now_iso()))
        total = conn.execute("SELECT COUNT(*) FROM google_contacts WHERE user_id = ?", (user.id,)).fetchone()[0]
        conn.commit()
    return {"changed": changed, "removed": removed, "contacts": total}


def clear(user: User) -> None:
    """Delete ``user``'s copy and sync token (the addon was disconnected)."""
    with get_db_connection() as conn:
        for table in ("google_contact_phones", "google_contacts", "google_contacts_sync"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user.id,))
        conn.commit()


# --- Lookups ----------------------------------------------------------------------------

def by_number(user: User, conn: sqlite3.Connection) -> Dict[str, List[str]]:
    """number → the ids of ``user``'s contacts that have it."""
    out: Dict[str, List[str]] = {}
    for r in conn.execute("SELECT phone, resource_name FROM google_contact_phones WHERE user_id = ? "
                          "ORDER BY resource_name", (user.id,)):
        out.setdefault(r["phone"], []).append(r["resource_name"])
    return out


def caller_name(user: Optional[User], phone: str) -> Optional[str]:
    """The name ``user``'s Google contacts give the number ``phone`` (exact,
    else a match on the last 9 digits). None without the ``contacts`` addon,
    for an unknown number and when contacts with different names share it."""
    number = norm_phone(phone)
    if user is None or not number or not has_contacts(user):
        return None
    sql = ("SELECT c.name FROM google_contact_phones p JOIN google_contacts c "
           "ON c.user_id = p.user_id AND c.resource_name = p.resource_name WHERE p.user_id = ? AND ")
    with get_db_connection() as conn:
        found = conn.execute(sql + "p.phone = ?", (user.id, number)).fetchall()
        if not found:
            # Stored without the country code (or with another prefix): the last 9 digits.
            found = conn.execute(sql + "substr(p.phone, -9) = ?", (user.id, number[-9:])).fetchall()
    names = {norm_name(r["name"]): r["name"] for r in found if r["name"] and not looks_like_number(r["name"])}
    return next(iter(names.values())) if len(names) == 1 else None


def _skeleton(key: str) -> str:
    """The consonants of a Latin-letter name, so a name and the transliteration
    of its Hebrew spelling compare equal ('gal cohen' and 'gl khn' → 'gl kn')."""
    words = (re.sub(r"[aeiouyh]", "", w) for w in key.replace("ph", "f").replace("c", "k").replace("q", "k").split())
    return " ".join(w for w in words if w)


def _words_in(needle: str, hay: str) -> bool:
    return f" {needle} " in f" {hay} "


def _name_score(query: str, names: List[str]) -> float:
    """How well ``query`` names a contact called ``names``: 100 the name
    itself, 85 whole words of it, 70 the start of a word, 55 inside it; in the
    other script (Hebrew ↔ Latin letters) 60 / 50 by consonants; 0 no match."""
    q, q_hebrew = norm_name(query), bool(HEBREW.search(query))
    if not q:
        return 0.0
    best = 0.0
    for name in names:
        k = norm_name(name)
        if not k:
            continue
        if bool(HEBREW.search(name)) == q_hebrew:
            if q == k:
                score = 100.0
            elif _words_in(q, k):
                score = 85.0
            elif all(any(w.startswith(part) for w in k.split()) for part in q.split()):
                score = 70.0
            elif len(q) >= 3 and q in k:
                score = 55.0
            else:
                score = 0.0
        else:
            a, b = _skeleton(norm_name(translit(q))), _skeleton(norm_name(translit(k)))
            score = 0.0 if len(a) < 2 else 60.0 if a == b else 50.0 if _words_in(a, b) else 0.0
        best = max(best, score)
    return best


def _fuzzy_score(query: str, names: List[str]) -> float:
    q = norm_name(query)
    ratio = max((difflib.SequenceMatcher(None, q, k).ratio() for k in map(norm_name, names) if k), default=0.0)
    return 40.0 * ratio if ratio >= FUZZY_MIN else 0.0


def _number_score(query: str, contact: Contact) -> float:
    digits = re.sub(r"\D", "", query)
    if len(digits) < 4:
        return 0.0
    number, best = norm_phone(query), 0.0
    for p in contact["phones"]:
        have = p.get("number") or re.sub(r"\D", "", p.get("value") or "")
        if number and have == number:
            best = max(best, 100.0)
        elif have.endswith(digits[-9:]):
            best = max(best, 80.0)
        elif digits in have:
            best = max(best, 60.0)
    return best


def _email_score(query: str, contact: Contact) -> float:
    q = query.strip().lower()
    if len(q) < 3:
        return 0.0
    values = [str(e.get("value") or "").lower() for e in contact["emails"]]
    return 100.0 if q in values else 75.0 if any(q in v for v in values) else 0.0


def search(user: User, query: str, limit: int = 10) -> List[Contact]:
    """``user``'s contacts matching ``query``: a name or part of it (Hebrew or
    English, any case or punctuation; the other script by its consonants), a
    phone number or an email address. Best match first; near-miss spellings
    only when nothing else matched."""
    q = (query or "").strip()
    if not q:
        return []
    everyone = all_contacts(user)
    scored: List[Tuple[float, Contact]] = []
    for c in everyone:
        names = [c["name"], *c["other_names"], c["organisation"]]
        score = max(_number_score(q, c) if looks_like_number(q) else 0.0, _email_score(q, c),
                    0.0 if looks_like_number(q) else _name_score(q, names))
        if score:
            scored.append((score, c))
    if not scored and not looks_like_number(q) and len(norm_name(q)) >= 3:
        scored = [(s, c) for c in everyone
                  if (s := _fuzzy_score(q, [c["name"], *c["other_names"], c["organisation"]]))]
    scored.sort(key=lambda sc: (-sc[0], norm_name(sc[1]["name"]), sc[1]["id"]))
    return [c for _, c in scored[:max(1, min(int(limit or 10), 50))]]
