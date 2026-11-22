"""Wake briefing + meeting context cards, per user.

A user's briefing is built once a day when they are confirmed awake (the wake
signal in core/wake.py), from their active addons' ``@addon.briefing`` sections
(calendar events, tasks, pending suggestions, Inbox, weather, people nudges,
and any installed addon's section); an addon they have not connected adds
nothing. The model turns that into a headline + a few points, written for
them (``core/user_profile.for_user``); if it fails, a plain version is
written from the same data. One row per user and local date in ``briefings``;
delivered through ``core.notify.send`` (``briefing``), read by the phone's
widgets from ``GET /api/briefing/today`` and shown by the dashboards' Reminders
widget in the morning (addons/tasks/hooks.py).

Who gets one (:func:`available`): a user with a delivery channel (e.g. phone
push), or with a Telegram chat and at least one briefing hook. If no wake is
seen by ``BRIEFING_FALLBACK_TIME`` (10:00) each such user's briefing is made
anyway (not pushed) so the app and widgets have it.

Meeting cards: ``MEETING_CARD_MINUTES`` (10) before an upcoming calendar event
whose title or attendees name a person, that person's card is delivered as a
``context_card`` notification.
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import inspect
import json
import logging
import os
import re
import time
from typing import Any, Dict, FrozenSet, List, Optional

from core import clock, composers, llm, notify as notify_mod, user_locale, user_profile, users
from core.addons import BriefingContext, Section, registry, store
from core.db import get_db_connection
from core.notify import notify
from core.users import User

logger = logging.getLogger(__name__)

FALLBACK_TIME = os.getenv("BRIEFING_FALLBACK_TIME", "10:00")
BRIEFING_TELEGRAM = os.getenv("BRIEFING_TELEGRAM", "0") == "1"
MEETING_CARD_MINUTES = int(os.getenv("MEETING_CARD_MINUTES", "10"))
MEETING_CARDS = os.getenv("MEETING_CARDS", "1") != "0"
MEETING_BACKOFF_SECONDS = 900

# Where gather() keeps the addons' sections (their plain points, headline and
# counts) until the briefing is written; never stored or shown to the model.
SECTIONS = "_sections"
# The keys of the briefing's own metadata: an addon section under one of these is left out.
CORE_KEYS = frozenset({"date", "weekday", "calendar_ok", SECTIONS})

# user id → the lock that keeps one build of their briefing at a time.
_build_locks: Dict[str, asyncio.Lock] = {}
# "<user id>:<event id>@<start>" → the local date the card was sent.
_sent_meeting_cards: Dict[str, str] = {}
# user id → monotonic time before which their meeting cards are not checked again (after a failure).
_meeting_backoff: Dict[str, float] = {}


def available(user: Optional[User]) -> bool:
    """Whether ``user`` gets a briefing: a delivery channel (phone), or a
    Telegram chat with at least one briefing section addon."""
    if user is None:
        return False
    if registry.channels(user):
        return True
    return user.telegram_chat_id is not None and bool(registry.hooks("briefing", user))


def require(user: User) -> None:
    """Raise ``NotConnected`` (403 ``not_connected``) when ``user`` gets no briefing."""
    if not available(user):
        ch = next((c.addon for a in registry.all_addons() for c in a.channels), "phone")
        raise store.NotConnected(user, ch)


def _today() -> str:
    return clock.now().date().isoformat()


def _now_iso() -> str:
    return clock.now().isoformat(timespec="seconds")


def _row_to_api(r) -> Dict[str, Any]:
    data = json.loads(r["data"] or "{}")
    return {"date": r["date"], "headline": r["headline"], "points": json.loads(r["points"] or "[]"),
            "data": data, "trigger": r["trigger"], "created_at": r["created_at"], "delivered_at": r["delivered_at"]}


def get_today(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s briefing of today (None when not made yet)."""
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM briefings WHERE user_id = ? AND date = ?", (user.id, _today())).fetchone()
    return _row_to_api(r) if r else None


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------

def _trim_counts(kind: str, hook: Any, section: Section) -> Section:
    declared = hook.option("counts", ())
    extra = sorted(set(section.counts) - set(declared))
    if extra:
        logger.warning(f"The {kind} section of {hook.addon} gave undeclared counts {extra}; left out")
        section.counts = {k: v for k, v in section.counts.items() if k in declared}
    return section


