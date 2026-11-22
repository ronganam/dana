"""AI summaries of the Inbox: one per app, plus a short overall briefing.

Covers the active (not snoozed) notifications. Each app's summary is cached
in ``notification_summaries`` with a signature of the notifications it was
written from; only apps whose notifications changed are rewritten, each in
its own model call (it sees only that app, so nothing leaks between apps),
then the briefing from those summaries. Reading never waits for the model:
:func:`get_summaries` returns what is cached (``stale`` marks ones being
rewritten) and starts a refresh in the background, at most every
``NOTIFICATION_SUMMARY_MIN_SECONDS``. Clients poll the Inbox anyway.

Apps with fewer than ``NOTIFICATION_SUMMARY_MIN_ITEMS`` notifications get no
summary (the notification says it already).

Per user: each user's summaries cover only their notifications, are cached
under their id, refreshed in their own background task (with its own
back-off) and written by prompts that name them (core/user_profile.py), in
their first language (core/user_locale.py); names and quotes stay as written.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import sqlite3
from typing import Any, Dict, List, Optional

from core import budget, clock, llm, user_locale, user_profile, users
from core.db import get_db_connection
from core.user_profile import Profile
from core.users import User

logger = logging.getLogger(__name__)

MIN_ITEMS = int(os.getenv("NOTIFICATION_SUMMARY_MIN_ITEMS", "2"))
MIN_SECONDS = float(os.getenv("NOTIFICATION_SUMMARY_MIN_SECONDS", "45"))
PER_APP = 40          # newest notifications per app sent to the model
TEXT_CHARS = 400      # per notification
OVERALL = "_overall"
# Part of every signature: bump when the output format changes so cached
# summaries are rewritten (2 = bullet points; 3 = no "[needs <name>]" tag in
# the prompt, which the model sometimes echoed; 4 = in the user's first language).
# The language is part of it too, so changing it rewrites the summaries.
STYLE_VERSION = "4"

# Per user id: the refresh lock, the running refresh and when the last one started.
_locks: Dict[str, asyncio.Lock] = {}
_tasks: Dict[str, asyncio.Task] = {}
_last_runs: Dict[str, float] = {}


def _marker(me: str) -> "re.Pattern[str]":
    """Tags a model may echo from older prompts ("[needs Robin]", "(needs you)",
    "needs Robin:", for the user's first name ``me``): never shown to them."""
    name = re.escape(me.lower()) if me else "the user"
    return re.compile(rf"[\[(]\s*needs\s+({name}|you)\s*[\])]\s*[:\-–—]?\s*"
                      rf"|^\s*(needs\s+({name}|you)|waiting\s+for\s+{name}|other\s+notifications)\s*([:\-–—]\s*|$)", re.I)


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def active_by_app(conn: sqlite3.Connection, user: User) -> Dict[str, List[Dict[str, Any]]]:
    """``user``'s active (not snoozed) notifications by app, newest first."""
    rows = conn.execute(
        "SELECT id, package, app_name, title, text, sender, conversation, is_group, posted_at, bucket, "
        "priority, content_hash FROM notifications WHERE user_id = ? AND status = 'active' "
        "AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY posted_at DESC", (user.id, _now_utc())).fetchall()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["package"], []).append(dict(r))
    return out


def _signature(items: List[Dict[str, Any]], lang: str) -> str:
    h = hashlib.sha1(f"{STYLE_VERSION}:{lang}".encode())
    for n in sorted(items, key=lambda n: n["id"]):
        h.update(f"{n['id']}:{n.get('content_hash') or ''}|".encode())
    return h.hexdigest()[:16]


def _overall_signature(apps: Dict[str, List[Dict[str, Any]]], lang: str) -> str:
    return hashlib.sha1(STYLE_VERSION.encode() + "|".join(f"{p}:{_signature(l, lang)}" for p, l in sorted(apps.items())).encode()).hexdigest()[:16]


