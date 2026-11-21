"""One search box over what Dana keeps for one user: what each addon's
``@addon.search`` finds (the tasks addon: their open reminders, own and on
lists shared with them), calls, people, notes (entries and projects: own and
shared), active notifications and their active shopping list. An addon they
have not connected adds nothing. Plain SQLite (FTS where it
exists, LIKE elsewhere), no model, Hebrew and English.

``search(user, q, limit)`` → ``[{kind, id, title, subtitle, …}]``, at most
``limit`` per addon (notifications add ``package``, notes ``project_id``), best
matches first across kinds (exact title > title prefix > a word of the title
starts with it > contains > matched only in the body).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core import clock, now as now_screen
from core.db import get_db_connection
from core.cards import loads
from core.users import User
from core.addons import registry, store
from core.addons.helpers import ago, like_pattern, match_score, search_terms
from addons.knowledge import entries as knowledge_entries, projects as knowledge_projects
from addons.people import store as people_store
from addons.people.values import norm_name, norm_phone, parse_time
from addons.phone.config import ADDON as PHONE
from addons.phone.notifications import store as notification_store
from addons.shopping import items as shopping_items, lists as shopping_lists

# Core's own kinds, in the order that breaks a tie of scores (the addons' hooks come first, in addon order).
KIND_ORDER = ("person", "call", "note", "notification", "shopping")


def _fts(q: str) -> str:
    return " ".join(f'"{t}"*' for t in search_terms(q))


def _local_ago(value: Any) -> str:
    """For timestamps stored as local time (call_timestamp, posted_at)."""
    dt = parse_time(value)
    return ago(dt, clock.now()) if dt else ""


def _calls(conn, user: User, q: str, limit: int) -> List[Dict[str, Any]]:
    """``user``'s own calls (none without ``phone``)."""
    if not store.is_connected(user, PHONE):
        return []
    uid = user.id
    found: Dict[str, float] = {}
    fts = _fts(q)
    if fts:
        try:
            for r in conn.execute("SELECT f.id FROM phone_calls_fts f JOIN phone_calls c ON c.id = f.id "
                                  "WHERE phone_calls_fts MATCH ? AND c.user_id = ? ORDER BY rank LIMIT 30",
                                  (fts, uid)).fetchall():
                found.setdefault(r["id"], 0.5)
        except Exception:
            pass
    like = like_pattern(q)
    for r in conn.execute("SELECT id FROM phone_calls WHERE user_id = ? AND (caller LIKE ? ESCAPE '\\' "
                          "OR receiver LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\') "
                          "LIMIT 30", (uid, like, like, like, like)).fetchall():
        found.setdefault(r["id"], 0.5)
    if not found:
        return []
    ids = list(found)
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, caller, receiver, title, call_timestamp, duration_seconds FROM phone_calls "
                        f"WHERE id IN ({marks}) AND user_id = ?", (*ids, uid)).fetchall()
    out = []
    for r in rows:
        contact = r["receiver"] if r["caller"] == "Me" else r["caller"]
        score = max(match_score(contact or "", q), match_score(r["title"] or "", q))
        if score <= 0.5 and q.casefold() not in f"{contact} {r['title']}".casefold():
            score = 0.5
        mins = int(r["duration_seconds"] or 0) // 60
        sub = " · ".join(x for x in (contact, _local_ago(r["call_timestamp"]), f"{mins} min" if mins else "") if x)
        out.append({"kind": "call", "id": r["id"], "title": r["title"] or f"Call with {contact}", "subtitle": sub,
                    "_score": score, "_when": r["call_timestamp"] or ""})
    out.sort(key=lambda x: x["_when"], reverse=True)  # newest first among equals
    out.sort(key=lambda x: -x["_score"])
    return out[:limit]


