"""About you: the old ``memories`` table and the old ``/api/memories`` shape.

``memories`` is not per user, and other code still keeps its own rows there
(``system`` trigger / digest / wake state, the calendar picker's
``selected_calendars``, contact lookups). Its real memories belong to one
user: the one marked by the ``about_state`` row ``legacy_memories``, which
migration ``m0009_about_per_user`` gives to the first admin of a database
that existed before it (a database created later has no such user and never
had real memories). For that user only:

- real memories (not ``system`` rows, not app state, not credential-looking)
  are copied into their facts once per key (:func:`migrate_legacy`); the old
  rows stay where they are;
- the rest stays answerable by ``recall_info`` as "older notes"
  (:func:`unmigrated_rows`), and the Telegram ``/memory`` picker,
  ``forget_info`` and ``DELETE /api/memories/{key}`` can delete an old key.

Everyone else gets nothing from ``memories``. ``GET`` / ``POST
/api/memories`` serve the caller's own facts in the old shape.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from core import users
from core.db import get_db_connection
from core.users import User
from addons.about import facts
from addons.about.values import (FACT_MAX_CHARS, SECTION_KEYS, clean_text, looks_sensitive, normalize,
                                       now_iso, section_for_category)

logger = logging.getLogger(__name__)

# The about_state key that marks the user who owns the old memories (written by
# migration m0009_about_per_user, which keeps its own copy of the name).
LEGACY_MEMORIES_KEY = "legacy_memories"

# Legacy memory rows that are app state, not memories.
_LEGACY_STATE_KEYS = {"selected_calendars"}
_LEGACY_SECRET = re.compile(r"(password|passwd|passcode|token|secret|api_?key|credential|\bpin\b|otp)", re.IGNORECASE)


def owner_id() -> Optional[str]:
    """Id of the user who owns the old memories (None: nobody does)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT user_id FROM about_state WHERE key = ? ORDER BY user_id LIMIT 1",
                           (LEGACY_MEMORIES_KEY,)).fetchone()
    return row["user_id"] if row else None


def owns_memories(user: Optional[User]) -> bool:
    return user is not None and owner_id() == user.id


def legacy_text(key: str, value: str) -> str:
    v = clean_text(value)
    k = key.replace("_", " ").strip()
    if not k or normalize(k) in normalize(v):
        text = v
    else:
        text = f"{k[:1].upper()}{k[1:]}: {v}"
    return text if len(text) <= FACT_MAX_CHARS else text[: FACT_MAX_CHARS - 1].rstrip() + "…"