def language(user: Optional[User]) -> str:
    """The language code ``user``'s summaries are written in: their first language."""
    return user_locale.languages(user)[0]


def _me(user: Optional[User]) -> str:
    return user_profile.for_user(user).first_name


def clean_point(text: str, me: str = "") -> str:
    """A summary point without any echoed needs-you tag (``me``: the user's first name)."""
    return re.sub(r"\s{2,}", " ", _marker(me).sub("", text or "")).strip(" :-–—")


def points(summary: Optional[str], me: str = "") -> List[str]:
    return [p for p in (clean_point(x, me) for x in (summary or "").split("\n")) if p]


def _cached(conn: sqlite3.Connection, user: User) -> Dict[str, Dict[str, Any]]:
    return {r["scope"]: dict(r) for r in conn.execute("SELECT * FROM notification_summaries WHERE user_id = ?",
                                                      (user.id,))}


def _state(user: User) -> Dict[str, Any]:
    """What is cached for ``user``, and which scopes no longer match their Inbox."""
    me = _me(user)
    lang = language(user)
    with get_db_connection() as conn:
        apps = active_by_app(conn, user)
        cache = _cached(conn, user)
    stale: List[str] = []
    out_apps: Dict[str, Dict[str, Any]] = {}
    for pkg, items in apps.items():
        if len(items) < MIN_ITEMS:
            continue
        c = cache.get(pkg)
        fresh = bool(c and c["signature"] == _signature(items, lang))
        if not fresh:
            stale.append(pkg)
        pts = points(c["summary"] if c else None, me)
        out_apps[pkg] = {
            "package": pkg, "app_name": items[0].get("app_name") or pkg, "count": len(items),
            "summary": "\n".join(pts) if c else None, "points": pts,
            "stale": not fresh, "updated_at": c["updated_at"] if c else None,
        }
    total = sum(len(l) for l in apps.values())
    overall = None
    if total >= MIN_ITEMS:
        c = cache.get(OVERALL)
        fresh = bool(c and c["signature"] == _overall_signature(apps, lang))
        if not fresh:
            stale.append(OVERALL)
        pts = points(c["summary"] if c else None, me)
        overall = {"summary": "\n".join(pts) if c else None, "points": pts,
                   "stale": not fresh, "count": total,
                   "updated_at": c["updated_at"] if c else None}
    return {"overall": overall, "apps": out_apps, "_stale": stale, "_apps": apps}


def get_summaries(user: User, *, refresh: bool = True, force: bool = False) -> Dict[str, Any]:
    """Cached summaries for ``user``'s Inbox; starts a background refresh (as them) when needed."""
    st = _state(user)
    refreshing = False
    if refresh and (st["_stale"] or force):
        refreshing = _schedule(user, force=force)
    return {"overall": st["overall"], "apps": st["apps"], "refreshing": refreshing or _running(user),
            "model": llm.model("notifications")}


def _running(user: User) -> bool:
    task = _tasks.get(user.id)
    return bool(task and not task.done())


def _schedule(user: User, force: bool = False) -> bool:
    if _running(user):
        return True
    if not force and time.monotonic() - _last_runs.get(user.id, 0.0) < MIN_SECONDS:
        return False
    if not llm.configured("notifications") or budget.holds("notification_summary"):
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    with users.acting_as(user):  # the task (and its usage rows) runs as the user
        _tasks[user.id] = loop.create_task(refresh_now(user, force=force))
    return True


def _line(n: Dict[str, Any]) -> str:
    when = str(n.get("posted_at") or "")[5:16].replace("T", " ")
    who = n.get("sender") or ""
    conv = f" in \"{n['conversation']}\"" if n.get("conversation") and n.get("is_group") else ""
    title = n.get("title") or ""
    head = f"{who}{conv}" if who else title
    if who and title and title not in (who, n.get("conversation")):
        head += f" — {title}"
    text = re.sub(r"\s+", " ", n.get("text") or "")[:TEXT_CHARS]
    return f"  - [{when}] {head}: {text}"


