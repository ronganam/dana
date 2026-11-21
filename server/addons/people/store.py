"""People rows: each user's people and their phone numbers.

``people`` (name, other names, relation, notes, birthday, emails, pinned,
hidden, source, the cached brief, the Google contact it was filled in from)
and ``person_phones`` (normalised digits) belong to a user (``user_id``,
migration ``m0010_people_per_user``). A number is unique
per user, not across users: two users may each have a person with the same
number. Every lookup is scoped to the user; another user's person id answers
like an unknown one (None / LookupError). No sharing for this addon.
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.db import get_db_connection
from core.users import User
from core.addons import store as connections
from addons.people import activity
from addons.people.values import (HEBREW, ADDON, clean_emails, clean_list, initials, loads,
                                        looks_like_number, norm_name, norm_phone, now_iso, translit,
                                        valid_birthday)
from addons.tasks import links as task_links


# --- Scope -----------------------------------------------------------------------------

def has_people(user: Optional[User]) -> bool:
    return user is not None and connections.is_connected(user, ADDON)


def owned_sql(user: Optional[User], alias: str = "") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for ``user``'s own people (or
    phones); nothing without a user or without ``people``. ``alias``: the
    table's alias ("" for none)."""
    if not has_people(user):
        return "0", []
    assert user is not None
    return f"{alias + '.' if alias else ''}user_id = ?", [user.id]


# --- Rows ------------------------------------------------------------------------------

def row(conn: sqlite3.Connection, user: Optional[User], person_id: str) -> Optional[sqlite3.Row]:
    """``user``'s person ``person_id`` (None for anyone else's or an unknown id)."""
    where, args = owned_sql(user)
    return conn.execute(f"SELECT * FROM people WHERE id = ? AND {where}", (person_id, *args)).fetchone()


def rows(conn: sqlite3.Connection, user: Optional[User], *, include_hidden: bool = True) -> List[sqlite3.Row]:
    where, args = owned_sql(user)
    return conn.execute(f"SELECT * FROM people WHERE {where}" + ("" if include_hidden else " AND hidden = 0"),
                        args).fetchall()


def owns(user: Optional[User], person_id: str) -> bool:
    """Whether ``person_id`` is one of ``user``'s people (what a task link to a person needs)."""
    with get_db_connection() as conn:
        return row(conn, user, person_id) is not None


def phones_of(conn: sqlite3.Connection, person_id: str) -> List[str]:
    return [r["phone"] for r in conn.execute(
        "SELECT phone FROM person_phones WHERE person_id = ? ORDER BY created_at", (person_id,))]


def phones_by_person(conn: sqlite3.Connection, user: Optional[User]) -> Dict[str, List[str]]:
    """person id → their numbers (oldest first), for ``user``'s people."""
    where, args = owned_sql(user)
    out: Dict[str, List[str]] = {}
    for r in conn.execute(f"SELECT phone, person_id FROM person_phones WHERE {where} ORDER BY created_at", args):
        out.setdefault(r["person_id"], []).append(r["phone"])
    return out