def collect(kind: str, user: User, now: datetime.datetime,
            reserved: FrozenSet[str] = frozenset()) -> Dict[str, Section]:
    """Section key → section of every synchronous ``@addon.<kind>`` hook
    (``briefing`` or ``review``) of the addons active for ``user``, in rank /
    addon order; one that fails or returns None is left out (logged), so is one
    under a ``reserved`` key. A briefing section keeps only its declared ``counts``."""
    out: Dict[str, Section] = {}
    ctx = BriefingContext(now=now)
    hooks = sorted(registry.hooks(kind, user), key=lambda h: h.option("rank", 50))
    for hook in hooks:
        if hook.key in reserved:
            logger.warning(f"The {kind} section {hook.key!r} of {hook.addon} is one of core's keys; left out")
            continue
        if inspect.iscoroutinefunction(hook.func):
            continue
        try:
            section = hook.func(user, ctx)
        except Exception as e:
            logger.warning(f"The {kind} section of {hook.addon} failed for user {user.id}: {e}", exc_info=True)
            continue
        if section is None:
            continue
        out[hook.key] = _trim_counts(kind, hook, section)
    return out


async def collect_async(kind: str, user: User, now: datetime.datetime,
                        reserved: FrozenSet[str] = frozenset()) -> Dict[str, Section]:
    """Async counterpart to :func:`collect`: runs both sync (in a thread) and
    async ``@addon.<kind>`` hooks for ``user`` in rank / addon order."""
    out: Dict[str, Section] = {}
    ctx = BriefingContext(now=now)
    hooks = sorted(registry.hooks(kind, user), key=lambda h: h.option("rank", 50))
    for hook in hooks:
        if hook.key in reserved:
            logger.warning(f"The {kind} section {hook.key!r} of {hook.addon} is one of core's keys; left out")
            continue
        try:
            if inspect.iscoroutinefunction(hook.func):
                section = await hook.func(user, ctx)
            else:
                section = await asyncio.to_thread(hook.func, user, ctx)
        except Exception as e:
            logger.warning(f"The {kind} section of {hook.addon} failed for user {user.id}: {e}", exc_info=True)
            continue
        if section is None:
            continue
        out[hook.key] = _trim_counts(kind, hook, section)
    return out


def sections(user: User, now: datetime.datetime) -> Dict[str, Section]:
    """The addons' synchronous parts of ``user``'s briefing (``@addon.briefing``), by section key."""
    return collect("briefing", user, now, CORE_KEYS)


