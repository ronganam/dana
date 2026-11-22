"""About you: the learner. For each user who has ``about`` connected, it reads
their own conversations that went quiet and their own processed calls since
their bookmark and asks the background model (one call per pass) for durable
facts worth keeping about them (docs/about.md, "Learner").

Every pass runs as its user (usage attributed to them); bookmarks
(``about_state`` row ``learner``) and the failure back-off are per user, and
one user's failure never stops the others.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core import llm, user_profile, users
from core.db import get_db_connection
from core.users import User
from addons.about import facts
from addons.about.values import (FACT_MAX_CHARS, LEARNED_KINDS, MAX_CHARS, SECTION_KEYS, clean_text,
                                       env_int, looks_sensitive, normalize, now_iso)
from core.addons import store

logger = logging.getLogger(__name__)

LEARN_ENABLED = os.getenv("ABOUT_LEARN", "1").strip().lower() not in ("0", "false", "no", "off")
LEARN_INTERVAL_MINUTES = env_int("ABOUT_LEARN_INTERVAL_MINUTES", 30)
LEARN_QUIET_MINUTES = env_int("ABOUT_LEARN_QUIET_MINUTES", 20)
LEARN_LOOKBACK_DAYS = env_int("ABOUT_LEARN_LOOKBACK_DAYS", 14)
LEARN_MAX_ITEMS = env_int("ABOUT_LEARN_MAX_ITEMS", 8)
LEARN_MAX_INPUT_CHARS = 14_000            # all items in one model call
LEARN_ITEM_CHARS = 2_500                  # one conversation or call
LEARN_MAX_NEW = 5                         # facts added or sharpened per pass
LEARN_MAX_BACKOFF_MINUTES = 6 * 60
LOOP_TICK_SECONDS = 60.0                  # how often the loop looks for users whose pass is due

# Agent tools whose use, alone, makes a conversation a home command or a quick
# lookup with nothing to learn.
TRIVIAL_TOOLS = {
    "call_ha_service", "get_ha_exposed_entities", "get_ha_zones", "control_android_tv",
    "get_current_time", "get_shopping_list", "add_shopping_items", "update_shopping_items",
    "edit_shopping_item",
}


def _sql_ts(dt: datetime.datetime) -> str:
    """UTC timestamp in SQLite's CURRENT_TIMESTAMP format (what sessions and
    phone_calls store), for string comparison."""
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# --- State (per user) ---------------------------------------------------------------

def _get_state(conn, user_id: str, key: str, default: Any) -> Any:
    row = conn.execute("SELECT value FROM about_state WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def _set_state(conn, user_id: str, key: str, value: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO about_state (user_id, key, value) VALUES (?, ?, ?)",
                 (user_id, key, json.dumps(value, ensure_ascii=False)))


# --- What a pass reads ----------------------------------------------------------------

def _learn_job() -> str:
    return "about" if "about" in getattr(llm, "_CHAINS", {}) else "briefs"


def _msg_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _msg_hash(msg: Dict[str, Any]) -> str:
    raw = json.dumps({"r": msg.get("role"), "c": msg.get("content"), "t": msg.get("tool_call_id")},
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _new_messages(history: List[Dict[str, Any]], tail: Optional[str]) -> List[Dict[str, Any]]:
    """Messages after the one whose hash is ``tail`` (all of them when the tail
    is unknown or was pruned away)."""
    if tail:
        for i in range(len(history) - 1, -1, -1):
            if _msg_hash(history[i]) == tail:
                return history[i + 1:]
    return history


def _conversation_item(msgs: List[Dict[str, Any]]) -> Tuple[Optional[str], bool]:
    """``(text, trivial)`` for the new part of a conversation."""
    user_texts: List[str] = []
    tools: set = set()
    lines: List[str] = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            t = clean_text(_msg_text(m.get("content")))
            if t:
                user_texts.append(t)
                lines.append(f"Owner: {t[:500]}")
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                if fn.get("name"):
                    tools.add(fn["name"])
            t = clean_text(_msg_text(m.get("content")))
            if t:
                lines.append(f"Dana: {t[:400]}")
    user_chars = sum(len(t) for t in user_texts)
    trivial = (
        not user_texts
        or user_chars < 12
        or (len(user_texts) == 1 and user_chars < 80 and bool(tools) and tools <= TRIVIAL_TOOLS)
    )
    if trivial:
        return None, True
    text = "\n".join(lines)
    if len(text) > LEARN_ITEM_CHARS:
        text = "…" + text[-LEARN_ITEM_CHARS:]
    return text, False


def _call_item(row: Dict[str, Any]) -> Optional[str]:
    summary = clean_text(row.get("summary"))
    if not summary or (row.get("importance") or "") == "low":
        return None
    who = row.get("caller") if (row.get("caller") or "Me") != "Me" else row.get("receiver")
    when = str(row.get("call_timestamp") or "")[:10]
    text = f"Phone call with {who or 'unknown'} ({row.get('direction') or 'call'}, {when}). " \
           f"Title: {clean_text(row.get('title'))}. Summary: {summary}"
    return text[:LEARN_ITEM_CHARS]


def _calls_of(conn, user: User, after: str) -> List[Dict[str, Any]]:
    """``user``'s own calls created after ``after`` that are ready, processing
    or failed (the one place the learner reads calls)."""
    return [dict(r) for r in conn.execute(
        "SELECT id, title, caller, receiver, direction, summary, importance, call_timestamp, created_at, status "
        "FROM phone_calls WHERE user_id = ? AND created_at > ? AND status IN ('ready', 'processing', 'failed') "
        "ORDER BY created_at ASC", (user.id, after)).fetchall()]


def collect_items(user: User, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """What ``user``'s next pass would read (their chats and their calls), and
    the bookmark it would save.

    Returns ``{"items": [{ref, kind, source_ref, label, text}], "state": new_state}``.
    Conversations count once they have been quiet for ``ABOUT_LEARN_QUIET_MINUTES``;
    calls once they are ``ready``. Trivial ones advance the bookmark without
    being sent. At most ``ABOUT_LEARN_MAX_ITEMS`` items / ``LEARN_MAX_INPUT_CHARS``
    per pass, oldest first; the rest waits for the next pass."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    floor = _sql_ts(now - datetime.timedelta(days=LEARN_LOOKBACK_DAYS))
    quiet = _sql_ts(now - datetime.timedelta(minutes=LEARN_QUIET_MINUTES))
    with get_db_connection() as conn:
        state = _get_state(conn, user.id, "learner", {}) or {}
        sessions_bm: Dict[str, Dict[str, str]] = dict(state.get("sessions") or {})
        calls_after = state.get("calls_after") or floor
        calls_done: Dict[str, str] = dict(state.get("calls_done") or {})
        srows = [dict(r) for r in conn.execute(
            "SELECT session_id, history, status, updated_at FROM sessions "
            "WHERE user_id = ? AND updated_at > ? AND updated_at <= ? "
            "AND COALESCE(status, 'idle') NOT IN ('running', 'waiting_approval') "
            "ORDER BY updated_at ASC", (user.id, floor, quiet)).fetchall()]
        crows = _calls_of(conn, user, calls_after)

    candidates: List[Tuple[str, str, Dict[str, Any]]] = []  # (timestamp, kind, data)
    for s in srows:
        bm = sessions_bm.get(s["session_id"]) or {}
        if bm.get("at") == s["updated_at"]:
            continue
        try:
            history = json.loads(s["history"] or "[]")
        except Exception:
            history = []
        candidates.append((s["updated_at"], "chat", {"row": s, "history": history, "bm": bm}))
    for c in crows:
        if c["id"] in calls_done:
            continue
        candidates.append((c["created_at"], "call", {"row": c}))
    candidates.sort(key=lambda p: p[0])

    items: List[Dict[str, Any]] = []
    used = 0
    capped = False
    for ts, kind, data in candidates:
        if kind == "call":
            c = data["row"]
            if c["status"] == "processing":  # not done yet: the bookmark stays before it
                continue
            text = _call_item(c) if c["status"] == "ready" else None
            if not text:  # failed / no summary / low importance: handled, nothing to send
                calls_done[c["id"]] = c["created_at"]
                continue
            if capped or len(items) >= LEARN_MAX_ITEMS or used + len(text) > LEARN_MAX_INPUT_CHARS:
                capped = True  # wait for the next pass
                continue
            who = c["caller"] if (c["caller"] or "Me") != "Me" else c["receiver"]
            items.append({"ref": f"call-{len(items) + 1}", "kind": "call", "source_ref": c["id"],
                          "label": f"a call with {who}" if who else "a call", "text": text})
            used += len(text)
            calls_done[c["id"]] = c["created_at"]
        else:
            s, history, bm = data["row"], data["history"], data["bm"]
            new = _new_messages(history, bm.get("tail"))
            text = _conversation_item(new)[0] if new else None
            if text:
                if capped or len(items) >= LEARN_MAX_ITEMS or used + len(text) > LEARN_MAX_INPUT_CHARS:
                    capped = True
                    continue
                items.append({"ref": f"chat-{len(items) + 1}", "kind": "chat", "source_ref": s["session_id"],
                              "label": "a chat", "text": text})
                used += len(text)
            sessions_bm[s["session_id"]] = {"tail": _msg_hash(history[-1]) if history else "",
                                            "at": s["updated_at"]}

    # Calls bookmark: advance past every handled call, but never past one still
    # processing or one left for the next pass; ids handled beyond it are kept.
    pending = [c["row"]["created_at"] for _, k, c in candidates
               if k == "call" and c["row"]["id"] not in calls_done]
    handled = sorted(calls_done.values())
    if handled:
        limit = min(pending) if pending else None
        advanced = [t for t in handled if limit is None or t < limit]
        if advanced:
            calls_after = max(calls_after, advanced[-1])
    calls_done = {cid: ts for cid, ts in calls_done.items() if ts > calls_after}
    sessions_bm = {sid: bm for sid, bm in sessions_bm.items() if (bm.get("at") or "") > floor}
    new_state = dict(state)
    new_state.update({"sessions": sessions_bm, "calls_after": calls_after, "calls_done": calls_done})
    return {"items": items, "state": new_state}


