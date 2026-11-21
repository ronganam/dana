"""About you: each user's facts (table ``about_facts``, owner ``user_id``).

One short plain-text line per fact, in a section (people / home / habits /
other), with where it came from. Removing a fact marks it ``forgotten`` (kept
so nothing re-adds it for that user); facts squeezed out by the user's size
cap are marked ``dropped``. Every function works on one user's facts: another
user's fact id is "not found", exactly like an id that does not exist. Every
change rebuilds that user's prompt block (:mod:`addons.about.prompt`).
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from core import users
from core.db import get_db_connection
from core.users import User
from addons.about import prompt
from addons.about.values import (FACT_MAX_CHARS, KEEP_INACTIVE, LEARNED_KINDS, MAX_CHARS, MAX_FACTS,
                                       SECTION_KEYS, SECTIONS, clean_text, fact_dict, guess_section,
                                       near_forgotten, normalize, now_iso, same_fact, similarity)
from core.addons import store

logger = logging.getLogger(__name__)

ADDON = "about"


# --- Prompt cache -----------------------------------------------------------------

def refresh_prompt(user: User) -> None:
    """Rebuild ``user``'s cached prompt block from the DB. Keeps the old text on error."""
    try:
        prompt.put(user.id, prompt.render(user.name, list_facts(user)))
    except Exception as e:
        logger.warning(f"About: prompt cache refresh failed for user {user.id}, keeping the previous one: {e}")


def load_prompts() -> None:
    """Build the prompt block of every user who has ``about`` connected
    (startup; a user who connects later gets theirs from ``on_connect``)."""
    for user_id in store.users_with(ADDON):
        user = users.get(user_id)
        if user is not None:
            refresh_prompt(user)


# --- Reads ------------------------------------------------------------------------

def list_facts(user: User, status: str = "active") -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM about_facts WHERE user_id = ? AND status = ? ORDER BY created_at ASC, id ASC",
            (user.id, status)).fetchall()
    return [fact_dict(r) for r in rows]


def get_fact(user: User, fact_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM about_facts WHERE id = ? AND user_id = ?", (fact_id, user.id)).fetchone()
    return fact_dict(row) if row else None


def snapshot(user: User, fact_id: str) -> Optional[Dict[str, Any]]:
    """The raw row of ``user``'s fact (for :func:`restore_snapshot`)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM about_facts WHERE id = ? AND user_id = ?", (fact_id, user.id)).fetchone()
    return dict(row) if row else None


def grouped(user: User) -> Dict[str, Any]:
    """``GET /api/about`` body."""
    facts = list_facts(user)
    sections = []
    for key, label in SECTIONS:
        sections.append({
            "key": key,
            "label": label,
            "facts": [{k: f[k] for k in ("id", "text", "source", "source_kind", "source_ref",
                                         "learned_at", "updated_at")}
                      for f in facts if f["section"] == key],
        })
    return {"sections": sections, "used_chars": sum(len(f["text"]) for f in facts),
            "max_chars": MAX_CHARS}


def find_similar(user: User, text: str) -> Optional[Dict[str, Any]]:
    """An active fact of ``user`` that says the same thing (``same_fact``), closest first."""
    matches = [f for f in list_facts(user) if same_fact(text, f["text"], user.name)]
    return max(matches, key=lambda f: similarity(text, f["text"])) if matches else None


def is_forgotten(user: User, text: str) -> bool:
    """Close to a fact ``user`` removed (or undid)."""
    return any(near_forgotten(text, f["text"], user.name) for f in list_facts(user, "forgotten"))


def resolve(user: User, ref: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Find an active fact of ``user`` by id, legacy key (``fact_<id>`` or an
    old memory key) or text. Returns ``(match, candidates)``: a single match,
    or the closest few when the text is ambiguous."""
    r = (ref or "").replace("\\", "").strip().strip("[]").strip()
    r = re.sub(r"^id[:\s]+", "", r, flags=re.IGNORECASE) if re.fullmatch(r"id[:\s]+[0-9a-f]{10}", r, re.I) else r
    if not r:
        return None, []
    fid = r[5:] if r.startswith("fact_") else r
    f = get_fact(user, fid)
    if f and f["status"] == "active":
        return f, []
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM about_facts WHERE user_id = ? AND legacy_key = ? AND status = 'active'",
                           (user.id, r.lower().replace(" ", "_"))).fetchone()
    if row:
        return fact_dict(row), []
    facts = list_facts(user)
    nr = normalize(r)
    exact = [x for x in facts if normalize(x["text"]) == nr]
    if len(exact) == 1:
        return exact[0], []
    contains = [x for x in facts if nr and nr in normalize(x["text"])]
    if len(contains) == 1:
        return contains[0], []
    scored = sorted(((similarity(r, x["text"]), x) for x in facts), key=lambda p: -p[0])
    if scored and scored[0][0] >= 0.75 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return scored[0][1], []
    cands = contains[:5] if contains else [x for s, x in scored[:5] if s >= 0.35]
    return None, cands