def to_api(r: sqlite3.Row, conn: Optional[sqlite3.Connection] = None, *,
           phones: Optional[List[str]] = None) -> Dict[str, Any]:
    if phones is None:
        if conn is None:
            with get_db_connection() as c:
                phones = phones_of(c, r["id"])
        else:
            phones = phones_of(conn, r["id"])
    brief = loads(r["brief"], {})
    return {
        "id": r["id"], "name": r["name"], "aliases": loads(r["aliases"], []),
        "phones": phones, "relation": r["relation"] or "", "notes": r["notes"] or "",
        "birthday": r["birthday"] or "", "emails": loads(r["emails"], []),
        "pinned": bool(r["pinned"]), "hidden": bool(r["hidden"]),
        "source": r["source"], "initials": initials(r["name"]),
        "brief": {"points": brief.get("points") or [], "open": brief.get("open") or [],
                  "updated_at": r["brief_updated_at"]} if brief else None,
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def names_of(r: Any) -> List[str]:
    """The person's name + other names, normalised, without duplicates."""
    out: List[str] = []
    for n in [r["name"], *loads(r["aliases"], [])]:
        k = norm_name(n)
        if k and k not in out:
            out.append(k)
    return out


def alias_map(conn: sqlite3.Connection, user: User) -> Dict[str, Optional[str]]:
    """normalised name → ``user``'s person id (None when two of their people share it: ambiguous)."""
    where, args = owned_sql(user)
    out: Dict[str, Optional[str]] = {}
    for r in conn.execute(f"SELECT id, name, aliases FROM people WHERE {where}", args):
        for k in names_of(r):
            if k in out and out[k] != r["id"]:
                out[k] = None
            else:
                out[k] = r["id"]
    return out


def phone_map(conn: sqlite3.Connection, user: User) -> Dict[str, str]:
    """number → ``user``'s person id."""
    where, args = owned_sql(user)
    return {r["phone"]: r["person_id"]
            for r in conn.execute(f"SELECT phone, person_id FROM person_phones WHERE {where}", args)}


# --- Create / update / delete -----------------------------------------------------------

def add_phone(conn: sqlite3.Connection, user: User, person_id: str, phone: str) -> bool:
    """Give ``user``'s person ``person_id`` the number ``phone`` (False: not a number;
    ValueError: another of ``user``'s people has it)."""
    p = norm_phone(phone)
    if not p:
        return False
    holder = conn.execute("SELECT person_id FROM person_phones WHERE user_id = ? AND phone = ?",
                          (user.id, p)).fetchone()
    if holder and holder["person_id"] != person_id:
        raise ValueError(f"{phone} already belongs to someone else")
    conn.execute("INSERT OR IGNORE INTO person_phones (user_id, phone, person_id, created_at) VALUES (?, ?, ?, ?)",
                 (user.id, p, person_id, now_iso()))
    return True


def create_person(user: User, name: str, *, phones: Iterable[str] = (), aliases: Iterable[str] = (),
                  relation: str = "", notes: str = "", birthday: str = "", pinned: bool = False,
                  hidden: bool = False, source: str = "manual", conn: Optional[sqlite3.Connection] = None,
                  recheck: bool = True) -> Dict[str, Any]:
    """A new person of ``user``'s. With ``conn`` it runs in the caller's transaction."""
    name = re.sub(r"\s+", " ", name or "").strip()[:120]
    if not name:
        raise ValueError("name is required")
    pid = str(uuid.uuid4())
    now = now_iso()
    own = conn is None
    conn = conn or get_db_connection()
    try:
        conn.execute(
            "INSERT INTO people (id, user_id, name, aliases, relation, notes, birthday, pinned, hidden, source, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, user.id, name,
             json.dumps([a for a in clean_list(aliases) if norm_name(a) != norm_name(name)], ensure_ascii=False),
             (relation or "").strip()[:80], (notes or "").strip()[:4000], valid_birthday(birthday),
             1 if pinned else 0, 1 if hidden else 0, source, now, now))
        for p in phones or []:
            add_phone(conn, user, pid, p)
        if recheck:
            activity.recheck_unmatched(conn, user)
        if own:
            conn.commit()
        found = row(conn, user, pid)
        assert found is not None
        return to_api(found, conn)
    finally:
        if own:
            conn.close()