def _style(me: str) -> str:
    return f"""How to write the points:
- Short fragments, not sentences: about 4–9 words each, no trailing period. One point per person, chat or topic.
- Lead with what needs {me} (the notifications listed under "Waiting for {me}"): a question to answer, something to do, a deadline.
- Never write list headings, tags or markers (such as "Waiting for {me}") in the points: only what the notifications say.
- Name who wrote, keeping names and chat names exactly as written. Never "{me} needs to…" or "you have…".
- Only what is written below; never add people or topics that aren't there.
- No counts, no app names, no advice, no emoji, no bullet characters. Codes, amounts and links: say what they are for, not the values."""


def _lang_rule(lang: str) -> str:
    """How to word the points in the language ``lang`` (a code), whatever language the notifications are in."""
    name = user_locale.language_name(lang, native=False)
    return (f"Write the points in natural, short {name}, whatever language the notifications are in. "
            "Keep names, chat names, short quotes and product or tech words (GitHub, PR, deploy) as written.\n"
            "The shape (in English): [\"Noa asks to move Thursday's call\", \"Avi sent the contract\", "
            "\"Family group planning Saturday\"]")


def _sections(items: List[Dict[str, Any]], me: str) -> str:
    """The notifications as two lists, the ones that need ``me`` first (a heading
    instead of a per-line tag: the model echoed tags into the points)."""
    needs = [n for n in items if n.get("bucket") == "needs_you"]
    rest = [n for n in items if n.get("bucket") != "needs_you"]
    out = []
    if needs:
        out.append(f"Waiting for {me}:\n" + "\n".join(_line(n) for n in needs))
    if rest:
        out.append(("Other notifications:\n" if needs else "Notifications:\n") + "\n".join(_line(n) for n in rest))
    return "\n\n".join(out)


def app_prompt(user: User, items: List[Dict[str, Any]]) -> str:
    who = user_profile.for_user(user)
    name = items[0].get("app_name") or items[0].get("package")
    more = f" (the newest {PER_APP} of {len(items)})" if len(items) > PER_APP else ""
    return f"""Summarize the notifications from the app "{name}" waiting on {who.name}'s phone{more}, so {who.pronoun} can take them in at a glance.

{who.about()}Now: {clock.describe_now()}

{_sections(items[:PER_APP], who.first_name)}

Return `summary`: 1–3 points, most important first (fewer is better when there's little to say).
{_lang_rule(language(user))}
{_style(who.first_name)}
"""


def overall_prompt(user: User, apps: Dict[str, List[Dict[str, Any]]], summaries: Dict[str, str]) -> str:
    who = user_profile.for_user(user)
    me = who.first_name
    lines = []
    for pkg, items in sorted(apps.items(), key=lambda kv: -len(kv[1])):
        name = items[0].get("app_name") or pkg
        if pkg in summaries:
            lines.append(f"- {name} ({len(items)}): " + "; ".join(points(summaries[pkg], me)))
        else:
            lines.append(f"- {name}: {_line(items[0]).strip()[2:]}")
    needs = [n for items in apps.values() for n in items if n.get("bucket") == "needs_you"]
    urgent = [f"{_line(n).strip()[2:]} (app {n.get('app_name')})" for n in needs[:15]]
    need = (f"\nWaiting for {me}:\n" + "\n".join(f"- {u}" for u in urgent) + "\n") if urgent else ""
    return f"""Write the at-a-glance briefing of the notifications waiting on {who.name}'s phone.

{who.about()}Now: {clock.describe_now()}

By app:
{chr(10).join(lines)}
{need}
Return `overall`: 2–4 points — first what needs {me} (who wants what), then one point for the rest ("PR reviews, login alerts, news").
{_lang_rule(language(user))}
{_style(me)}
"""


