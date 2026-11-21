"""Wake briefing + meeting context cards, per user.

A user's briefing is built once a day when they are confirmed awake (the wake
signal in core/wake.py), from their own kinds only: today's calendar, tasks
due or overdue, pending suggestions, the Inbox briefing (already cached), the
weather from their Home Assistant and people nudges (birthdays, people they
usually talk to but haven't lately); a kind they have not connected adds
nothing. The model turns that into a headline + a few points, written for
them (``core/user_profile.for_user``); if it fails, a plain version is
written from the same data. One row per user and local date in
``briefings``; pushed to their phone as a ``briefing`` event and read by the
widgets / bedroom dashboard from ``GET /api/briefing/today``.

Who gets one (:func:`available`): a user with ``phone``, or with a Telegram
chat and at least one kind that has something to say. If no wake is seen by
``BRIEFING_FALLBACK_TIME`` (10:00) each such user's briefing is made anyway
(not pushed) so the app and widgets have it.

Meeting cards: ``MEETING_CARD_MINUTES`` (10) before an event of a user's
calendar whose title or attendees name one of their people, that person's
card is pushed to their phone as a ``context_card`` event (same card as for
an incoming call); for every user with ``calendar`` and ``phone``.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from core import clock, composers, llm, user_profile, users
from core.db import get_db_connection
from core.notify import notify
from core.users import User
from integrations.calendar import config as calendar_config, events as calendar_events
from integrations.framework import store
from integrations.homeassistant import client as ha_client, config as ha_config
from integrations.people import cadence as people_cadence, detail as people_detail, store as people_store
from integrations.people.values import KIND as PEOPLE
from integrations.phone import push as phone_push
from integrations.phone.config import KIND as PHONE
from integrations.phone.notifications import store as notification_store, summary as notification_summary
from integrations.tasks import service as tasks_service
from integrations.tasks.service import KIND as TASKS


logger = logging.getLogger(__name__)

LANGUAGE = os.getenv("BRIEFING_LANGUAGE", "English")
FALLBACK_TIME = os.getenv("BRIEFING_FALLBACK_TIME", "10:00")
BRIEFING_TELEGRAM = os.getenv("BRIEFING_TELEGRAM", "0") == "1"
MEETING_CARD_MINUTES = int(os.getenv("MEETING_CARD_MINUTES", "10"))
MEETING_CARDS = os.getenv("MEETING_CARDS", "1") != "0"
MEETING_BACKOFF_SECONDS = 900

# Kinds whose data a briefing is made of (with nothing of these there is nothing to say).
CONTENT_KINDS = (PHONE, calendar_config.KIND, TASKS, PEOPLE, ha_config.KIND)
# Who meeting cards are for: the event comes from their calendar, the card goes to their phone.
MEETING_KINDS = (calendar_config.KIND, PHONE)

# user id → the lock that keeps one build of their briefing at a time.
_build_locks: Dict[str, asyncio.Lock] = {}
# "<user id>:<event id>@<start>" → the local date the card was sent.
_sent_meeting_cards: Dict[str, str] = {}
# user id → monotonic time before which their meeting cards are not checked again (after a failure).
_meeting_backoff: Dict[str, float] = {}


def available(user: Optional[User]) -> bool:
    """Whether ``user`` gets a briefing: ``phone`` connected (pushed there), or
    a Telegram chat to send it to and at least one kind with something to say."""
    if user is None:
        return False
    mine = store.connected_kinds(user)
    if PHONE in mine:
        return True
    return user.telegram_chat_id is not None and any(k in mine for k in CONTENT_KINDS)


def require(user: User) -> None:
    """Raise ``NotConnected`` (403 ``not_connected``, ``phone``) when ``user`` gets no briefing."""
    if not available(user):
        raise store.NotConnected(user, PHONE)


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

def _hhmm(iso: Optional[str]) -> str:
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo:
            dt = dt.astimezone(clock.get_timezone())
        return dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


async def _events_today(user: User) -> Optional[List[Dict[str, Any]]]:
    """``user``'s events today (remaining and past); None when their
    calendar isn't reachable or they have none."""
    conn = store.get(user, calendar_config.KIND)
    if conn is None:
        return None
    try:
        now = clock.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        evs = await asyncio.to_thread(calendar_events.list_events, conn, start, start + datetime.timedelta(days=1))
    except Exception as e:
        logger.info(f"Briefing: calendar unavailable ({e})")
        return None
    out = []
    for e in evs:
        if e["all_day"] and str(e["start"])[:10] != start.date().isoformat():
            continue
        out.append({"title": e["title"], "time": "all day" if e["all_day"] else _hhmm(e["start"]),
                    "end": "" if e["all_day"] else _hhmm(e["end"]), "location": e["location"][:80]})
    return out


