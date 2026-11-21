"""Learned phrases: short home commands Dana has seen work run without the model.

**Learn** (after every finished model turn, :func:`learn`): a short text or voice
message (no images, no on-screen item, no shared phone screen, at most ``MAX_WORDS`` words) that led to
exactly one successful call of an allow-listed tool (``call_ha_service``,
``control_android_tv``), optionally preceded by ``get_ha_exposed_entities``,
and a short final reply, counts one outcome for the pair
*normalised phrase → tool + exact arguments*. After ``LEARN_AFTER`` (3)
identical outcomes in a row the phrase is **active**. A different outcome for
the same phrase resets it (the new outcome starts again at 1).

**Use** (:func:`match`, from ``core.session_runner.start_agent_task``): a new
text message whose normalised phrase is active, in a session with no pending
approval/selection, runs the tool directly, replies with the stored
confirmation and is stored as a normal tool call. If the tool fails, the
phrase is deactivated and the model answers instead.

Normalisation is conservative: Unicode NFKC, case-folded, Hebrew points
removed, surrounding punctuation and extra whitespace dropped. Nothing else —
"open the gate" and "open the gate please" are different phrases.

Learned phrases belong to a user: learned from, matched, listed and deleted
per user (table ``shortcuts``, phrase unique per user). A phrase is learned
for, and runs, only a tool the user has now (docs/access.md, "Enforcement"):
when its addon is gone the message goes to the model as if no phrase
matched (and the model's answer then counts as a different outcome).

``SHORTCUTS=0`` turns learning and use off. Routes: core/actions_api.py.
"""
import json
import logging
import os
import re
import time
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Sequence

from core import users
from core.db import get_db_connection
from core.users import User
from core.addons import registry
from addons.homeassistant import home_map

logger = logging.getLogger(__name__)

ALLOWED_TOOLS = ("call_ha_service", "control_android_tv")
PREP_TOOLS = ("get_ha_exposed_entities",)
LEARN_AFTER = 3
MAX_WORDS = 8
MAX_REPLY_CHARS = 120
# control_android_tv actions that are not "the same command every time".
_UNLEARNABLE_TV_ACTIONS = ("run_command",)
_LLM_ERROR_PREFIX = "Sorry, I encountered an error communicating with the AI model"

def enabled() -> bool:
    return os.getenv("SHORTCUTS", "1").strip().lower() not in ("0", "false", "off", "no")


# --- Normalisation --------------------------------------------------------------

_HEBREW_POINTS = re.compile(r"[֑-ׇ]")