def update_person(user: User, person_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    """Fields: name, aliases (list, replaces), add_alias, phones (list, replaces), add_phone,
    relation, notes, birthday, emails (list, replaces), pinned, hidden. LookupError when it is
    not ``user``'s person."""
    with get_db_connection() as conn:
        r = row(conn, user, person_id)
        if not r:
            raise LookupError("Person not found")
        sets: Dict[str, Any] = {}
        if "name" in changes and str(changes["name"] or "").strip():
            sets["name"] = re.sub(r"\s+", " ", str(changes["name"])).strip()[:120]
        aliases = loads(r["aliases"], [])
        if isinstance(changes.get("aliases"), list):
            aliases = clean_list(changes["aliases"])
        if changes.get("add_alias"):
            aliases = clean_list([*aliases, changes["add_alias"]])
        name = sets.get("name", r["name"])
        if "aliases" in changes or changes.get("add_alias") or "name" in sets:
            if "name" in sets and norm_name(r["name"]) != norm_name(name):
                aliases = clean_list([*aliases, r["name"]])  # keep the old name for matching
            sets["aliases"] = json.dumps([a for a in aliases if norm_name(a) != norm_name(name)], ensure_ascii=False)
        for f, limit in (("relation", 80), ("notes", 4000)):
            if f in changes and changes[f] is not None:
                sets[f] = str(changes[f]).strip()[:limit]
        if "birthday" in changes and changes["birthday"] is not None:
            sets["birthday"] = valid_birthday(str(changes["birthday"]))
        if isinstance(changes.get("emails"), list):
            sets["emails"] = json.dumps(clean_emails(changes["emails"]), ensure_ascii=False)
        for f in ("pinned", "hidden"):
            if f in changes and changes[f] is not None:
                sets[f] = 1 if changes[f] else 0
        if isinstance(changes.get("phones"), list):
            wanted = {norm_phone(p) for p in changes["phones"]} - {""}
            for p in phones_of(conn, person_id):
                if p not in wanted:
                    conn.execute("DELETE FROM person_phones WHERE user_id = ? AND phone = ?", (user.id, p))
                    activity.recheck_calls_of(conn, person_id)
            for p in wanted:
                add_phone(conn, user, person_id, p)
        if changes.get("add_phone"):
            if not add_phone(conn, user, person_id, str(changes["add_phone"])):
                raise ValueError("not a phone number")
        if sets:
            sets["updated_at"] = now_iso()
            conn.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                         (*sets.values(), person_id))
        if "aliases" in sets or "name" in sets:
            activity.recheck_notifications_of(conn, person_id)
        activity.recheck_unmatched(conn, user)
        conn.commit()
        updated = row(conn, user, person_id)
        assert updated is not None
        return to_api(updated, conn)


def delete_person(user: User, person_id: str) -> bool:
    """Delete ``user``'s person (False when it is not theirs). Their activity is
    left unmatched and task links to them go."""
    with get_db_connection() as conn:
        if not row(conn, user, person_id):
            return False
        conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.execute("DELETE FROM person_phones WHERE person_id = ?", (person_id,))
        activity.unlink_person(conn, person_id)
        task_links.forget_ref("person", person_id, conn=conn)
        conn.commit()
    return True


# --- Google contacts --------------------------------------------------------------------

def fill_from_contact(conn: sqlite3.Connection, user: User, person: sqlite3.Row, *, resource: str, name: str = "",
                      phones: Iterable[str] = (), birthday: str = "", emails: Iterable[str] = ()) -> bool:
    """What the Google contact ``resource`` (matched by one of the person's
    numbers, addons/contacts/enrich.py) adds to ``user``'s ``person``:
    its name when the person is an auto-created number-only one (which is
    then shown), its numbers that none of ``user``'s people has, its birthday
    and emails when the person has none, and the contact's id. A name,
    birthday, email or note that is already there is never changed.

    Returns:
        Whether a name or a number was added (activity may now match).
    """
    sets: Dict[str, Any] = {}
    name = re.sub(r"\s+", " ", name or "").strip()[:120]
    named = bool(name and person["source"] == "auto" and looks_like_number(person["name"])
                 and not looks_like_number(name))
    if named:
        sets.update(name=name, hidden=0)
    if not person["birthday"] and birthday:
        try:
            sets["birthday"] = valid_birthday(birthday)
        except ValueError:
            pass
    found = clean_emails(emails)
    if found and not loads(person["emails"], []):
        sets["emails"] = json.dumps(found, ensure_ascii=False)
    added = False
    for number in phones:
        number = norm_phone(number)
        if number and conn.execute("SELECT 1 FROM person_phones WHERE user_id = ? AND phone = ?",
                                   (user.id, number)).fetchone() is None:
            added = add_phone(conn, user, person["id"], number) or added
    if sets or added:
        sets["updated_at"] = now_iso()
    if person["contact_resource"] != resource:
        sets["contact_resource"] = resource
    if sets:
        conn.execute(f"UPDATE people SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ? AND user_id = ?",
                     (*sets.values(), person["id"], user.id))
    return named or added