def _tasks(conn, user: User, today: str) -> Dict[str, Any]:
    # The user's tasks (none without the tasks integration).
    where, args = tasks_service.visible_sql(user, "")
    rows = conn.execute(
        "SELECT id, title, priority, due_date, triggers FROM tasks WHERE status = 'needs_action' "
        f"AND due_date IS NOT NULL AND due_date != '' AND substr(due_date, 1, 10) <= ? AND {where} "
        "ORDER BY due_date, CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 12",
        (today, *args)).fetchall()
    due = [{"id": r["id"], "title": r["title"], "priority": r["priority"], "due_date": r["due_date"][:10],
            "overdue": r["due_date"][:10] < today} for r in rows]
    high = conn.execute(
        "SELECT id, title FROM tasks WHERE status = 'needs_action' AND priority = 'high' "
        f"AND (due_date IS NULL OR due_date = '') AND {where} ORDER BY updated_at DESC LIMIT 3", args).fetchall()
    timed = []
    for r in conn.execute("SELECT id, title, triggers FROM tasks WHERE status = 'needs_action' "
                          f"AND triggers LIKE '%\"time\"%' AND {where}", args).fetchall():
        try:
            t = json.loads(r["triggers"] or "{}").get("time")
        except Exception:
            t = None
        if t and str(t)[:10] == today:
            timed.append({"id": r["id"], "title": r["title"], "time": _hhmm(t) or str(t)[11:16]})
    timed.sort(key=lambda x: x["time"])
    return {"due": due, "high_priority": [{"id": r["id"], "title": r["title"]} for r in high], "reminders_today": timed[:6]}


def _suggestions(conn, user: User) -> Dict[str, Any]:
    """``user``'s pending suggestions (none without ``phone``: they come from calls and notifications)."""
    if not store.is_connected(user, PHONE):
        return {"count": 0, "top": []}
    rows = conn.execute("SELECT payload FROM call_suggestions WHERE status = 'pending' AND user_id = ? "
                        "ORDER BY created_at DESC", (user.id,)).fetchall()
    titles = []
    for r in rows[:3]:
        try:
            titles.append(json.loads(r["payload"] or "{}").get("title") or "")
        except Exception:
            pass
    return {"count": len(rows), "top": [t for t in titles if t]}


def _inbox(user: User) -> Dict[str, Any]:
    """``user``'s notifications Inbox (empty without ``phone``)."""
    if not store.is_connected(user, PHONE):
        return {"needs_you": 0, "active": 0, "points": []}
    try:
        c = notification_store.counts(user)
        s = notification_summary.get_summaries(user, refresh=False)
        overall = (s.get("overall") or {}).get("points") or []
        return {"needs_you": c.get("needs_you", 0), "active": c.get("active", 0), "points": overall[:3]}
    except Exception as e:
        logger.debug(f"Briefing inbox failed: {e}")
        return {"needs_you": 0, "active": 0, "points": []}