async def weather(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s weather via the active ``weather`` provider (None when unavailable)."""
    hook = registry.provider("weather", user)
    if hook is None:
        return None
    try:
        return await hook.func(user) if inspect.iscoroutinefunction(hook.func) else await asyncio.to_thread(hook.func, user)
    except Exception as e:
        logger.debug(f"Briefing weather failed: {e}")
        return None


async def gather(user: User) -> Dict[str, Any]:
    """What ``user``'s briefing is made of, from their own active addons."""
    now = clock.now()
    today = now.date().isoformat()
    defaults: Dict[str, Any] = {}
    for hook in sorted(registry.hooks("briefing", None), key=lambda h: h.option("rank", 50)):
        if hook.key not in CORE_KEYS and hook.option("has_default", False):
            defaults[hook.key] = copy.deepcopy(hook.option("default"))
    parts = await collect_async("briefing", user, now, CORE_KEYS)
    data: Dict[str, Any] = {"date": today, "weekday": now.strftime("%A"), "calendar_ok": "events" in parts}
    data.update(defaults)
    for key, section in parts.items():
        data[key] = section.data
    data[SECTIONS] = list(parts.values())
    return data


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def plain_briefing(d: Dict[str, Any]) -> Dict[str, Any]:
    """The no-model version: always works, says the same things more plainly."""
    parts: List[Section] = d.get(SECTIONS) or []
    pts: List[str] = []
    if parts:
        for p in parts:
            pts += p.points
    else:
        w = d.get("weather")
        if w and w.get("temperature") is not None:
            hi = f", high {w['high']}°" if w.get("high") is not None else ""
            pts.append(f"{w['condition'].capitalize()}, {w['temperature']}°{hi}")
        evs = d.get("events") or []
        if evs:
            first = evs[0]
            more = f" + {len(evs) - 1} more" if len(evs) > 1 else ""
            pts.append(f"{first['time']} {first['title']}{more}")
        if (d.get("inbox") or {}).get("needs_you"):
            pts.append(f"{d['inbox']['needs_you']} notification(s) need you")
        if (d.get("suggestions") or {}).get("count"):
            pts.append(f"{d['suggestions']['count']} suggestion(s) waiting")
        for b in (d.get("people") or {}).get("birthdays", [])[:1]:
            pts.append(f"{b['name']}'s birthday " + ("today" if b["in_days"] == 0 else f"in {b['in_days']} days"))
        for r in (d.get("people") or {}).get("reach_out", [])[:1]:
            pts.append(f"Haven't talked to {r['name']} in {r['days_since']} days")
    evs = d.get("events") or []
    n = len(evs)
    headline = f"{d['weekday']}: " + (f"{n} event{'s' if n != 1 else ''}" if n else "a clear calendar")
    headline += "".join(p.headline for p in parts)
    return {"headline": headline, "points": pts[:6] or ["Nothing scheduled — enjoy the day"]}


def briefing_prompt(d: Dict[str, Any], user: Optional[User]) -> str:
    """The model prompt for ``user``'s briefing, written for them (their name, pronoun and context)."""
    who = user_profile.for_user(user)
    his = who.possessive
    language = user_locale.language_name(user_locale.languages(user)[0], native=False)
    return f"""Write {who.name}'s morning briefing. {who.pronoun.capitalize()} just woke up and will glance at it on {his} phone, watch and bedroom tablet.

{who.about()}Now: {clock.describe_now()}

Today's data (JSON):
{json.dumps({k: v for k, v in d.items() if k not in ('calendar_ok', SECTIONS)}, ensure_ascii=False, indent=1)}
{'' if d.get('calendar_ok', True) else '(The calendar could not be read today — say nothing about meetings.)'}

Return:
- `headline`: 3–8 words capturing the shape of the day ("Busy morning, free after 2", "Quiet Friday, two things due").
- `points`: 3–6 short fragments (4–12 words), most useful first: the first event and anything time-critical, what's due or overdue, what needs {who.obj} in the Inbox, weather only if it matters (rain, heat, cold), birthdays, one person to reach out to. Skip empty categories; never pad.
Fragments, not sentences; no trailing periods; no emoji; no advice beyond the facts. Use 24h times. Write in {language}; keep names, event titles and task titles as written, in their own language."""


_SCHEMA = {"type": "object", "properties": {"headline": {"type": "string"},
                                            "points": {"type": "array", "items": {"type": "string"}}},
           "required": ["headline", "points"]}


async def compose(d: Dict[str, Any], user: User) -> Dict[str, Any]:
    """Headline + points for ``user``'s briefing data (the plain version when the model fails)."""
    if llm.configured("briefs"):
        try:
            with users.acting_as(user):  # usage is theirs
                raw = await llm.complete_json(briefing_prompt(d, user), _SCHEMA, job="briefs", temperature=0.3,
                                              purpose="wake_briefing")
            points = llm.clean_points(raw.get("points"), 6, chars=140)
            headline = re.sub(r"\s+", " ", str(raw.get("headline") or "")).strip().rstrip(".")[:80]
            if headline and points:
                return {"headline": headline, "points": points, "model": True}
        except Exception as e:
            logger.warning(f"Briefing: the model failed, using the plain version ({e})")
    return {**plain_briefing(d), "model": False}


async def build_and_store(user: User, trigger: str) -> Dict[str, Any]:
    """Build ``user``'s briefing of today and store it (replacing one made earlier today)."""
    async with _build_locks.setdefault(user.id, asyncio.Lock()):
        d = await gather(user)
        text = await compose(d, user)
        d.pop(SECTIONS, None)
        d["model"] = text.pop("model", False)
        with get_db_connection() as conn:
            prev = conn.execute("SELECT delivered_at FROM briefings WHERE user_id = ? AND date = ?",
                                (user.id, d["date"])).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO briefings (user_id, date, headline, points, data, trigger, created_at, "
                "delivered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, d["date"], text["headline"], json.dumps(text["points"], ensure_ascii=False),
                 json.dumps(d, ensure_ascii=False), trigger, _now_iso(), prev["delivered_at"] if prev else None))
            conn.commit()
        logger.info(f"Briefing of user {user.id} for {d['date']} built ({trigger}, model={d['model']}): "
                    f"{text['headline']}")
        return get_today(user) or {}


async def ensure_today(user: User, trigger: str) -> Dict[str, Any]:
    return get_today(user) or await build_and_store(user, trigger)