def unlink_contact(conn: sqlite3.Connection, user: User, person_id: str) -> None:
    """Forget which Google contact ``user``'s person was filled in from."""
    conn.execute("UPDATE people SET contact_resource = '' WHERE id = ? AND user_id = ?", (person_id, user.id))


def unlink_contacts(user: User) -> None:
    """Forget the Google contact of every one of ``user``'s people (what was filled in stays)."""
    with get_db_connection() as conn:
        conn.execute("UPDATE people SET contact_resource = '' WHERE user_id = ?", (user.id,))
        conn.commit()


# --- Lookups ---------------------------------------------------------------------------

def find_by_phone(user: Optional[User], phone: str) -> Optional[str]:
    """``user``'s person with this number (exact, else a unique match on the last 9 digits)."""
    p = norm_phone(phone)
    if not p:
        return None
    where, args = owned_sql(user)
    with get_db_connection() as conn:
        r = conn.execute(f"SELECT person_id FROM person_phones WHERE phone = ? AND {where}", (p, *args)).fetchone()
        if r:
            return r["person_id"]
        # Numbers stored without the country code (or with a different prefix): match on the last 9 digits.
        tail = p[-9:]
        found = conn.execute(f"SELECT person_id FROM person_phones WHERE substr(phone, -9) = ? AND {where}",
                             (tail, *args)).fetchall()
        return found[0]["person_id"] if len(found) == 1 else None


def resolve(user: Optional[User], query: str) -> Optional[str]:
    """``user``'s person id from an id, a phone number or a (partial, Hebrew or English) name."""
    q = (query or "").strip()
    if not q:
        return None
    with get_db_connection() as conn:
        if row(conn, user, q) is not None:
            return q
        everyone = rows(conn, user)
    if re.search(r"\d{5}", q):
        pid = find_by_phone(user, q)
        if pid:
            return pid
    qn = norm_name(q)
    qt = norm_name(translit(q)) if HEBREW.search(q) else qn
    exact = [r for r in everyone if qn in names_of(r) or qt in names_of(r)]
    if len(exact) >= 1:
        return sorted(exact, key=lambda r: r["hidden"])[0]["id"]
    words = [r for r in everyone if any(f" {qn} " in f" {k} " for k in names_of(r))]
    if words:
        return sorted(words, key=lambda r: (r["hidden"], not r["pinned"]))[0]["id"]
    best, score = None, 0.0
    for r in everyone:
        for k in names_of(r) + [norm_name(translit(n)) for n in names_of(r)]:
            s = max(difflib.SequenceMatcher(None, qn, k).ratio(), difflib.SequenceMatcher(None, qt, k).ratio())
            if s > score:
                best, score = r["id"], s
    return best if score >= 0.75 else None


def mentioned_in(user: Optional[User], text: str, *, limit: int = 3) -> List[str]:
    """``user``'s people whose name appears as whole words in ``text`` (event titles, attendee names)."""
    t = f" {norm_name(text)} "
    if not t.strip():
        return []
    with get_db_connection() as conn:
        visible = rows(conn, user, include_hidden=False)
    hits = [(max(len(k) for k in names_of(r) if f" {k} " in t), r["id"])
            for r in visible if any(len(k) >= 3 and f" {k} " in t for k in names_of(r))]
    return [pid for _, pid in sorted(hits, reverse=True)[:limit]]


def name_of(user: Optional[User], person_id: str) -> Optional[str]:
    """The display name of ``user``'s person (None for anyone else's)."""
    with get_db_connection() as conn:
        r = row(conn, user, person_id)
    return r["name"] if r else None