# --- The model call -----------------------------------------------------------------

LEARN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "add": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "section": {"type": "string", "enum": SECTION_KEYS},
                    "from": {"type": "string"},
                },
                "required": ["text", "section", "from"],
            },
        },
        "update": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "from": {"type": "string"},
                },
                "required": ["id", "text", "from"],
            },
        },
    },
    "required": ["add", "update"],
}


def build_learn_prompt(user: User, items: List[Dict[str, Any]], known_facts: List[Dict[str, Any]],
                       forgotten: List[Dict[str, Any]]) -> str:
    """The learner's prompt for ``user`` (their name and pronoun, ``core/user_profile.py``)."""
    who = user_profile.for_user(user)
    owner = who.name
    used = sum(len(f["text"]) for f in known_facts)
    known = "\n".join(f"[{f['id']}] ({f['section']}, {f['source_kind']}) {f['text']}"
                      for f in known_facts) or "(none yet)"
    removed = "\n".join(f"- {f['text']}" for f in forgotten[-60:]) or "(none)"
    blocks = "\n\n".join(f"### {it['ref']} ({it['kind']})\n{it['text']}" for it in items)
    return (
        f"You keep a short list of durable facts about {owner}, the owner of a personal assistant called Dana. "
        "The list is put in front of Dana in every conversation so she can understand references without asking.\n\n"
        "Read the new conversations and call summaries below and decide whether any of them teaches a fact worth "
        "keeping. Most of them teach nothing: returning empty lists is the normal answer.\n\n"
        "Keep a fact ONLY if it will still be true and useful in a month, for example:\n"
        f"- who a person is to {owner} (\"Dan is {who.possessive} brother\", \"אמא calls most evenings\") — just enough to recognise them\n"
        "- what a nickname, place or device name refers to (\"the gate\" = the front gate)\n"
        "- standing preferences and routines (\"prefers reminders as 'when I get home'\", \"up around 07:30 on weekdays\")\n"
        "Never keep: one-time events, plans, tasks or appointments; anything already in the list (unless you can "
        "make it sharper); passwords, codes, account or card numbers; health, medical or financial details; "
        "anything about other people beyond what is needed to recognise them; guesses or things Dana said that "
        f"{owner} did not confirm; anything in the 'do not re-add' list, in any wording.\n\n"
        "Style: one short plain line per fact (under 120 characters), in the language the owner used for it, "
        "no bullet characters. Sections: people, home, habits, other.\n"
        "To sharpen or correct an existing fact of kind chat/call, return it under 'update' with its id and the "
        "new full text instead of adding a near-duplicate. Do not update facts of other kinds. "
        f"At most {LEARN_MAX_NEW} adds and updates together. 'from' is the ref of the item (e.g. chat-1, call-2) "
        "the fact comes from.\n"
        f"Space used: {used} of {MAX_CHARS} characters; when it is nearly full, prefer sharpening to adding.\n\n"
        f"## Current facts\n{known}\n\n"
        f"## Do not re-add (the owner removed these)\n{removed}\n\n"
        f"## New conversations and calls\n{blocks}\n"
    )