def push_payload(b: Dict[str, Any], counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """The phone's ``briefing`` event: the stored briefing ``b`` plus the
    addons' ``counts`` (e.g. ``due`` from tasks, ``needs_you`` from inbox)."""
    d = b.get("data") or {}
    evs = d.get("events") or []
    return {"date": b["date"], "headline": b["headline"], "points": b["points"],
            "next_event": (f"{evs[0]['time']} {evs[0]['title']}" if evs else None),
            "due": (counts or {}).get("due", 0),
            "needs_you": (d.get("inbox") or {}).get("needs_you", (counts or {}).get("needs_you", 0))}


def counts(user: User) -> Dict[str, int]:
    """The addons' counts for the briefing's push (their sections' ``counts``, now)."""
    out: Dict[str, int] = {}
    for p in sections(user, clock.now()).values():
        for k, v in p.counts.items():
            out[k] = out.get(k, 0) + v
    return out


async def deliver(b: Dict[str, Any], user: User) -> bool:
    """Push the briefing to ``user``'s channels and, with ``BRIEFING_TELEGRAM``, to them on Telegram."""
    payload = push_payload(b, await asyncio.to_thread(counts, user))
    res = await notify_mod.send(user, notify_mod.Message(kind="briefing", data=payload))
    ok = any(res.values())
    with get_db_connection() as conn:
        conn.execute("UPDATE briefings SET delivered_at = ? WHERE user_id = ? AND date = ?",
                     (_now_iso(), user.id, b["date"]))
        conn.commit()
    if BRIEFING_TELEGRAM:
        await notify(user, "☀️ " + b["headline"] + "\n\n" + "\n".join("• " + p for p in b["points"]))
    return ok


async def on_wake(user: User) -> Optional[Dict[str, Any]]:
    """``user`` is confirmed awake: build (if needed) and push their briefing of today once."""
    if not available(user):
        return None
    try:
        b = get_today(user)
        if b and b.get("delivered_at"):
            return b
        if not b or b.get("trigger") == "fallback":
            b = await build_and_store(user, "wake")
        await deliver(b, user)
        return b
    except Exception as e:
        logger.error(f"Wake briefing of user {user.id} failed: {e}", exc_info=True)
        return None


def _fallback_due(now: datetime.datetime) -> bool:
    try:
        hh, mm = (int(x) for x in FALLBACK_TIME.split(":"))
    except ValueError:
        hh, mm = 10, 0
    return (now.hour, now.minute) >= (hh, mm) and now.hour < 23


async def _fallback_for(user: User) -> bool:
    if get_today(user):
        return False
    await build_and_store(user, "fallback")
    return True


async def fallback_once(now: Optional[datetime.datetime] = None) -> Dict[str, bool]:
    """At ``BRIEFING_FALLBACK_TIME``: build today's briefing of every user who
    gets one and has none yet (each as that user, a failing one not stopping
    the next). user id → whether one was built."""
    if not _fallback_due(now or clock.now()):
        return {}
    return await composers.for_each("Briefing fallback", available, _fallback_for)


async def fallback_loop(interval_seconds: int = 60) -> None:
    """Build each user's briefing at ``BRIEFING_FALLBACK_TIME`` when no wake came."""
    while True:
        await fallback_once()
        await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Meeting cards
# ---------------------------------------------------------------------------

def meetings_available(user: Optional[User]) -> bool:
    """Whether meeting cards are checked for ``user`` (calendar + delivery channel)."""
    return user is not None and registry.provider("events_between", user) is not None and bool(registry.channels(user))


async def check_meetings(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Push the card of anyone named in an upcoming event of ``user``'s calendar
    starting within MEETING_CARD_MINUTES."""
    ev_hook = registry.provider("events_between", user)
    if ev_hook is None or not registry.channels(user):
        return 0
    now = now or clock.now()
    evs = await ev_hook.func(user, now, now + datetime.timedelta(minutes=MEETING_CARD_MINUTES + 1)) or []
    card_hook = registry.provider("person_cards_for_text", user)
    sent = 0
    for e in evs:
        if e["all_day"] or not e["start"]:
            continue
        try:
            start = datetime.datetime.fromisoformat(e["start"].replace("Z", "+00:00"))
        except ValueError:
            continue
        mins = (start - now).total_seconds() / 60
        if not (0 <= mins <= MEETING_CARD_MINUTES):
            continue
        key = f"{user.id}:{e['id']}@{e['start']}"
        if key in _sent_meeting_cards:
            continue
        _sent_meeting_cards[key] = now.date().isoformat()
        cards_out = card_hook.func(user, [e["title"], *e["attendees"]], 2) if card_hook is not None else []
        for c in cards_out:
            await notify_mod.send(user, notify_mod.Message(
                kind="context_card",
                data={"reason": "meeting", "event_title": e["title"], "starts_at": e["start"],
                      "minutes": round(mins), "card": c},
            ))
            sent += 1
    today = now.date().isoformat()
    for k in [k for k, d in _sent_meeting_cards.items() if d != today]:
        _sent_meeting_cards.pop(k, None)
    return sent


async def _meetings_for(user: User) -> int:
    if time.monotonic() < _meeting_backoff.get(user.id, 0.0):
        return 0
    try:
        return await check_meetings(user)
    except Exception as e:
        logger.warning(f"Meeting cards check for user {user.id} failed: {e}")
        _meeting_backoff[user.id] = time.monotonic() + MEETING_BACKOFF_SECONDS
        raise


async def meetings_once() -> Dict[str, int]:
    """One meeting-card check for every user with a calendar and delivery channel
    (each as that user; one failing backs off for 15 min, the next still runs)."""
    return await composers.for_each("Meeting cards", meetings_available, _meetings_for)


async def meeting_cards_loop(interval_seconds: int = 120) -> None:
    if not MEETING_CARDS:
        return
    await asyncio.sleep(45)
    while True:
        await meetings_once()
        await asyncio.sleep(interval_seconds)