async def weather(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s weather (their Home Assistant ``weather_entity``):
    condition and temperature, today's high / low / rain chance when the
    forecast answers. None without Home Assistant or when it fails."""
    conn = store.get(user, ha_config.KIND)
    entity = ha_config.setting(conn, ha_config.WEATHER) if conn is not None else None
    if conn is None or not entity:
        return None
    try:
        s = await ha_client.entity_state(conn, entity)
        if s is None:
            return None
        out = {"condition": str(s.get("state") or "").replace("-", " "),
               "temperature": (s.get("attributes") or {}).get("temperature"),
               "unit": (s.get("attributes") or {}).get("temperature_unit") or "°C"}
        d = await ha_client.forecast(conn, entity)
        if d:
            out.update({"high": d.get("temperature"), "low": d.get("templow"),
                        "rain_chance": d.get("precipitation_probability")})
        return out
    except Exception as e:
        logger.debug(f"Briefing weather failed: {e}")
        return None


def _people(user: User) -> Dict[str, Any]:
    """``user``'s birthdays soon and people to reach out to (none without ``people``)."""
    if not store.is_connected(user, PEOPLE):
        return {"birthdays": [], "reach_out": []}
    try:
        everyone = people_cadence.list_people(user, sort="recent")
    except Exception as e:
        logger.debug(f"Briefing people failed: {e}")
        return {"birthdays": [], "reach_out": []}
    bdays = [{"id": p["id"], "name": p["name"], "in_days": p["birthday_in_days"]}
             for p in everyone if p.get("birthday_in_days") is not None and p["birthday_in_days"] <= 3]
    drifting = [p for p in everyone if p["stats"]["drifting"]]
    drifting.sort(key=lambda p: (not p["pinned"], -(p["stats"]["contact_days"] or 0)))
    reach = [{"id": p["id"], "name": p["name"], "days_since": p["stats"]["days_since"],
              "typical_gap_days": p["stats"]["typical_gap_days"]} for p in drifting[:2]]
    return {"birthdays": sorted(bdays, key=lambda b: b["in_days"]), "reach_out": reach}


async def gather(user: User) -> Dict[str, Any]:
    """What ``user``'s briefing is made of, from their own kinds."""
    now = clock.now()
    today = now.date().isoformat()
    events, weather_now = await asyncio.gather(_events_today(user), weather(user))
    with get_db_connection() as conn:
        tasks = _tasks(conn, user, today)
        sugg = _suggestions(conn, user)
    return {"date": today, "weekday": now.strftime("%A"), "events": events or [],
            "calendar_ok": events is not None, "tasks": tasks, "suggestions": sugg, "inbox": _inbox(user),
            "weather": weather_now, "people": _people(user)}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def plain_briefing(d: Dict[str, Any]) -> Dict[str, Any]:
    """The no-model version: always works, says the same things more plainly."""
    pts: List[str] = []
    evs = d["events"]
    if evs:
        first = evs[0]
        more = f" + {len(evs) - 1} more" if len(evs) > 1 else ""
        pts.append(f"{first['time']} {first['title']}{more}")
    t = d["tasks"]
    overdue = [x for x in t["due"] if x["overdue"]]
    today_due = [x for x in t["due"] if not x["overdue"]]
    if today_due:
        pts.append("Due today: " + ", ".join(x["title"] for x in today_due[:3]))
    if overdue:
        pts.append(f"{len(overdue)} overdue: " + ", ".join(x["title"] for x in overdue[:2]))
    if d["inbox"]["needs_you"]:
        pts.append(f"{d['inbox']['needs_you']} notification(s) need you")
    if d["suggestions"]["count"]:
        pts.append(f"{d['suggestions']['count']} suggestion(s) waiting")
    for b in d["people"]["birthdays"][:1]:
        pts.append(f"{b['name']}'s birthday " + ("today" if b["in_days"] == 0 else f"in {b['in_days']} days"))
    for r in d["people"]["reach_out"][:1]:
        pts.append(f"Haven't talked to {r['name']} in {r['days_since']} days")
    w = d.get("weather")
    if w and w.get("temperature") is not None:
        hi = f", high {w['high']}°" if w.get("high") is not None else ""
        pts.insert(0, f"{w['condition'].capitalize()}, {w['temperature']}°{hi}")
    n = len(evs)
    headline = f"{d['weekday']}: " + (f"{n} event{'s' if n != 1 else ''}" if n else "a clear calendar")
    if today_due:
        headline += f", {len(today_due)} due"
    return {"headline": headline, "points": pts[:6] or ["Nothing scheduled — enjoy the day"]}


def briefing_prompt(d: Dict[str, Any], user: Optional[User]) -> str:
    """The model prompt for ``user``'s briefing, written for them (their name, pronoun and context)."""
    who = user_profile.for_user(user)
    his = who.possessive
    return f"""Write {who.name}'s morning briefing. {who.pronoun.capitalize()} just woke up and will glance at it on {his} phone, watch and bedroom tablet.

{who.about()}Now: {clock.describe_now()}

Today's data (JSON):
{json.dumps({k: v for k, v in d.items() if k != 'calendar_ok'}, ensure_ascii=False, indent=1)}
{'' if d.get('calendar_ok', True) else '(The calendar could not be read today — say nothing about meetings.)'}

Return:
- `headline`: 3–8 words capturing the shape of the day ("Busy morning, free after 2", "Quiet Friday, two things due").
- `points`: 3–6 short fragments (4–12 words), most useful first: the first event and anything time-critical, what's due or overdue, what needs {who.obj} in the Inbox, weather only if it matters (rain, heat, cold), birthdays, one person to reach out to. Skip empty categories; never pad.
Fragments, not sentences; no trailing periods; no emoji; no advice beyond the facts. Use 24h times. Write in {LANGUAGE}; keep names, event titles and task titles as written (Hebrew stays Hebrew)."""


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


def push_payload(b: Dict[str, Any]) -> Dict[str, Any]:
    d = b.get("data") or {}
    evs = d.get("events") or []
    return {"date": b["date"], "headline": b["headline"], "points": b["points"],
            "next_event": (f"{evs[0]['time']} {evs[0]['title']}" if evs else None),
            "due": len([t for t in (d.get("tasks") or {}).get("due", []) if not t.get("overdue")]),
            "needs_you": (d.get("inbox") or {}).get("needs_you", 0)}


async def deliver(b: Dict[str, Any], user: User) -> bool:
    """Push the briefing to ``user``'s phone and, with ``BRIEFING_TELEGRAM``, to them on Telegram."""
    ok = await phone_push.send(user, "briefing", push_payload(b))
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
            b = await build_and_store(user, "wake")  # fresh data: the fallback one may be hours old
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
    """Whether meeting cards are checked for ``user`` (``calendar`` and ``phone``)."""
    return user is not None and composers.has_all(user, MEETING_KINDS)


async def check_meetings(user: User, now: Optional[datetime.datetime] = None) -> int:
    """Push the card of anyone named in an event of ``user``'s calendar
    starting within MEETING_CARD_MINUTES to their phone (nothing without
    their calendar and phone; no card without their people)."""
    conn = store.get(user, calendar_config.KIND)
    if conn is None or not store.is_connected(user, PHONE):
        return 0
    now = now or clock.now()
    evs = await asyncio.to_thread(calendar_events.list_events, conn, now,
                                  now + datetime.timedelta(minutes=MEETING_CARD_MINUTES + 1))
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
        pids: List[str] = []
        for text in [e["title"], *e["attendees"]]:
            for pid in people_store.mentioned_in(user, text):
                if pid not in pids:
                    pids.append(pid)
        for pid in pids[:2]:
            c = people_detail.card(user, pid)
            if not c or not (c["last_call"] or c["points"] or c["open"]):
                continue
            await phone_push.send(user, "context_card", {"reason": "meeting", "event_title": e["title"],
                                                         "starts_at": e["start"], "minutes": round(mins), "card": c})
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
    """One meeting-card check for every user with ``calendar`` and ``phone``
    (each as that user; one failing backs off for 15 min, the next still runs)."""
    return await composers.for_each("Meeting cards", meetings_available, _meetings_for)


async def meeting_cards_loop(interval_seconds: int = 120) -> None:
    if not MEETING_CARDS:
        return
    await asyncio.sleep(45)
    while True:
        await meetings_once()
        await asyncio.sleep(interval_seconds)