def _people(conn, user: User, q: str, limit: int) -> List[Dict[str, Any]]:
    """``user``'s people (none without ``people``)."""
    qn = norm_name(q)
    qp = norm_phone(q) if re.search(r"\d{3}", q or "") else ""
    phones: Dict[str, List[str]] = people_store.phones_by_person(conn, user) if qp else {}
    out = []
    for r in people_store.rows(conn, user, include_hidden=False):
        names = people_store.names_of(r)
        hit_name = qn and any(qn in n for n in names)
        hit_phone = qp and any(qp in p for p in phones.get(r["id"], []))
        hit_rel = qn and qn in norm_name(r["relation"])
        if not (hit_name or hit_phone or hit_rel):
            continue
        score = max((match_score(n, qn) for n in names), default=0.5) if hit_name else 1.0
        out.append({"kind": "person", "id": r["id"], "title": r["name"], "subtitle": r["relation"] or "Person",
                    "_score": score + 0.1})
    return sorted(out, key=lambda x: -x["_score"])[:limit]


def _notes(conn, user: User, q: str, limit: int) -> List[Dict[str, Any]]:
    """Entries and projects ``user`` can see: own and shared (none without ``knowledge``)."""
    out = []
    for r in knowledge_entries.search_rows(user, knowledge_entries.matching_ids(user, _fts(q), like_pattern(q))):
        out.append({"kind": "note", "id": r["id"], "title": r["title"],
                    "subtitle": r["project"] or (r["kind"] or "note").capitalize(), "project_id": r["project_id"],
                    "_score": match_score(r["title"], q) if q.casefold() in (r["title"] or "").casefold() else 0.5})
    for r in knowledge_projects.search_titles(user, like_pattern(q)):
        out.append({"kind": "note", "id": f"project:{r['id']}", "title": r["title"], "subtitle": "Project",
                    "project_id": r["id"], "_score": match_score(r["title"], q)})
    return sorted(out, key=lambda x: -x["_score"])[:limit]


def _notifications(user: User, q: str, limit: int) -> List[Dict[str, Any]]:
    """``user``'s active notifications (none without ``phone``)."""
    if not store.is_connected(user, PHONE):
        return []
    out = []
    for n in notification_store.list_notifications(user, status="active", q=q, limit=30, with_suggestions=False):
        # The same headline / sender split as the Now card.
        parts = now_screen.notification_parts(n)
        title = " ".join((parts["headline"] or "").split())[:140]
        out.append({"kind": "notification", "id": n["id"], "title": title,
                    "subtitle": " · ".join(x for x in (n.get("app_name"), parts["who"], _local_ago(n.get("posted_at"))) if x),
                    "package": n["package"],
                    "_score": max(match_score(title, q) if q.casefold() in title.casefold() else 0.5,
                                  match_score(parts["who"], q) if parts["who"] and q.casefold() in parts["who"].casefold() else 0.5)})
    return sorted(out, key=lambda x: -x["_score"])[:limit]


def _shopping(conn, user: User, q: str, limit: int) -> List[Dict[str, Any]]:
    """Items of ``user``'s active shopping list: theirs or one shared with them to edit (none without ``shopping``)."""
    if not store.is_connected(user, shopping_lists.ADDON):
        return []
    rows = shopping_items.search(shopping_lists.active_list(user), like_pattern(q))
    return [{"kind": "shopping", "id": r["id"], "title": r["name"],
             "subtitle": "Bought" if r["checked"] else "On the list",
             "_score": match_score(r["name"], q) - (0.3 if r["checked"] else 0)} for r in rows][:limit]


def search(user: User, q: str, limit: int = 5) -> List[Dict[str, Any]]:
    """``user``'s items matching ``q`` across their addons."""
    q = (q or "").strip()[:100]
    if not q:
        return []
    limit = max(1, min(int(limit or 5), 20))
    results: List[Dict[str, Any]] = []
    kinds: List[str] = []
    for hook in registry.hooks("search", user):
        try:
            hits = hook.func(user, q, limit) or []
        except Exception:
            continue  # one addon's search failing leaves the others
        results += [{**h.to_json(), "_score": h.score} for h in hits]
        kinds += [h.kind for h in hits if h.kind not in kinds]
    with get_db_connection() as conn:
        for fn in (_people, _calls, _notes, _shopping):
            try:
                results += fn(conn, user, q, limit)
            except Exception:
                continue  # a table that doesn't exist yet (fresh DB) is not an error
    try:
        results += _notifications(user, q, limit)
    except Exception:
        pass
    order = {k: i for i, k in enumerate(kinds + [k for k in KIND_ORDER if k not in kinds])}
    results.sort(key=lambda r: (-r["_score"], order.get(r["kind"], 9)))
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
