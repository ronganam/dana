"""Briefs: a few points + open loops per person, written by the model and
cached with a signature of the activity they were written from.

Per user: a brief is written from one user's activity about one of their
people, as that user (model usage is theirs), for them (``user_profile``).
The loop rewrites up to ``PEOPLE_BRIEF_BATCH`` stale briefs of every user who
has ``people`` connected, each as that user, every
``PEOPLE_BRIEF_INTERVAL_SECONDS``; one user's failure does not stop the
others. The incoming-call lookup never waits for a model.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
import sqlite3
from typing import Dict, List, Optional

from core import budget, clock, llm, user_locale, user_profile, users
from core.db import get_db_connection
from core.text import untrusted
from core.users import User
from core.addons import store as connections
from addons.people import activity, linking, person_tasks, store
from addons.people.values import BRIEF_BATCH, BRIEF_INTERVAL, BRIEF_STYLE, ADDON, loads, now_iso

logger = logging.getLogger(__name__)

_brief_tasks: Dict[str, asyncio.Task] = {}
_brief_locks: Dict[str, asyncio.Lock] = {}

_BRIEF_SCHEMA = {"type": "object", "properties": {
    "points": {"type": "array", "items": {"type": "string"}},
    "open": {"type": "array", "items": {"type": "string"}}}, "required": ["points", "open"]}


def signature(conn: sqlite3.Connection, user: User, r: sqlite3.Row) -> Optional[str]:
    """What the brief of ``user``'s person ``r`` depends on (None: not enough to say anything)."""
    pid = r["id"]
    calls = activity.recent_call_states(conn, user, pid, 8)
    since = (clock.now() - datetime.timedelta(days=30)).isoformat(timespec="seconds")
    msg = activity.message_stats(conn, user, pid, since)
    if not calls and (msg["n"] or 0) < 3:
        return None  # not enough to say anything
    tasks = sorted(t["id"] for t in person_tasks.tasks_for(conn, user, pid))
    h = hashlib.sha1(f"{BRIEF_STYLE}:{_language(user)}".encode())  # a new language rewrites the brief
    h.update(("|".join(f"{c['id']}:{c['status']}" for c in calls)).encode())
    h.update(f"|{msg['n']}:{msg['t']}|{','.join(tasks)}|{r['relation']}|{r['notes']}".encode())
    return h.hexdigest()[:16]


def _language(user: Optional[User]) -> str:
    """The language ``user``'s briefs are written in: their first one, by name ("German")."""
    return user_locale.language_name(user_locale.languages(user)[0], native=False)


def brief_prompt(conn: sqlite3.Connection, user: User, r: sqlite3.Row) -> str:
    who = user_profile.for_user(user)
    me = who.first_name
    pid = r["id"]
    calls = activity.calls(conn, user, pid, limit=8)
    msgs = activity.recent_messages(conn, user, pid, 40)
    tasks = person_tasks.tasks_for(conn, user, pid)
    sugg = activity.suggestions_for(conn, user, pid)
    call_lines = [f"- {c['at'][:10]} ({c['direction'] or 'call'}): {c['title'] or ''} — {(c['summary'] or '')[:700]}"
                  for c in calls] or ["- (no calls)"]
    msg_lines = [f"- {str(m['posted_at'])[:16].replace('T', ' ')}"
                 f"{' in ' + repr(m['conversation']) if m['is_group'] else ''}: {re.sub(chr(10), ' ', m['text'] or '')[:220]}"
                 for m in msgs] or ["- (no messages)"]
    heard = untrusted(f"calls with {r['name']} and messages from them",
                      "Recent calls (newest first):\n" + "\n".join(call_lines) + "\n\n"
                      f"Recent messages from them (newest first; {me}'s replies aren't captured):\n"
                      + "\n".join(msg_lines))
    lang = _language(user)
    names = ", ".join(loads(r["aliases"], []))
    about = f"About {me}:\n{who.context}\n\n" if who.context else ""
    return f"""Write a short brief about {r['name']} for {who.name}, to glance at when they call or before meeting them.

{about}Person: {r['name']}{f' (also: {names})' if names else ''}{f' — {r["relation"]}' if r['relation'] else ''}
{me}'s notes: {r['notes'] or '(none)'}
Now: {clock.describe_now()}

{heard}

{me}'s open tasks that involve them:
{chr(10).join('- ' + t['title'] for t in tasks) or '- (none)'}
Suggestions waiting for {me}: {', '.join(s['title'] for s in sugg) or '(none)'}

Return:
- `points`: 1–3 short fragments (4–10 words) on what's going on with them lately, most recent and important first.
- `open`: 0–3 open loops between them and {me} — who owes what ("{me} to send the quote", "waiting for her answer about Friday"). Only things not yet done.
Only what is written above; never invent. No emoji, no trailing periods. Write in {lang}, whatever language the calls and messages are in; keep names as written."""