def apply_learned(user: User, result: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Store what the model proposed for ``user``, after the same checks the tools use."""
    by_ref = {it["ref"]: it for it in items}
    added: List[str] = []
    updated: List[str] = []
    skipped: List[str] = []
    budget = LEARN_MAX_NEW
    for u in (result.get("update") or [])[:LEARN_MAX_NEW]:
        if budget <= 0:
            break
        text = clean_text(u.get("text"))
        f = facts.get_fact(user, str(u.get("id") or ""))
        src = by_ref.get(str(u.get("from") or ""))
        if (not f or f["status"] != "active" or f["source_kind"] not in LEARNED_KINDS or not text
                or len(text) > FACT_MAX_CHARS or looks_sensitive(text) or facts.is_forgotten(user, text)
                or normalize(text) == normalize(f["text"])):
            skipped.append(text or str(u.get("id")))
            continue
        try:
            facts.update_fact(user, f["id"], text=text, source_kind=src["kind"] if src else f["source_kind"],
                              source_ref=src["source_ref"] if src else None,
                              source_label=src["label"] if src else None)
            updated.append(f["id"])
            budget -= 1
        except (LookupError, ValueError):
            skipped.append(text)
    for a in result.get("add") or []:
        if budget <= 0:
            break
        text = clean_text(a.get("text"))
        src = by_ref.get(str(a.get("from") or "")) or (items[0] if len(items) == 1 else None)
        if not text or len(text) > FACT_MAX_CHARS or looks_sensitive(text) or facts.is_forgotten(user, text):
            skipped.append(text)
            continue
        kind = src["kind"] if src else "chat"
        try:
            fact, created = facts.add_fact(user, text, a.get("section"), source_kind=kind,
                                           source_ref=src["source_ref"] if src else None,
                                           source_label=src["label"] if src else None)
        except ValueError:
            skipped.append(text)
            continue
        if created:
            added.append(fact["id"])
            budget -= 1
        else:
            skipped.append(text)
    return {"added": added, "updated": updated, "skipped": skipped}


async def learn_once(user: User, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """One learner pass over ``user``'s chats and calls. Saves their bookmark
    only when the pass succeeded (or had nothing to send). Raises when the
    model call failed."""
    collected = collect_items(user, now)
    items = collected["items"]
    outcome: Dict[str, Any] = {"items": len(items), "added": [], "updated": [], "skipped": []}
    if items:
        prompt_text = build_learn_prompt(user, items, facts.list_facts(user), facts.list_facts(user, "forgotten"))
        result = await llm.complete_json(prompt_text, LEARN_SCHEMA, job=_learn_job(), temperature=0.1, timeout=90.0,
                                         purpose="about_learner")
        outcome.update(apply_learned(user, result, items))
    state = collected["state"]
    state["last_run"] = now_iso()
    with get_db_connection() as conn:
        _set_state(conn, user.id, "learner", state)
        conn.commit()
    if items:
        logger.info(f"About learner (user {user.id}): read {len(items)} item(s), added {len(outcome['added'])}, "
                    f"updated {len(outcome['updated'])}, skipped {len(outcome['skipped'])}")
    return outcome


# --- Scheduling (per user) ------------------------------------------------------------

# user id → consecutive failed passes / monotonic time their next pass is due.
_failures: Dict[str, int] = {}
_due: Dict[str, float] = {}
_warned_no_key = False


def failures(user: User) -> int:
    return _failures.get(user.id, 0)


def next_delay_seconds(user: User) -> float:
    """Seconds until ``user``'s next pass: the interval, doubled per
    consecutive failure of theirs up to ``LEARN_MAX_BACKOFF_MINUTES``."""
    minutes = LEARN_INTERVAL_MINUTES * (2 ** min(failures(user), 8))
    return 60.0 * min(minutes, max(LEARN_MAX_BACKOFF_MINUTES, LEARN_INTERVAL_MINUTES))


async def run_pass(user: User) -> Optional[Dict[str, Any]]:
    """A pass as ``user``, with the loop's bookkeeping (key check, their
    failure count). Never raises."""
    with users.acting_as(user):
        return await _run_pass(user)


async def _run_pass(user: User) -> Optional[Dict[str, Any]]:
    global _warned_no_key
    try:
        if not llm.configured(_learn_job()):
            if not _warned_no_key:
                logger.warning(f"About learner: no API key for {llm.model(_learn_job())}; not learning")
                _warned_no_key = True
            return None
        outcome = await learn_once(user)
        _failures.pop(user.id, None)
        return outcome
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _failures[user.id] = failures(user) + 1
        logger.warning(f"About learner pass failed for user {user.id} ({_failures[user.id]} in a row), "
                       f"backing off: {e}")
        return None


def learners() -> List[User]:
    """The users the learner runs for: everyone with ``about`` connected."""
    return [u for u in (users.get(uid) for uid in store.users_with(facts.ADDON)) if u is not None]


async def run_if_due(user: User, now: Optional[float] = None) -> bool:
    """Run ``user``'s pass when it is due (the background job, every
    :data:`LOOP_TICK_SECONDS`); their next pass is then due after
    :func:`next_delay_seconds`. True when it ran."""
    moment = time.monotonic() if now is None else now
    if not LEARN_ENABLED or moment < _due.get(user.id, 0.0):
        return False
    try:
        await run_pass(user)
    finally:
        _due[user.id] = moment + next_delay_seconds(user)
    return True


async def run_due(now: Optional[float] = None) -> List[str]:
    """Run the pass of every learner whose pass is due (each as that user, one
    after another); a user's next pass is then due after
    :func:`next_delay_seconds`. Returns the ids that ran."""
    ran: List[str] = []
    for user in learners():
        moment = time.monotonic() if now is None else now
        if moment < _due.get(user.id, 0.0):
            continue
        try:
            await run_pass(user)
        except asyncio.CancelledError:
            raise
        except Exception:  # run_pass never raises; a broken user must not stop the rest anyway
            logger.exception(f"About learner: pass for user {user.id} broke")
        _due[user.id] = moment + next_delay_seconds(user)
        ran.append(user.id)
    return ran