def _is_edge_char(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", str(text or "")).casefold()
    t = _HEBREW_POINTS.sub("", t)
    t = " ".join(t.split())
    start, end = 0, len(t)
    while start < end and _is_edge_char(t[start]):
        start += 1
    while end > start and _is_edge_char(t[end - 1]):
        end -= 1
    return t[start:end]


def is_short(text: str) -> bool:
    n = normalize(text)
    return bool(n) and len(n.split()) <= MAX_WORDS


def looks_failed(result: str) -> bool:
    r = str(result or "").lstrip()
    return r.lower().startswith(("error", "failed", "sorry"))


# --- Learning ------------------------------------------------------------------

def _tool_calls(entries: Sequence[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """[{name, args, result}] in order, or None if something can't be parsed."""
    results = {e.get("tool_call_id"): str(e.get("content") or "") for e in entries
               if isinstance(e, dict) and e.get("role") == "tool"}
    calls = []
    for e in entries:
        if not isinstance(e, dict) or e.get("role") != "assistant":
            continue
        for tc in e.get("tool_calls") or []:
            if not isinstance(tc, dict):
                return None
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (TypeError, ValueError):
                return None
            calls.append({"name": fn.get("name") or "", "args": args, "result": results.get(tc.get("id"))})
    return calls


def outcome_of(entries: Sequence[Dict[str, Any]], final_text: str) -> Optional[Dict[str, Any]]:
    """The learnable outcome of a finished turn ({tool, args, reply}), or None."""
    calls = _tool_calls(entries or [])
    if not calls:
        return None
    *prep, main = calls
    if any(c["name"] not in PREP_TOOLS for c in prep):
        return None
    if main["name"] not in ALLOWED_TOOLS or main["result"] is None or looks_failed(main["result"]):
        return None
    if main["name"] == "control_android_tv" and str(main["args"].get("action", "")).lower() in _UNLEARNABLE_TV_ACTIONS:
        return None
    reply = (final_text or "").strip()
    if not reply or len(reply) > MAX_REPLY_CHARS or reply.startswith(_LLM_ERROR_PREFIX):
        return None
    return {"tool": main["name"], "args": main["args"], "reply": reply}


def _args_key(args: Dict[str, Any]) -> str:
    return json.dumps(args or {}, sort_keys=True, ensure_ascii=False)


def learn(user: User, user_text: str, entries: Sequence[Dict[str, Any]], final_text: str) -> Optional[str]:
    """Count one finished turn of ``user``. Returns what happened (for logs/tests):
    ``counted`` / ``activated`` / ``reset`` / ``cleared`` / None (ignored).
    An outcome whose tool the user does not have now counts as no outcome."""
    if not enabled() or not is_short(user_text):
        return None
    if (final_text or "").startswith(_LLM_ERROR_PREFIX):
        return None
    user_id = user.id
    norm = normalize(user_text)
    outcome = outcome_of(entries, final_text)
    if outcome is not None and not registry.has_tool(user, outcome["tool"]):
        outcome = None
    now = time.time()
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM shortcuts WHERE user_id = ? AND phrase_norm = ?", (user_id, norm)).fetchone()
        if outcome is None:
            if row is None or (row["streak"] == 0 and not row["active"]):
                return None
            conn.execute("UPDATE shortcuts SET streak = 0, active = 0, updated_at = ? WHERE id = ?", (now, row["id"]))
            conn.commit()
            logger.info(f"Shortcut '{row['phrase']}' cleared: the phrase did something else this time")
            return "cleared"
        args = _args_key(outcome["args"])
        if row is None:
            conn.execute(
                "INSERT INTO shortcuts (id, user_id, phrase_norm, phrase, tool, args, reply, streak, active, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
                (uuid.uuid4().hex[:12], user_id, norm, user_text.strip(), outcome["tool"], args, outcome["reply"],
                 now, now))
            conn.commit()
            return "counted"
        if row["tool"] == outcome["tool"] and row["args"] == args:
            streak = row["streak"] + 1
            activate = streak >= LEARN_AFTER and not row["active"]
            conn.execute(
                "UPDATE shortcuts SET streak = ?, active = ?, reply = ?, phrase = ?, updated_at = ?, "
                "learned_at = CASE WHEN ? THEN ? ELSE learned_at END WHERE id = ?",
                (streak, 1 if streak >= LEARN_AFTER else 0, outcome["reply"], user_text.strip(), now,
                 1 if activate else 0, now, row["id"]))
            conn.commit()
            if activate:
                logger.info(f"Shortcut learned: '{user_text.strip()}' → {outcome['tool']} {args}")
            return "activated" if activate else "counted"
        conn.execute(
            "UPDATE shortcuts SET tool = ?, args = ?, reply = ?, streak = 1, active = 0, phrase = ?, updated_at = ? "
            "WHERE id = ?", (outcome["tool"], args, outcome["reply"], user_text.strip(), now, row["id"]))
        conn.commit()
        logger.info(f"Shortcut '{user_text.strip()}' reset: a different outcome this time")
        return "reset"


# --- Use -------------------------------------------------------------------------

def _row_to_shortcut(row) -> Dict[str, Any]:
    try:
        args = json.loads(row["args"] or "{}")
    except (TypeError, ValueError):
        args = {}
    return {
        "id": row["id"], "phrase": row["phrase"], "phrase_norm": row["phrase_norm"],
        "tool": row["tool"], "args": args, "reply": row["reply"], "streak": row["streak"],
        "active": bool(row["active"]), "uses": row["uses"], "last_used": row["last_used"],
        "learned_at": row["learned_at"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def match(user: User, user_text: str) -> Optional[Dict[str, Any]]:
    """The user's active shortcut for this message, if any (none when its tool
    is not one the user has now: the message goes to the model)."""
    if not enabled() or not is_short(user_text):
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM shortcuts WHERE user_id = ? AND phrase_norm = ? AND active = 1",
                           (user.id, normalize(user_text))).fetchone()
    if row is None:
        return None
    sc = _row_to_shortcut(row)
    if sc["tool"] not in ALLOWED_TOOLS or not sc["reply"] or not registry.has_tool(user, sc["tool"]):
        return None
    return sc


def mark_used(shortcut_id: str) -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE shortcuts SET uses = uses + 1, last_used = ? WHERE id = ?", (time.time(), shortcut_id))
        conn.commit()


def deactivate(shortcut_id: str, reason: str = "") -> None:
    with get_db_connection() as conn:
        conn.execute("UPDATE shortcuts SET active = 0, streak = 0, updated_at = ? WHERE id = ?",
                     (time.time(), shortcut_id))
        conn.commit()
    logger.info(f"Shortcut {shortcut_id} deactivated: {reason}")


# --- Listing (API) ------------------------------------------------------------------

def _entity_name(user: Optional[User], entity_id: str) -> str:
    """Friendly name from ``user``'s cached HA home map, else the entity id."""
    for line in home_map.cached_text(user).splitlines():
        eid, _, rest = line.partition("|")
        if eid.strip() == entity_id and rest.strip():
            return rest.strip().split("  ")[0].strip()
    return entity_id


def describe(tool: str, args: Dict[str, Any], user: Optional[User]) -> str:
    """What the shortcut does, in words (entity names from ``user``'s home map)."""
    if tool == "call_ha_service":
        service = str(args.get("service") or "").replace("_", " ")
        what = _entity_name(user, str(args.get("entity_id") or ""))
        extra = args.get("service_data")
        text = f"{service.capitalize()} {what}".strip()
        if extra:
            text += " (" + ", ".join(f"{k} {v}" for k, v in dict(extra).items()) + ")"
        return text
    if tool == "control_android_tv":
        action = str(args.get("action") or "").replace("_", " ")
        detail = next((str(args[k]) for k in ("app_id", "channel", "query", "video_id", "title_id", "command")
                       if args.get(k)), "")
        return f"TV: {action} {detail}".strip()
    return f"{tool} {_args_key(args)}"


def list_all(user_id: str) -> List[Dict[str, Any]]:
    """The user's learned phrases (active first)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM shortcuts WHERE user_id = ? ORDER BY active DESC, uses DESC, updated_at DESC",
                            (user_id,)).fetchall()
    out = []
    user = users.get(user_id)
    for r in rows:
        sc = _row_to_shortcut(r)
        out.append({
            "id": sc["id"], "phrase": sc["phrase"], "does": describe(sc["tool"], sc["args"], user),
            "tool": sc["tool"], "args": sc["args"], "reply": sc["reply"],
            "active": sc["active"], "times_used": sc["uses"], "last_used": sc["last_used"],
            "seen": sc["streak"], "learn_after": LEARN_AFTER, "learned_at": sc["learned_at"],
        })
    return out


def delete(user_id: str, shortcut_id: str) -> bool:
    """Forget one of the user's phrases (False when they have no such phrase)."""
    with get_db_connection() as conn:
        n = conn.execute("DELETE FROM shortcuts WHERE id = ? AND user_id = ?", (shortcut_id, user_id)).rowcount
        conn.commit()
    return n > 0