def _schema(field: str) -> Dict[str, Any]:
    return {"type": "object", "properties": {field: {"type": "array", "items": {"type": "string"}}}, "required": [field]}


async def _summarize(prompt: str, field: str, me: str, ref: str = "") -> str:
    raw = await llm.complete_json(prompt, _schema(field), job="notifications", purpose="notification_summary", ref=ref)
    items = raw.get(field)
    if isinstance(items, list):
        items = [clean_point(str(p), me) for p in items]
    out = llm.clean_points(items, 4 if field == "overall" else 3)
    if not out:
        raise ValueError("empty summary")
    return "\n".join(out)


def _save(user: User, scope: str, signature: str, text: str, count: int) -> None:
    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO notification_summaries (user_id, scope, signature, summary, count, "
                     "updated_at) VALUES (?, ?, ?, ?, ?, ?)", (user.id, scope, signature, text, count, _now_utc()))
        conn.commit()


async def refresh_now(user: User, force: bool = False) -> Dict[str, Any]:
    """Rewrite ``user``'s summaries that no longer match their Inbox (all of them
    with ``force``), as ``user``.

    Each changed app gets its own call (it sees only its own notifications, so
    nothing leaks between apps), then the briefing is written from the app
    summaries and the notifications that need them."""
    with users.acting_as(user):
        return await _refresh(user, force)


async def _refresh(user: User, force: bool) -> Dict[str, Any]:
    me = _me(user)
    lang = language(user)
    async with _locks.setdefault(user.id, asyncio.Lock()):
        _last_runs[user.id] = time.monotonic()
        st = _state(user)
        apps = st["_apps"]
        stale = set(st["_stale"]) | ((set(st["apps"]) | ({OVERALL} if st["overall"] else set())) if force else set())
        changed = {p: apps[p] for p in st["apps"] if p in stale}
        with_overall = bool(st["overall"]) and (OVERALL in stale or bool(changed))
        if not changed and not with_overall:
            return {"updated": 0}
        sem = asyncio.Semaphore(4)
        errors: List[str] = []

        async def one(pkg: str, items: List[Dict[str, Any]]) -> Optional[str]:
            async with sem:
                try:
                    return await _summarize(app_prompt(user, items), "summary", me, f"app:{pkg}")
                except Exception as e:
                    errors.append(f"{pkg}: {e}")
                    return None

        texts = dict(zip(changed, await asyncio.gather(*(one(p, l) for p, l in changed.items()))))
        updated = 0
        for pkg, text in texts.items():
            if text:
                _save(user, pkg, _signature(changed[pkg], lang), text, len(changed[pkg]))
                updated += 1
        if with_overall:
            current = {p: a["summary"] for p, a in st["apps"].items() if a.get("summary")}
            current.update({p: t for p, t in texts.items() if t})
            try:
                _save(user, OVERALL, _overall_signature(apps, lang),
                      await _summarize(overall_prompt(user, apps, current), "overall", me, "app:all"),
                      sum(len(l) for l in apps.values()))
                updated += 1
            except Exception as e:
                errors.append(f"briefing: {e}")
        with get_db_connection() as conn:  # apps that left the Inbox
            live = set(apps) | {OVERALL}
            for (scope,) in conn.execute("SELECT scope FROM notification_summaries WHERE user_id = ?",
                                         (user.id,)).fetchall():
                if scope not in live:
                    conn.execute("DELETE FROM notification_summaries WHERE user_id = ? AND scope = ?",
                                 (user.id, scope))
            conn.commit()
        if errors:
            logger.warning(f"Notification summaries: {len(errors)} failed — {errors[0]}")
            _last_runs[user.id] = time.monotonic() + 255  # back off ~5 min
        logger.info(f"Notification summaries: rewrote {updated} ({', '.join(changed) or 'briefing'})")
        return {"updated": updated, **({"errors": errors} if errors else {})}