def search(user: User, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """``user``'s active facts matching the query words (any word, case-insensitive)."""
    words = [w for w in normalize(query).split() if len(w) > 1] or [normalize(query)]
    out = []
    for f in list_facts(user):
        hay = normalize(f["text"] + " " + f["section"])
        score = sum(1 for w in words if w and w in hay)
        if score:
            out.append((score, f))
    out.sort(key=lambda p: -p[0])
    return [f for _, f in out[:limit]]


# --- Changes ----------------------------------------------------------------------

def _validate_text(text: str) -> str:
    t = clean_text(text)
    if not t:
        raise ValueError("The fact is empty.")
    if len(t) > FACT_MAX_CHARS:
        raise ValueError(f"Too long ({len(t)} characters): keep a fact to one short line "
                         f"(under {FACT_MAX_CHARS}).")
    return t


def add_fact(user: User, text: str, section: Optional[str] = None, *, source_kind: str = "owner",
             source_ref: Optional[str] = None, source_label: Optional[str] = None,
             legacy_key: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Store a fact for ``user``. Returns ``(fact, created)``; ``created`` is
    False when one of their active facts already says the same (that one is
    returned, re-confirmed).

    Raises:
        ValueError: empty or too long.
    """
    t = _validate_text(text)
    sec = section if section in SECTION_KEYS else guess_section(t)
    now = now_iso()
    dup = find_similar(user, t)
    if dup:
        with get_db_connection() as conn:
            conn.execute("UPDATE about_facts SET confirmed_at = ? WHERE id = ? AND user_id = ?",
                         (now, dup["id"], user.id))
            conn.commit()
        return dup, False
    fid = uuid.uuid4().hex[:10]
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO about_facts (id, user_id, section, text, status, source_kind, source_ref, source_label, "
            "legacy_key, created_at, updated_at, confirmed_at) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)",
            (fid, user.id, sec, t, source_kind, source_ref, source_label, legacy_key, now, now, now))
        conn.commit()
    enforce_cap(user, protect=fid)
    refresh_prompt(user)
    return get_fact(user, fid) or {"id": fid, "text": t, "section": sec}, True


def update_fact(user: User, fact_id: str, text: Optional[str] = None, section: Optional[str] = None, *,
                source_kind: str = "edited", source_ref: Optional[str] = None,
                source_label: Optional[str] = None) -> Dict[str, Any]:
    """Change an active fact's text and/or section.

    Raises:
        LookupError: ``user`` has no active fact with that id.
        ValueError: bad text or section.
    """
    old = get_fact(user, fact_id)
    if not old or old["status"] != "active":
        raise LookupError(fact_id)
    new_text = _validate_text(text) if text is not None else old["text"]
    if section is not None and section not in SECTION_KEYS:
        raise ValueError(f"Unknown section '{section}'. Use one of: {', '.join(SECTION_KEYS)}.")
    new_section = section or old["section"]
    now = now_iso()
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE about_facts SET text = ?, section = ?, source_kind = ?, source_ref = COALESCE(?, source_ref), "
            "source_label = ?, updated_at = ?, confirmed_at = ? WHERE id = ? AND user_id = ?",
            (new_text, new_section, source_kind, source_ref, source_label, now, now, fact_id, user.id))
        conn.commit()
    enforce_cap(user, protect=fact_id)
    refresh_prompt(user)
    return get_fact(user, fact_id)  # type: ignore[return-value]


def restore_snapshot(user: User, snap: Dict[str, Any]) -> None:
    """Put ``user``'s fact back exactly as ``snap`` (a raw row from :func:`snapshot`) had it."""
    with get_db_connection() as conn:
        conn.execute(
            "UPDATE about_facts SET text = ?, section = ?, status = ?, source_kind = ?, source_ref = ?, "
            "source_label = ?, updated_at = ?, inactive_at = ? WHERE id = ? AND user_id = ?",
            (snap["text"], snap["section"], snap["status"], snap["source_kind"],
             snap["source_ref"], snap["source_label"], snap["updated_at"],
             snap["inactive_at"], snap["id"], user.id))
        conn.commit()
    if snap["status"] == "active":
        enforce_cap(user, protect=snap["id"])
    refresh_prompt(user)


def forget_fact(user: User, fact_id: str) -> bool:
    """Mark ``user``'s fact forgotten (hidden, and never re-added for them by the learner or tools)."""
    now = now_iso()
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE about_facts SET status = 'forgotten', inactive_at = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status = 'active'", (now, now, fact_id, user.id))
        conn.commit()
    if cur.rowcount:
        _purge_inactive(user)
        refresh_prompt(user)
    return cur.rowcount > 0


def restore_fact(user: User, fact_id: str) -> bool:
    now = now_iso()
    with get_db_connection() as conn:
        cur = conn.execute(
            "UPDATE about_facts SET status = 'active', inactive_at = NULL, updated_at = ? "
            "WHERE id = ? AND user_id = ? AND status != 'active'", (now, fact_id, user.id))
        conn.commit()
    if cur.rowcount:
        enforce_cap(user, protect=fact_id)
        refresh_prompt(user)
    return cur.rowcount > 0


def enforce_cap(user: User, protect: Optional[str] = None) -> List[str]:
    """Keep ``user``'s active facts under ``ABOUT_MAX_CHARS`` / ``ABOUT_MAX_FACTS``.

    Over the cap, facts are marked ``dropped`` in this order: learned from
    chats/calls (least recently confirmed first), then imported from the old
    Memory list, then the user's own (oldest first). ``protect`` (the fact just
    added or edited) is never dropped. Returns the dropped ids."""
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, text, source_kind, COALESCE(confirmed_at, updated_at) AS seen FROM about_facts "
            "WHERE user_id = ? AND status = 'active'", (user.id,)).fetchall()]
        total = sum(len(r["text"]) for r in rows)
        count = len(rows)
        if total <= MAX_CHARS and count <= MAX_FACTS:
            return []

        def rank(r: Dict[str, Any]) -> Tuple[int, str]:
            tier = 0 if r["source_kind"] in LEARNED_KINDS else 1 if r["source_kind"] == "imported" else 2
            return tier, r["seen"] or ""

        dropped: List[str] = []
        now = now_iso()
        for r in sorted((r for r in rows if r["id"] != protect), key=rank):
            if total <= MAX_CHARS and count <= MAX_FACTS:
                break
            conn.execute("UPDATE about_facts SET status = 'dropped', inactive_at = ? WHERE id = ? AND user_id = ?",
                         (now, r["id"], user.id))
            total -= len(r["text"])
            count -= 1
            dropped.append(r["id"])
        conn.commit()
    if dropped:
        logger.info(f"About: user {user.id} over the cap, dropped {len(dropped)} fact(s): {dropped}")
        _purge_inactive(user)
    return dropped


def _purge_inactive(user: User) -> None:
    """Keep only ``user``'s newest ``KEEP_INACTIVE`` forgotten/dropped rows."""
    with get_db_connection() as conn:
        conn.execute(
            "DELETE FROM about_facts WHERE user_id = ? AND status != 'active' AND id NOT IN ("
            "SELECT id FROM about_facts WHERE user_id = ? AND status != 'active' ORDER BY inactive_at DESC LIMIT ?)",
            (user.id, user.id, KEEP_INACTIVE))
        conn.commit()