async def refresh_brief(user: User, person_id: str, *, force: bool = False) -> bool:
    """Rewrite the brief of ``user``'s person when its activity changed (or ``force``),
    as ``user``. False when nothing was written (not theirs, not enough activity, unchanged)."""
    with users.acting_as(user):
        with get_db_connection() as conn:
            r = store.row(conn, user, person_id)
            if not r:
                return False
            sig = signature(conn, user, r)
            if not sig or (sig == r["brief_signature"] and not force):
                return False
            prompt = brief_prompt(conn, user, r)
        raw = await llm.complete_json(prompt, _BRIEF_SCHEMA, job="briefs", purpose="people_brief",
                                      ref=f"person:{person_id}")
    brief = {"points": llm.clean_points(raw.get("points"), 3), "open": llm.clean_points(raw.get("open"), 3)}
    with get_db_connection() as conn:
        conn.execute("UPDATE people SET brief = ?, brief_signature = ?, brief_updated_at = ? WHERE id = ? AND user_id = ?",
                     (json.dumps(brief, ensure_ascii=False), sig, now_iso(), person_id, user.id))
        conn.commit()
    return True


def stale_briefs(user: User, limit: int = BRIEF_BATCH) -> List[str]:
    """``user``'s people whose brief is out of date, newest contact first (at most ``limit``)."""
    with get_db_connection() as conn:
        acts = activity.last_activity(conn, user)
        everyone = {r["id"]: r for r in store.rows(conn, user, include_hidden=False)}
        order = sorted(everyone, key=lambda pid: max([str(t) for t, _ in acts.get(pid, {}).values() if t] or [""]),
                       reverse=True)
        out = []
        for pid in order:
            if pid not in acts:
                continue
            sig = signature(conn, user, everyone[pid])
            if sig and sig != everyone[pid]["brief_signature"]:
                out.append(pid)
            if len(out) >= limit:
                break
    return out


def _lock(user: User) -> asyncio.Lock:
    lock = _brief_locks.get(user.id)
    if lock is None:
        lock = _brief_locks[user.id] = asyncio.Lock()
    return lock


async def refresh_briefs(user: User, person_ids: Optional[List[str]] = None) -> int:
    """Rewrite ``user``'s stale briefs (or those of ``person_ids``), as ``user``;
    a failed rewrite keeps the old brief. Returns how many were written."""
    if not store.has_people(user) or not llm.configured("briefs") or budget.holds("people_brief"):
        return 0
    async with _lock(user):
        with users.acting_as(user):
            linking.link_pending(user)
            ids = person_ids or stale_briefs(user)
            done = 0
            for pid in ids:
                try:
                    done += 1 if await refresh_brief(user, pid) else 0
                except Exception as e:
                    logger.warning(f"People brief for {pid} failed: {e}")
            if done:
                logger.info(f"People: rewrote {done} brief(s) for user {user.id}")
            return done


def schedule_briefs(user: User, person_ids: Optional[List[str]] = None) -> bool:
    """Rewrite ``user``'s briefs in the background (one run per user at a time)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    running = _brief_tasks.get(user.id)
    if running and not running.done():
        return True
    _brief_tasks[user.id] = loop.create_task(refresh_briefs(user, person_ids))
    return True


def after_call(user: User, call_id: str) -> None:
    """``user``'s call finished processing: link it and rewrite that person's brief."""
    try:
        linking.link_pending(user)
        with get_db_connection() as conn:
            pid = activity.person_of_call(conn, user, call_id)
            if pid and not store.row(conn, user, pid):
                pid = None
        if pid:
            schedule_briefs(user, [pid])
    except Exception as e:
        logger.warning(f"People after_call {call_id} failed: {e}")


async def run_briefs() -> Dict[str, int]:
    """One pass of the loop: every user with ``people``, each as that user, one
    after another. Returns user id → briefs written (a user whose pass broke is left out)."""
    out: Dict[str, int] = {}
    for uid in connections.users_with(ADDON):
        user = users.get(uid)
        if user is None:
            continue
        try:
            out[uid] = await refresh_briefs(user)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"People briefs for user {uid} failed")
    return out