def migrate_legacy() -> int:
    """Copy real memories (category not ``system``) into the owner's facts,
    once per key.

    Skipped (they stay only in ``memories``): ``system`` rows (trigger state),
    app state (``selected_calendars``), and credential-looking rows (password,
    token, …), which stay reachable through ``recall_info``. Idempotent: a key
    already copied — even if the owner since removed the fact — is not copied
    again. Returns how many facts were created (0 when nobody owns the old
    memories)."""
    uid = owner_id()
    user = users.get(uid) if uid else None
    if user is None:
        return 0
    created = 0
    with get_db_connection() as conn:
        rows = conn.execute(
            # scoped: memories has no user_id: the old single-owner table, copied to its owner (owner_id)
            "SELECT key, value, category, updated_at FROM memories "
            "WHERE COALESCE(category, '') != 'system' AND key NOT IN "
            "(SELECT legacy_key FROM about_facts WHERE legacy_key IS NOT NULL)").fetchall()
    for r in rows:
        key, value, cat = r["key"], str(r["value"] or ""), r["category"] or ""
        if key in _LEGACY_STATE_KEYS or not value.strip():
            continue
        if _LEGACY_SECRET.search(key) or _LEGACY_SECRET.search(cat) or looks_sensitive(value):
            continue
        text = legacy_text(key, value)
        now = now_iso()
        with get_db_connection() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO about_facts (id, user_id, section, text, status, source_kind, source_ref, "
                "legacy_key, created_at, updated_at, confirmed_at) "
                "VALUES (?, ?, ?, ?, 'active', 'imported', ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:10], user.id, section_for_category(cat, text), text, key, key, now, now, now))
            conn.commit()
            created += cur.rowcount
    if created:
        logger.info(f"About: imported {created} fact(s) from the old memories table")
        facts.enforce_cap(user)
        facts.refresh_prompt(user)
    return created


def unmigrated_rows(user: User, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Old memories that were not turned into facts (credentials), minus trigger
    state — still answerable by ``recall_info`` for their owner (empty for
    anyone else)."""
    if not owns_memories(user):
        return []
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            # scoped: owns_memories(user) checked above (memories has no user_id)
            "SELECT key, value, category, updated_at FROM memories "
            "WHERE COALESCE(category, '') != 'system' AND key NOT IN "
            "(SELECT legacy_key FROM about_facts WHERE legacy_key IS NOT NULL)").fetchall()]
    rows = [r for r in rows if r["key"] not in _LEGACY_STATE_KEYS]
    if query:
        q = query.strip().lower()
        qn = q.replace(" ", "_")
        rows = [r for r in rows if q in str(r["value"]).lower() or qn in r["key"] or q in r["key"]]
    return rows


# --- Old keys (Telegram /memory picker, forget_info, DELETE /api/memories/{key}) ----

def memory_keys(user: User) -> List[str]:
    """Every old memory key, for their owner's Telegram "Forget" picker (empty for anyone else)."""
    if not owns_memories(user):
        return []
    with get_db_connection() as conn:
        return [r["key"] for r in conn.execute("SELECT key FROM memories ORDER BY key ASC").fetchall()]


def memory_categories(user: User) -> List[str]:
    """The old memory categories, for their owner's Telegram "List" picker (empty for anyone else)."""
    if not owns_memories(user):
        return []
    with get_db_connection() as conn:
        rows = conn.execute("SELECT DISTINCT category FROM memories WHERE category IS NOT NULL").fetchall()
    return [r["category"] for r in rows if r["category"]]


def _clean_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_")


def old_memory(user: User, key: str) -> Optional[Dict[str, Any]]:
    """The old memory row ``key`` when ``user`` owns the old memories."""
    if not owns_memories(user):
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT key, value, category, updated_at FROM memories WHERE key = ?",
                           (_clean_key(key),)).fetchone()
    return dict(row) if row else None


def delete_old_memory(user: User, key: str) -> bool:
    """Delete the old memory row ``key`` (only its owner can). Returns whether a row went."""
    if not owns_memories(user):
        return False
    with get_db_connection() as conn:
        cur = conn.execute("DELETE FROM memories WHERE key = ?", (_clean_key(key),))
        conn.commit()
        return cur.rowcount > 0


def restore_old_memory(row: Dict[str, Any]) -> None:
    """Put back an old memory row :func:`old_memory` returned (undo)."""
    with get_db_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO memories (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
                     (row["key"], row["value"], row["category"], row["updated_at"]))
        conn.commit()


# --- Old /api/memories shape -------------------------------------------------------

def legacy_list(user: User) -> List[Dict[str, Any]]:
    """``user``'s facts in the old ``GET /api/memories`` shape ({key, value, category, updated_at})."""
    out = []
    for f in facts.list_facts(user):
        out.append({"key": f"fact_{f['id']}", "value": f["text"], "category": f["section"],
                    "updated_at": f["updated_at"], "source": f["source"]})
    out.sort(key=lambda m: (SECTION_KEYS.index(m["category"]) if m["category"] in SECTION_KEYS else 99))
    return out


def legacy_upsert(user: User, key: str, value: str, category: Optional[str]) -> Dict[str, Any]:
    """Old ``POST /api/memories``: ``fact_<id>`` edits that fact of ``user``, any other key adds one."""
    k = (key or "").strip()
    fid = k[5:] if k.startswith("fact_") else None
    existing = facts.get_fact(user, fid) if fid else None
    if existing and existing["status"] == "active":
        sec = category.strip().lower() if category and category.strip().lower() in SECTION_KEYS else None
        return facts.update_fact(user, existing["id"], text=value, section=sec)
    text = legacy_text(k.lower().replace(" ", "_"), value) if not fid else clean_text(value)
    fact, _ = facts.add_fact(user, text, section_for_category(category, text), source_kind="owner")
    return fact
