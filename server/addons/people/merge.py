"""Merging two of a user's people, and the pairs that look like the same person.

Only within one user's people: the other user's people are never candidates,
and merging with a person who is not the user's answers "Person not found".
"""
from __future__ import annotations

import difflib
import json
from typing import Any, Dict, List

from core.db import get_db_connection
from core.users import User
from addons.people import activity, store
from addons.people.values import HEBREW, clean_emails, clean_list, loads, norm_name, now_iso, translit
from addons.tasks import links as task_links


def merge_people(user: User, keep_id: str, other_id: str) -> Dict[str, Any]:
    """Fold ``other`` into ``keep`` (both ``user``'s): names, numbers, emails, activity, task links, notes."""
    if keep_id == other_id:
        raise ValueError("can't merge a person into themselves")
    with get_db_connection() as conn:
        keep, other = store.row(conn, user, keep_id), store.row(conn, user, other_id)
        if not keep or not other:
            raise LookupError("Person not found")
        aliases = clean_list([*loads(keep["aliases"], []), other["name"], *loads(other["aliases"], [])])
        aliases = [a for a in aliases if norm_name(a) != norm_name(keep["name"])]
        notes = "\n".join(x for x in (keep["notes"], other["notes"]) if x).strip()
        emails = clean_emails([*loads(keep["emails"], []), *loads(other["emails"], [])])
        conn.execute(
            "UPDATE people SET aliases = ?, notes = ?, relation = ?, birthday = ?, emails = ?, contact_resource = ?, "
            "pinned = ?, hidden = ?, source = ?, brief_signature = NULL, updated_at = ? WHERE id = ?",
            (json.dumps(aliases, ensure_ascii=False), notes[:4000], keep["relation"] or other["relation"],
             keep["birthday"] or other["birthday"], json.dumps(emails, ensure_ascii=False),
             keep["contact_resource"] or other["contact_resource"],
             1 if (keep["pinned"] or other["pinned"]) else 0,
             1 if (keep["hidden"] and other["hidden"]) else 0,
             "manual" if "manual" in (keep["source"], other["source"]) else keep["source"], now_iso(), keep_id))
        conn.execute("UPDATE person_phones SET person_id = ? WHERE person_id = ?", (keep_id, other_id))
        activity.move_person(conn, other_id, keep_id)
        task_links.move_ref("person", other_id, keep_id, conn)
        conn.execute("DELETE FROM people WHERE id = ?", (other_id,))
        activity.recheck_unmatched(conn, user)
        conn.commit()
        merged = store.row(conn, user, keep_id)
        assert merged is not None
        return store.to_api(merged, conn)


def merge_candidates(user: User, limit: int = 20) -> List[Dict[str, Any]]:
    """Pairs of ``user``'s people that look like the same person (same or
    near-same name, or one's name inside the other's)."""
    with get_db_connection() as conn:
        visible = store.rows(conn, user, include_hidden=False)
    keys = [(r, store.names_of(r) + [norm_name(translit(n)) for n in store.names_of(r) if HEBREW.search(n)])
            for r in visible]
    out = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, ka = keys[i]
            b, kb = keys[j]
            score = 0.0
            for x in ka:
                for y in kb:
                    if not x or not y:
                        continue
                    if x == y:
                        score = 1.0
                    elif len(x) >= 3 and len(y) >= 3 and (f" {x} " in f" {y} " or f" {y} " in f" {x} "):
                        score = max(score, 0.9)
                    else:
                        score = max(score, difflib.SequenceMatcher(None, x, y).ratio())
            if score >= 0.88:
                out.append({"a": {"id": a["id"], "name": a["name"]}, "b": {"id": b["id"], "name": b["name"]},
                            "score": round(score, 2)})
    out.sort(key=lambda p: -p["score"])
    return out[:limit]
