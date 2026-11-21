"""The Now screen: one short queue of things that need a decision.

``build()`` composes ``GET /api/now`` from plain DB reads and cached values
(calendar + weather are fetched in the background, never awaited for long),
so it is fast and makes no model call. ``act()`` carries out
``POST /api/now/act`` through the same code paths the older endpoints use
(suggestion accept/dismiss, notification status/snooze,
weekly review apply/dismiss) and registers an undo (``core/undo.py``) for
every reversible action.

Item ids are ``<kind-prefix>:<id>``:

- ``approval:<session_id>`` — a chat waiting for Approve / a pick (client-only: opens the thread)
- ``notif:<notification id>`` — a needs-you notification
- ``sug:<suggestion id>`` — a pending suggestion (calls in ``items``; notification ones in ``quiet``)
- ``<prefix>:<id>`` — an addon's card (``@addon.now_cards``, acted on by its
  ``@addon.now_action(prefix)``): the tasks addon's ``task:<task id>``, a
  reminder that fired and wasn't handled
- ``check:<key>`` — a failing self-check (core/checks.py)
- ``review:<review id>`` — the pending weekly review
- ``money:<transaction id>`` — an unusual charge of the last 3 days (with ``money``)
- ``money_save:<saving key>`` — a way to save with a deadline in 14 days, or a bill that just went up
- ``money_target:<saving key>:<YYYY-MM>`` — a watched target at 80 % this month, or last month's result

Cards without a snooze of their own (checks, review, suggestions) are hidden
until a time in ``now_hidden`` (per user: core/now_hidden.py). The contract is
in docs/now.md.

Per user: ``build(user)`` / ``act(user, …)`` compose and act only on that
user's items, from the addons they have connected; an addon they lack adds no
card, count, slot or choice (needs-you notifications, suggestions and the
quiet list need ``phone``, the review ``tasks``, an addon's cards and day
items that addon, events
``calendar``, weather and the "at home" slot / choices ``homeassistant``, the
"on waking" ones the wake flow's addons, unusual charges ``money``). Acting on a card of an addon they lack
answers 403 ``not_connected``; another user's card 404.
"""
from __future__ import annotations

import asyncio
import datetime
import difflib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core import approval_text, briefing, cards, checks, clock, now_hidden, undo, users, wake, weekly_review
from core.db import get_db_connection
from core.users import User
from addons.calendar import config as calendar_config, today as calendar_today_cache, tools as calendar_tools
from core.addons import DayItem, NowContext, registry, store
from addons.homeassistant import client as ha_client, config as ha_config
from addons.money import config as money_config, insights as money_insights, queries as money_queries
from addons.money import savings as money_savings, store as money_store
from addons.people.values import ago as people_ago
from addons.phone import push as phone_push, suggestions as phone_suggestions
from addons.phone.calls import actions as suggestion_actions
from addons.phone.calls.store import parse_call_time
from addons.phone.config import ADDON as PHONE
from addons.phone.notifications import overview as notification_overview, shade as notification_shade
from addons.phone.notifications import store as notification_store
from addons.phone.notifications.triage import NO, YES

logger = logging.getLogger(__name__)

MAX_NOTIFICATIONS = int(os.getenv("NOW_MAX_NOTIFICATIONS", "3"))
MAX_SUGGESTIONS = int(os.getenv("NOW_MAX_SUGGESTIONS", "4"))
MAX_QUIET = 20
MAX_MONEY = 3
# Unusual charges of this many days (today included) become cards.
MONEY_DAYS = 3
# At most this many way-to-save cards, and as many target cards.
MAX_SAVINGS = 2
# A way to save whose deadline is this close, or a bill increase Dana found this recently, is a card.
SAVINGS_DUE_DAYS = 14
SAVINGS_NEW_DAYS = 7
# A watched target this far spent this month is a card; last month's result shows this many days.
TARGET_WARN_SHARE = 0.8
TARGET_RESULT_DAYS = 3
# Call suggestions below this confidence go to `quiet` instead of the queue.
QUIET_CONFIDENCE = float(os.getenv("NOW_QUIET_CONFIDENCE", "0.7"))
APPROVAL_MAX_HOURS = 48
WEATHER_TTL = 1800
CALENDAR_WAIT_SECONDS = float(os.getenv("NOW_CALENDAR_WAIT_SECONDS", "1.5"))
# Day strip: hour slots from the next full hour through DAY_LAST_HOUR (the
# 23:00 slot covers 23:00–23:59); between midnight and DAY_FIRST_HOUR it starts
# at DAY_FIRST_HOUR (no slots for the sleeping hours).
DAY_FIRST_HOUR = 6
DAY_LAST_HOUR = 23
# The morning briefing's headline is the lede only this long after it was made.
LEDE_BRIEFING_HOURS = 3
_SUGGESTION_OVERRIDES = ("title", "triggers", "due_date", "priority", "group", "description",
                         "start", "end", "location")

# Per user (their Home Assistant): user id → cache.
_weather: Dict[str, Dict[str, Any]] = {}
_home: Dict[str, Dict[str, Any]] = {}


def _utc(dt: Optional[datetime.datetime] = None) -> str:
    return now_hidden.utc_iso(dt)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _parse(value: Any) -> Optional[datetime.datetime]:
    return checks.parse(value)


def ago(dt: Optional[datetime.datetime], now: Optional[datetime.datetime] = None) -> str:
    return people_ago(dt, now or clock.now()) if dt else ""


def when_phrase(dt: datetime.datetime, now: Optional[datetime.datetime] = None) -> str:
    """'19:40' (today) / 'tonight 20:00' / 'tomorrow 09:00' / 'Thu 18:00' / '3 Dec 10:00'."""
    now = now or clock.now()
    dt = dt.astimezone(now.tzinfo)
    hm = dt.strftime("%H:%M")
    days = (dt.date() - now.date()).days
    if days == 0:
        return f"tonight {hm}" if dt.hour >= 18 and now.hour < 18 else hm
    if days == 1:
        return f"tomorrow {hm}"
    if 1 < days < 7:
        return f"{dt.strftime('%a')} {hm}"
    return f"{dt.day} {dt.strftime('%b')} {hm}"


def _home_cache() -> Dict[str, Any]:
    """The current user's home-zone cache (Now's formatting helpers run as the user they compose for)."""
    return _home.setdefault(users.current_id() or "", {"name": "home", "at": 0.0})


def _home_zone() -> str:
    return _home_cache()["name"]


def has_home(user: Optional[User]) -> bool:
    """Whether "at home" means something for ``user`` (a Home Assistant to have zones)."""
    return user is not None and store.is_connected(user, ha_config.ADDON)


def _has_wake(user: Optional[User]) -> bool:
    """Whether "on waking" means something for ``user`` (the wake flow runs for them)."""
    return wake.available(user)


async def _resolve_home_zone(user: User) -> str:
    """``user``'s HA zone that is "home" (zone.home's friendly name, lower
    case; "home" without Home Assistant)."""
    cache = _home.setdefault(user.id, {"name": "home", "at": 0.0})
    if time.monotonic() - cache["at"] < 600:
        return cache["name"]
    conn = store.get(user, ha_config.ADDON)
    if conn is None:
        return cache["name"]
    try:
        zones = await asyncio.wait_for(ha_client.get_ha_zones_list(conn), timeout=5)
        for z in zones or []:
            if z.get("entity_id") == "zone.home" and z.get("name"):
                cache["name"] = z["name"].lower()
                break
        cache["at"] = time.monotonic()
    except Exception as e:
        logger.debug(f"Home zone lookup failed: {e}")
    return cache["name"]


def is_home(loc: Any) -> bool:
    return isinstance(loc, dict) and str(loc.get("location") or "").lower() in (_home_zone(), "home") \
        and (loc.get("event") or "arrive") == "arrive"


def trigger_phrase(triggers: Dict[str, Any], now: Optional[datetime.datetime] = None) -> str:
    """How a reminder fires, as a phrase: 'tomorrow 09:00', 'when you get home'…"""
    triggers = triggers or {}
    if triggers.get("time"):
        dt = cards.parse_local(triggers["time"])
        return when_phrase(dt, now) if dt else str(triggers["time"])
    loc = triggers.get("location")
    if isinstance(loc, dict) and loc.get("location"):
        if is_home(loc):
            return "when you get home"
        zone = str(loc["location"]).title()
        return f"when you leave {zone}" if loc.get("event") == "leave" else f"when you arrive at {zone}"
    if triggers.get("car"):
        return "when you get out of the car" if triggers["car"] == "leave" else "when you get in the car"
    if triggers.get("wake"):
        return "when you wake"
    return ""


def _remind_label(triggers: Dict[str, Any], now: Optional[datetime.datetime] = None) -> str:
    triggers = triggers or {}
    if triggers.get("time"):
        return f"Remind me {trigger_phrase(triggers, now)}"
    loc = triggers.get("location")
    if isinstance(loc, dict) and loc.get("location"):
        return "Remind me at home" if is_home(loc) else \
            f"Remind me {trigger_phrase(triggers, now).replace('you ', 'I ')}"
    if triggers.get("car"):
        return "Remind me in the car"
    if triggers.get("wake"):
        return "Remind me when I wake"
    return "Add to reminders"


def _set_message(triggers: Dict[str, Any], now: Optional[datetime.datetime] = None) -> str:
    phrase = trigger_phrase(triggers, now)
    return f"Reminder set · {phrase}" if phrase else "Added to reminders"


def fmt_date(d: Optional[str]) -> str:
    try:
        x = datetime.date.fromisoformat(str(d)[:10])
    except ValueError:
        return str(d or "")
    today = clock.now().date()
    if x == today:
        return "today"
    if x == today + datetime.timedelta(days=1):
        return "tomorrow"
    if 0 < (x - today).days < 7:
        return x.strftime("%a")
    return f"{x.day} {x.strftime('%b')}"


# ---------------------------------------------------------------------------
# Cached outside data: calendar + weather (never block the request for long)
# ---------------------------------------------------------------------------

def _refresh(cache: Dict[str, Any], fn) -> Optional[asyncio.Task]:
    t = cache.get("task")
    if t and not t.done():
        return t
    try:
        cache["task"] = asyncio.get_running_loop().create_task(fn())
    except RuntimeError:
        return None
    return cache["task"]


async def calendar_today(user: User, wait: float = CALENDAR_WAIT_SECONDS) -> Optional[List[Dict[str, Any]]]:
    """``user``'s events today (their calendar's cache, refreshed in the
    background every 5 min). Waits up to ``wait`` seconds only when there is
    nothing for today yet; None when not read today or without the addon."""
    return await calendar_today_cache.events_today(user, wait)


async def _fetch_weather(user: User, cache: Dict[str, Any]) -> None:
    try:
        cache["value"] = await briefing.weather(user)
    except Exception as e:
        logger.debug(f"Now: weather failed ({e})")
    finally:
        cache["at"] = time.monotonic()


def weather_cached(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s cached weather (None without their Home Assistant)."""
    if not store.is_connected(user, ha_config.ADDON):
        return None
    cache = _weather.setdefault(user.id, {"value": None, "at": 0.0, "task": None})
    if time.monotonic() - cache["at"] > WEATHER_TTL:
        _refresh(cache, lambda: _fetch_weather(user, cache))
    return cache["value"]


def _event_start(e: Dict[str, Any]) -> Optional[datetime.datetime]:
    if e.get("all_day") or not e.get("start"):
        return None
    return cards.parse_local(e["start"])


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _approval_items(now: datetime.datetime, user_id: str) -> List[Dict[str, Any]]:
    """The chat of ``user_id`` waiting for an answer (approvals are in the conversation core: every user has them)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT session_id, pending_approval, title, updated_at FROM sessions "
                "WHERE user_id = ? AND pending_approval IS NOT NULL "
                "ORDER BY (session_id = 'main') DESC, updated_at DESC LIMIT 5", (user_id,)).fetchall()
    except Exception:
        return []
    for r in rows:
        at = _parse(r["updated_at"])
        if at and (now - at).total_seconds() > APPROVAL_MAX_HOURS * 3600:
            continue
        p = cards.loads(r["pending_approval"], {})
        if not isinstance(p, dict):
            continue
        status = p.get("status") or "pending_approval"
        if status not in ("pending_approval", "tool_call", "awaiting_selection"):
            continue
        sid = r["session_id"]
        if status == "awaiting_selection":
            title = cards.clip(p.get("prompt") or "Dana asked you to pick", 120)
            label, blocks = "Pick", []
            opts = [o.get("label") if isinstance(o, dict) else str(o) for o in (p.get("options") or [])][:6]
            if opts:
                blocks.append({"type": "facts", "label": "Options", "items": [cards.clip(o, 80) for o in opts if o]})
        else:
            # The same wording as the Telegram Approve message (core/approval_text.py).
            d = approval_text.describe(str(p.get("tool_name") or ""), p.get("tool_args"))
            title = cards.clip(d["title"], 160)
            label = "Review"
            blocks = []
            if d["body"]:
                blocks.append({"type": "text", "label": d["body_label"] or "Content", "text": cards.clip(d["body"], 600)})
            if d["facts"]:
                blocks.append({"type": "facts", "label": "Details",
                               "items": [cards.clip(f"{k}: {v}", 200) for k, v in d["facts"]]})
        item = {
            "id": f"approval:{sid}", "kind": "approval",
            "source": f"Chat · {r['title'] or 'Dana'}" + (f" · {ago(at, now)}" if at else ""),
            "title": title,
            **({"why": f"You asked: “{cards.clip(p['user_text'], 100)}”"} if p.get("user_text") else {}),
            "primary": cards.action("open", label, client=True),
            "secondary": [],
            "schedulable": False,
            "ref": {"type": "thread", "id": sid},
            "blocks": blocks,
        }
        return [item]
    return []


# --- Notification cards: headline, who, why ------------------------------------------

# "3 new messages", "(2) הודעות חדשות": a count, not content and not a sender.
_COUNT_LINE = re.compile(r"^\(?\d+\)?\s+(new\s+)?(messages?|emails?|notifications?|chats?|הודעות(\s+חדשות)?)\b", re.I)
# WhatsApp's "Dan (2 messages)".
_COUNT_SUFFIX = re.compile(r"\s*\(\d+\s+[^)]{1,20}\)\s*$")
_SPEAKER = re.compile(r"^([^:\n]{1,30}):\s+(.+)$")


def _cmp(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").casefold()).strip()


def _title_is_content(title: str, first_line: str) -> bool:
    """Whether a non-chat notification's title is its message ("Your package has
    arrived" over "Tap to track") rather than who / what it is from ("Bright Data"
    over the subject, "homelab" over "CI passed on #3")."""
    words = len(title.split())
    return (title.rstrip()[-1:] in "?!.…:" or words >= 5
            or (words >= 3 and len(title) > len(first_line)))


def notification_parts(n: Dict[str, Any]) -> Dict[str, str]:
    """``headline`` (what the notification says), ``who`` (sender / where it is
    from, '' when the app or the headline already says it) and ``rest`` (the
    text after the headline, for the card's text block).

    Chats (a sender or a conversation): the newest message is the headline, the
    sender (" in <group>") is who. Everything else (mail, GitHub, banks, …): the
    first meaningful line of the text is the headline and the title is who,
    unless the title itself reads as the message."""
    app = (n.get("app_name") or "").strip()
    title = _COUNT_SUFFIX.sub("", (n.get("title") or "").strip())
    lines = [l.strip() for l in (n.get("text") or "").split("\n") if l.strip()]
    sender = (n.get("sender") or "").strip()
    conv = (n.get("conversation") or "").strip()
    if sender or conv:
        who = sender or conv
        if sender and conv and n.get("is_group") and _cmp(conv) != _cmp(sender):
            who = f"{sender} in {conv}"
        last = lines[-1] if lines else ""
        m = _SPEAKER.match(last)
        if m and (n.get("is_group") or _cmp(m.group(1)) == _cmp(sender)):
            last = m.group(2).strip()
        return {"headline": last or title or app, "who": who if _cmp(who) != _cmp(app) else "", "rest": ""}
    useful = [l for l in lines if _cmp(l) != _cmp(title) and not _COUNT_LINE.match(l)]
    is_label = title and not _COUNT_LINE.match(title) and _cmp(title) != _cmp(app)
    if not useful:
        return {"headline": title or app, "who": "", "rest": ""}
    if title and not _COUNT_LINE.match(title) and _title_is_content(title, useful[0]):
        return {"headline": title, "who": "", "rest": "\n".join(useful)}
    headline = useful[0]
    who = title if is_label and _cmp(title) not in _cmp(headline) else ""
    return {"headline": headline, "who": who, "rest": "\n".join(useful[1:])}


def _rule_subject(conn, n: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """The rule of the notification's user that applies (sender rule first, as
    the triage reads it) and what it was set on: the sender's name or the app's."""
    sender = n.get("sender") or ""
    if sender:
        r = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ?",
                         (n["user_id"], n["package"], sender)).fetchone()
        if r:
            return r["action"], sender
    r = conn.execute("SELECT action FROM notification_rules WHERE user_id = ? AND package = ? AND sender = ''",
                     (n["user_id"], n["package"])).fetchone()
    return (r["action"] if r else None), (n.get("app_name") or "this app")


def notification_why(n: Dict[str, Any], rule: Optional[str], subject: str = "") -> Optional[str]:
    """Why a needs-you notification is in the queue, from what the triage
    answered (``notifications.triage``) and the user's rule — or None when
    there is nothing more specific to say than "it's here"."""
    tri = cards.loads(n.get("triage"), {})
    tri = tri if isinstance(tri, dict) else {}

    def sig(name: str) -> float:
        try:
            return float(tri.get(name))
        except (TypeError, ValueError):
            return 0.5  # not asked / not answered: unsettled

    person, action, soon = sig("from_person"), sig("needs_action"), sig("time_sensitive")
    asks_now, to_me, own = sig("urgent_request"), sig("addressed_to_owner"), sig("owner_wrote_it")
    cat = n.get("category") or ""
    who = (n.get("sender") or "").strip()
    if not who and person >= YES:
        parts = notification_parts(n)
        who = parts["who"]
    subject = subject or n.get("app_name") or "this app"

    main = ""
    # What a person wants says more than the rule that let it through.
    if asks_now >= YES and person >= YES and own < YES and to_me > NO:
        return f"{who or 'Someone'} says it's urgent"
    if person >= YES and action >= YES and own < YES and to_me > NO:
        main = f"{who} is waiting for an answer" if who else "Someone is waiting for an answer"
    elif rule == "alert":
        main = f"You asked to be interrupted for {subject}"
    elif rule == "important":
        main = f"You marked {subject} as important"
    elif cat == "security":
        main = "Security alert"
    elif cat == "money" and action >= YES and sig("record_only") < YES:
        main = "A payment to handle"
    elif cat == "money":
        main = "About your money"
    elif cat == "delivery" and action >= YES:
        main = "A delivery needs you"
    if soon >= YES:
        return f"{main} · time-sensitive" if main else "Time-sensitive"
    return main or None


def _chat_lines(text: str) -> List[Dict[str, Any]]:
    out = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([^:\n]{1,30}):\s+(.+)$", line)
        msg: Dict[str, Any] = {"mine": False, "text": cards.clip(m.group(2) if m else line, 300)}
        if m:
            msg["who"] = m.group(1).strip()
        out.append(msg)
    return out


def _chat_block(conn, n: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The last few messages of the conversation this notification belongs to."""
    if not (n.get("sender") or n.get("conversation")):
        return None
    since = (cards.parse_local(n.get("posted_at")) or clock.now()) - datetime.timedelta(hours=12)
    if n.get("conversation"):
        cond, val = "conversation = ?", n["conversation"]
    else:
        cond, val = "sender = ?", n["sender"]
    rows = conn.execute(
        f"SELECT text FROM notifications WHERE user_id = ? AND package = ? AND {cond} AND posted_at >= ? "
        f"AND posted_at <= ? ORDER BY posted_at DESC LIMIT 6",
        (n["user_id"], n["package"], val, since.isoformat(timespec="seconds"), n.get("posted_at") or "")).fetchall()
    msgs: List[Dict[str, Any]] = []
    for r in reversed(rows):
        for m in _chat_lines(r["text"] or ""):
            if not msgs or msgs[-1]["text"] != m["text"]:
                msgs.append(m)
    if not msgs:
        return None
    label = "The chat" + (f" · {n['conversation']}" if n.get("conversation") and n.get("is_group") else "")
    return {"type": "chat", "label": label, "messages": msgs[-5:]}


def _has_phone(user: Optional[Any]) -> bool:
    return user is not None and store.is_connected(user, PHONE)


def _notification_items(user: User, now: datetime.datetime) -> List[Dict[str, Any]]:
    """``user``'s needs-you notifications (none without ``phone``)."""
    if not _has_phone(user):
        return []
    rows_out = []
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND status = 'active' AND bucket = 'needs_you' "
            "AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY posted_at DESC LIMIT ?",
            (user.id, _utc(), MAX_NOTIFICATIONS * 6)).fetchall()]
        seen = set()
        for n in rows:
            if len(rows_out) >= MAX_NOTIFICATIONS:
                break
            api = notification_store.to_api(n)
            rule, subject = _rule_subject(conn, n)
            if rule == "mute":
                continue
            posted = cards.parse_local(n.get("posted_at"))
            parts = notification_parts(n)
            # The same thing notified twice (a second review comment with the
            # same wording, a repeated alert) is one card: the newest.
            same = (n["package"], _norm(parts["headline"]))
            if same in seen:
                continue
            seen.add(same)
            chatty = bool(n.get("sender") or n.get("conversation"))
            title = cards.clip(parts["headline"], 140)
            can_reply = api["on_phone"] and any(a.get("reply") for a in api["actions"])
            blocks = []
            chat = _chat_block(conn, n) if chatty else None
            if chat:
                blocks.append(chat)
            elif parts["rest"]:
                blocks.append({"type": "text", "label": parts["who"] or n.get("app_name") or "Notification",
                               "text": cards.clip(parts["rest"], 400)})
            primary = cards.action("reply", "Reply", client=True) if can_reply else cards.action("open", "Open", client=True)
            why = notification_why(n, rule, subject)
            rows_out.append({
                "id": f"notif:{n['id']}", "kind": "notification",
                "source": " · ".join(x for x in (n.get("app_name"), parts["who"], ago(posted, now)) if x),
                "title": title,
                **({"why": why} if why else {}),
                "primary": primary,
                "secondary": [cards.action("snooze", "In 1 h", {"minutes": 60}), cards.action("dismiss", "Dismiss")],
                "schedulable": True,
                "ref": {"type": "notification", "id": n["id"]},
                "notification": {"id": n["id"], "key": n.get("device_key"), "package": n["package"],
                                 "app": n.get("app_name"), "can_reply": can_reply, "on_phone": api["on_phone"],
                                 "actions": api["actions"]},
                "blocks": blocks,
            })
    return rows_out


# --- Suggestions ---------------------------------------------------------------

def _sentences(transcript: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n+", transcript or "")
    return [p.strip() for p in parts if p and p.strip()]


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", s or "")).strip().lower()


def transcript_lines(transcript: str, evidence: str, around: int = 2) -> Optional[List[Dict[str, Any]]]:
    """A few transcript lines around the one the evidence quotes (``hit``), or None."""
    sents = _sentences(transcript)
    ev = _norm(evidence)
    if not sents or len(ev) < 4:
        return None
    normed = [_norm(s) for s in sents]
    hits = [i for i, s in enumerate(normed) if s and (s in ev or ev in s)]
    if not hits:
        # The quote may span sentences: match its start, else the most similar one.
        head = ev[:25]
        hits = [i for i, s in enumerate(normed) if head and head in s]
    if not hits:
        best = max(range(len(sents)), key=lambda i: difflib.SequenceMatcher(None, normed[i], ev).ratio())
        if difflib.SequenceMatcher(None, normed[best], ev).ratio() < 0.5:
            return None
        hits = [best]
    lo, hi = max(0, hits[0] - around), min(len(sents), hits[-1] + around + 1)
    hi = min(hi, lo + 7)
    out = []
    for i in range(lo, hi):
        s = sents[i]
        m = re.match(r"^([^:\n]{1,25}):\s+(.+)$", s)
        line: Dict[str, Any] = {"text": cards.clip(m.group(2) if m else s, 300)}
        if m:
            line["who"] = m.group(1).strip()
        if i in hits:
            line["hit"] = True
        out.append(line)
    return out


def _choice_label(triggers: Dict[str, Any], now: datetime.datetime) -> str:
    phrase = trigger_phrase(triggers, now)
    if triggers.get("time"):
        return phrase[:1].upper() + phrase[1:]
    if triggers.get("car"):
        return "In the car" if triggers["car"] != "leave" else "Getting out of the car"
    return phrase.replace("when you get", "When I get").replace("when you", "When I")


def _suggestion_choices(user: User, triggers: Dict[str, Any], now: datetime.datetime) -> List[Dict[str, Any]]:
    tomorrow9 = (now + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    options: List[Tuple[str, Dict[str, Any]]] = []
    if triggers:
        options.append((_choice_label(triggers, now), triggers))
    candidates = [("Tomorrow 09:00", {"time": tomorrow9.strftime("%Y-%m-%d %H:%M")})]
    if has_home(user):
        candidates.append(("When I get home", {"location": {"location": _home_zone(), "event": "arrive"}}))
    if _has_wake(user):
        candidates.append(("When I wake", {"wake": True}))
    for label, trig in candidates:
        if not any(json.dumps(trig, sort_keys=True) == json.dumps(t, sort_keys=True) for _, t in options):
            options.append((label, trig))
    if not triggers:
        options.insert(0, ("No reminder", {}))
    out = []
    for i, (label, trig) in enumerate(options[:4]):
        out.append({"label": label, "value": trig, "selected": i == 0,
                    "action_label": _remind_label(trig, now), "message": _set_message(trig, now)})
    return out


def _call_source(s: Dict[str, Any], now: datetime.datetime) -> str:
    c = s.get("call") or {}
    when = parse_call_time(c.get("call_timestamp") or "") if c.get("call_timestamp") else None
    return " · ".join(x for x in ("Call", c.get("contact"), ago(when, now)) if x)


def _notif_source(s: Dict[str, Any], now: datetime.datetime) -> str:
    n = s.get("notification") or {}
    who = notification_parts(n)["who"] if n else ""
    return " · ".join(x for x in (n.get("app_name") or "Notification", who,
                                   ago(cards.parse_local(n.get("posted_at")), now)) if x)


def _suggestion_item(user: User, s: Dict[str, Any], now: datetime.datetime,
                     transcript: Optional[str]) -> Dict[str, Any]:
    p = s["payload"] or {}
    c = s.get("call") or {}
    item: Dict[str, Any] = {
        "id": f"sug:{s['id']}", "kind": "suggestion",
        "source": _call_source(s, now) if s["source_type"] == "call" else _notif_source(s, now),
        "title": cards.clip(p.get("title") or "Suggestion", 160),
        "schedulable": s["kind"] == "task",
        "ref": {"type": "call", "id": s["source_id"]} if s["source_type"] == "call"
        else {"type": "notification", "id": s["source_id"]},
        "blocks": [],
    }
    why = []
    if s["kind"] == "event":
        start = cards.parse_local(p.get("start"))
        why.append(f"Event {when_phrase(start, now)}" if start else "Event")
    if p.get("due_date"):
        why.append(f"Due {fmt_date(p['due_date'])}")
    if c.get("title"):
        why.append(cards.clip(c["title"], 80))
    item["why"] = " · ".join(why) or "From what was said."
    if s["kind"] == "alert":
        item["primary"] = cards.action("dismiss", "Got it")
        item["secondary"] = []
        item["why"] = cards.clip(p.get("text") or item["why"], 200)
    elif s["kind"] == "event":
        start = cards.parse_local(p.get("start"))
        item["primary"] = cards.action("accept", f"Add to calendar · {when_phrase(start, now)}" if start else "Add to calendar")
        item["secondary"] = [cards.action("dismiss", "Dismiss")]
    else:
        trig = p.get("triggers") or {}
        item["primary"] = cards.action("accept", _remind_label(trig, now), {"triggers": trig})
        item["secondary"] = [cards.action("dismiss", "Dismiss")]
        item["blocks"].append({"type": "choices", "label": "Remind me", "key": "triggers",
                               "options": _suggestion_choices(user, trig, now)})
    lines = transcript_lines(transcript or "", p.get("evidence") or "") if transcript else None
    if lines:
        when = parse_call_time(c.get("call_timestamp") or "") if c.get("call_timestamp") else None
        label = "The call" + (f" · {when_phrase(when, now)}" if when else "")
        item["blocks"].insert(0, {"type": "lines", "label": label, "lines": lines})
    elif c.get("summary"):
        item["blocks"].insert(0, {"type": "text", "label": "From the call", "text": cards.clip(c["summary"], 600)})
    elif p.get("evidence"):
        item["blocks"].insert(0, {"type": "text", "label": "What was said", "text": f"“{cards.clip(p['evidence'], 400)}”"})
    return item


def _quiet_entry(s: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    p = s["payload"] or {}
    return {"id": f"sug:{s['id']}", "kind": "suggestion", "title": cards.clip(p.get("title") or "Suggestion", 140),
            "source": _call_source(s, now) if s["source_type"] == "call" else _notif_source(s, now),
            "primary": cards.action("accept", "Add"), "secondary": [cards.action("dismiss", "Dismiss")],
            "ref": {"type": "call" if s["source_type"] == "call" else "notification", "id": s["source_id"]}}


def _suggestions(user: User, now: datetime.datetime,
                 hidden: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """``user``'s pending suggestions: the queue's, the quiet list and its full
    count (nothing without ``phone``: they come from calls and notifications)."""
    if not _has_phone(user):
        return [], [], 0
    pending = [s for s in phone_suggestions.list_suggestions(user, "pending", limit=300)
               if f"sug:{s['id']}" not in hidden]
    loud, quiet = [], []
    for s in pending:
        conf = (s["payload"] or {}).get("confidence")
        low = isinstance(conf, (int, float)) and conf < QUIET_CONFIDENCE
        if s["source_type"] == "call" and (not low or s["kind"] == "alert") and len(loud) < MAX_SUGGESTIONS:
            loud.append(s)
        else:
            quiet.append(s)
    transcripts: Dict[str, str] = {}
    ids = [s["source_id"] for s in loud if s["source_type"] == "call"]
    if ids:
        with get_db_connection() as conn:
            marks = ",".join("?" * len(ids))
            transcripts = {r["id"]: r["transcript"] or "" for r in conn.execute(
                f"SELECT id, transcript FROM phone_calls WHERE id IN ({marks}) AND user_id = ?",
                (*ids, user.id)).fetchall()}
    items = [_suggestion_item(user, s, now, transcripts.get(s["source_id"])) for s in loud]
    return items, [_quiet_entry(s, now) for s in quiet[:MAX_QUIET]], len(quiet)


# --- Snooze choices (any card that can be snoozed) -----------------------------

def snooze_options(user: User, now: datetime.datetime) -> List[Dict[str, Any]]:
    opts = [cards.action("snooze", "1 h", {"minutes": 60})]
    if now.hour < 19:
        opts.append(cards.action("snooze", "Tonight", {"until": now.replace(hour=20, minute=0, second=0, microsecond=0)
                                                 .isoformat(timespec="minutes")}))
    if has_home(user):
        opts.append(cards.action("snooze", "When I get home", {"slot": "home"}))
    opts.append(cards.action("snooze", "Tomorrow morning", {"until": (now + datetime.timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0).isoformat(timespec="minutes")}))
    return opts


# --- Addon hooks ---------------------------------------------------------------

def _hook_cards(user: User, ctx: NowContext) -> List[Dict[str, Any]]:
    """The cards of every ``@addon.now_cards`` of an addon ``user`` has (one failing is logged and skipped)."""
    out: List[Dict[str, Any]] = []
    for hook in registry.hooks("now_cards", user):
        try:
            out += [c.to_json() for c in hook.func(user, ctx) or []]
        except Exception as e:
            logger.warning(f"Now cards of {hook.addon} failed for user {user.id}: {e}", exc_info=True)
    return out


def _hook_day_items(user: User, ctx: NowContext) -> List[DayItem]:
    """The day-strip items of every ``@addon.day_items`` of an addon ``user`` has."""
    out: List[DayItem] = []
    for hook in registry.hooks("day_items", user):
        try:
            out += list(hook.func(user, ctx) or [])
        except Exception as e:
            logger.warning(f"Now day items of {hook.addon} failed for user {user.id}: {e}", exc_info=True)
    return out


# --- Money -----------------------------------------------------------------------

def _money_items(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``user``'s unusual charges of the last :data:`MONEY_DAYS` days (with ``money``), one card each."""
    if not store.is_connected(user, money_config.ADDON):
        return []
    out = []
    for u in money_insights.unusual(user, now.date() - datetime.timedelta(days=MONEY_DAYS - 1)):
        t = u["transaction"]
        if f"money:{t['id']}" in hidden:
            continue
        facts = [f"{t['account_label']} · {fmt_date(t['purchase_date'])}", t["category_label"]]
        facts += [f"Installment {b['number']} of {b['total']}" for b in t["badges"] if b["type"] == "installment"]
        facts += [f"{b['currency']} {b['amount'] / 100:,.2f}" for b in t["badges"] if b["type"] == "foreign"]
        if t["description"] and t["description"] != t["merchant"]:
            facts.append(cards.clip(t["description"], 80))
        out.append({
            "id": f"money:{t['id']}", "kind": "money", "source": f"Money · {t['account_label']}",
            "title": u["title"], "why": u["why"][:1].upper() + u["why"][1:],
            "primary": cards.action("open", "Ask Dana", client=True),
            "secondary": [cards.action("dismiss", "Dismiss")],
            "schedulable": False,
            "ref": {"type": "transaction", "id": t["id"]},
            "blocks": [{"type": "facts", "label": "Charge", "items": facts}],
        })
    return out[:MAX_MONEY]


def _savings_items(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``user``'s saving cards (with ``money``): a way to save due within :data:`SAVINGS_DUE_DAYS` or a bill
    increase first seen in the last :data:`SAVINGS_NEW_DAYS` days, a watched target at
    :data:`TARGET_WARN_SHARE` of this month's, and last month's result in the first days of a month."""
    if not store.is_connected(user, money_config.ADDON):
        return []
    today = now.date()
    try:
        items = money_savings.overview(user, today)["items"]
    except Exception as e:
        logger.warning(f"Now savings cards for user {user.id} failed: {e}")
        return []
    sh = money_store.shekels
    saves: List[Dict[str, Any]] = []
    targets: List[Dict[str, Any]] = []
    for i in items:
        if not i["active"]:
            continue
        if i["group"] == "save" and i["status"] in ("open", "doing"):
            until = (i.get("timing") or {}).get("until")
            left = (datetime.date.fromisoformat(until) - today).days if until else None
            fresh = (i["type"] == "bill" and i["status"] == "open"
                     and (today - datetime.date.fromisoformat(i["first_seen"])).days <= SAVINGS_NEW_DAYS)
            if not ((left is not None and 0 <= left <= SAVINGS_DUE_DAYS) or fresh):
                continue
            item_id = f"money_save:{i['key']}"
            if item_id in hidden:
                continue
            s = i["saving"]
            facts = [f"Saves {sh(s['low'])}–{sh(s['high'])} a year" if s and s["low"] != s["high"]
                     else f"Saves {sh(s['high'])} a year" if s else f"{sh(i['at_stake'])} a year at stake"]
            if i["steps"]:
                facts.append(i["steps"][0]["text"])
            why = (f"{i['timing']['label']} · in {left} day{'s' if left != 1 else ''}" if left is not None
                   and (i.get("timing") or {}).get("label") else i["summary"])
            saves.append({"id": item_id, "kind": "money", "source": "Money · Ways to save", "title": i["title"],
                        "why": why, "primary": cards.action("open", "Ask Dana", client=True),
                        "secondary": [cards.action("dismiss", "Dismiss")], "schedulable": False,
                        "ref": {"type": "saving", "id": i["key"]},
                        "blocks": [{"type": "facts", "label": "Saving", "items": facts}]})
        t = i.get("target")
        if i["status"] != "watching" or not t:
            continue
        name = money_savings.short_name(user, i["key"])
        month = today.strftime("%Y-%m")
        if t["monthly"] and t["this_month"] >= TARGET_WARN_SHARE * t["monthly"] and f"money_target:{i['key']}:{month}" not in hidden:
            share = round(t["this_month"] / t["monthly"] * 100)
            targets.append({"id": f"money_target:{i['key']}:{month}", "kind": "money", "source": "Money · Spending to watch",
                        "title": f"{name}: {sh(t['this_month'])} of {sh(t['monthly'])} this month",
                        "why": f"{share}% of your target with {t['days_left']} day{'s' if t['days_left'] != 1 else ''} left",
                        "primary": cards.action("open", "Ask Dana", client=True), "secondary": [cards.action("dismiss", "Dismiss")],
                        "schedulable": False, "ref": {"type": "saving", "id": i["key"]},
                        "blocks": [{"type": "facts", "label": "Target", "items": [f"Target {sh(t['monthly'])} a month",
                                                                                  f"Spent {sh(t['this_month'])} so far"]}]})
        last = t["months"][-1] if t["months"] else None
        if (today.day <= TARGET_RESULT_DAYS and last and f"money_target:{i['key']}:{last['month']}" not in hidden
                and last["month"] == (today.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")):
            label = datetime.date.fromisoformat(last["month"] + "-01").strftime("%B")
            why = (f"Under your target by {sh(last['target'] - last['spent'])}" if last["under"]
                   else f"Over your target by {sh(last['spent'] - last['target'])}")
            targets.append({"id": f"money_target:{i['key']}:{last['month']}", "kind": "money",
                        "source": "Money · Spending to watch",
                        "title": f"{name} in {label}: {sh(last['spent'])} of {sh(last['target'])}", "why": why,
                        "primary": cards.action("open", "Ask Dana", client=True), "secondary": [cards.action("dismiss", "Dismiss")],
                        "schedulable": False, "ref": {"type": "saving", "id": i["key"]},
                        "blocks": [{"type": "facts", "label": "Target",
                                    "items": [f"Saved {sh(t['saved'])} against your usual pace so far"]}]})
    return saves[:MAX_SAVINGS] + targets[:MAX_SAVINGS]


# --- Checks / review ------------------------------------------------------------

def _check_items(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``user``'s failing checks (only theirs: core/checks.py ``for_user``)."""
    out = []
    for c in checks.for_user(user, now):
        if c["ok"] or f"check:{c['key']}" in hidden:
            continue
        out.append({
            "id": f"check:{c['key']}", "kind": "check", "source": "Dana check",
            "title": c.get("title") or f"{c['label']}: {c['detail']}",
            "why": c.get("why") or c["detail"],
            "primary": cards.action("open", "Details", client=True),
            "secondary": [cards.action("ignore", "Ignore for a week", {"days": 7})],
            "schedulable": True,
            "ref": {"type": "check", "id": c["key"]},
            "blocks": c.get("blocks") or [{"type": "text", "label": c["label"], "text": c["detail"]}],
        })
    return out[:3]


_REVIEW_PHRASE = {"complete": "mark done", "delete": "delete", "keep": "keep", "accept": "add it",
                  "dismiss": "dismiss"}


def _review_label(i: Dict[str, Any]) -> str:
    a = i.get("action")
    if a == "reschedule":
        what = f"move to {fmt_date(i.get('due_date'))}"
    elif a == "move_group":
        what = f"move to {i.get('group')}"
    elif a == "reach_out":
        what = f"remind me to reach out {fmt_date(i.get('due_date'))}"
    else:
        what = _REVIEW_PHRASE.get(a, a or "keep")
    return f"{i.get('title')} → {what}"


def _review_items(user: User, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = weekly_review.latest(user)  # None without tasks
    if not r or r["status"] != "pending" or f"review:{r['id']}" in hidden:
        return []
    items = r["items"]
    proposed = [i for i in items if i.get("action") != "keep"]
    st = r.get("stats") or {}
    title = f"{len(items)} items, {len(proposed)} worth changing" if proposed else f"{len(items)} items to look at"
    blocks: List[Dict[str, Any]] = []
    if st:
        blocks.append({"type": "facts", "label": "This week",
                       "items": [f"{st.get('done', 0)} reminders done · {st.get('created', 0)} added · "
                                 f"{st.get('open', 0)} open"] + list(r.get("summary") or [])[:2]})
    if r.get("money"):
        blocks.append({"type": "facts", "label": "Money", "items": money_queries.week_lines(r["money"])})
    if proposed:
        blocks.append({"type": "checks", "label": "Proposed", "key": "items",
                       "options": [{"label": cards.clip(_review_label(i), 140), "value": i["ref"],
                                    "checked": bool(i.get("enabled"))} for i in proposed]})
    return [{
        "id": f"review:{r['id']}", "kind": "review", "source": "Weekly review",
        "title": title, "why": r.get("headline") or "",
        "primary": cards.action("apply", "Apply") if proposed else cards.action("open", "Review", client=True),
        "secondary": [cards.action("skip", "Skip this week")],
        "schedulable": True,
        "ref": {"type": "review", "id": r["id"]},
        "blocks": blocks,
    }]


# --- Day strip -----------------------------------------------------------------

def day_hours(now: datetime.datetime) -> List[int]:
    """The hour slots left today: every full hour from the next one through
    ``DAY_LAST_HOUR`` (23:00, the last slot of the evening). After midnight
    (before ``DAY_FIRST_HOUR``) the strip starts at 06:00 instead of offering
    the sleeping hours; from 23:00 there are none (only home / wake)."""
    first = max(now.hour + 1, DAY_FIRST_HOUR)
    return list(range(first, DAY_LAST_HOUR + 1))


def day_slots(user: User, now: datetime.datetime, events: Optional[List[Dict[str, Any]]],
              ctx: Optional[NowContext] = None) -> List[Dict[str, Any]]:
    """The day strip of ``user``: hour slots, then "At home" (with Home
    Assistant) and "On waking" (with the wake flow's addons). Events first,
    then the addons' ``@addon.day_items``."""
    hours = day_hours(now)
    first = hours[0] if hours else 24
    slots = {str(h): {"key": str(h), "label": f"{h:02d}:00", "items": []} for h in hours}
    home = {"key": "home", "label": "At home", "items": []}
    waking = {"key": "wake", "label": "On waking", "items": []}

    def slot_for(dt: datetime.datetime) -> Optional[Dict[str, Any]]:
        if dt.date() != now.date() or dt < now or not hours:
            return None
        return slots.get(str(max(dt.hour, first))) if dt.hour <= hours[-1] else None

    for e in events or []:
        st = _event_start(e)
        s = slot_for(st) if st else None
        if s is not None:
            s["items"].append({"title": e.get("title") or "(No title)", "kind": "event",
                               "time": st.strftime("%H:%M"), "event_id": e.get("id")})
    by_slot = {"home": home, "wake": waking}
    for item in _hook_day_items(user, ctx or NowContext(now=now, hidden={}, events=events)):
        target = by_slot.get(item.slot) if item.slot else (slot_for(item.at) if item.at else None)
        if target is not None:
            target["items"].append(item.to_json())
    for s in slots.values():
        s["items"].sort(key=lambda x: x.get("time") or "")
    return [slots[str(h)] for h in hours] + ([home] if has_home(user) else []) + ([waking] if _has_wake(user) else [])


# --- Lede ------------------------------------------------------------------------

def _fresh_briefing(user: User, now: datetime.datetime) -> Optional[str]:
    """``user``'s briefing headline of today while it still describes the
    moment: made today, less than LEDE_BRIEFING_HOURS ago."""
    b = briefing.get_today(user)
    if not b or not b.get("headline"):
        return None
    made = cards.parse_local(b.get("created_at"))
    if not made or made.date() != now.date() or not _within(now - made, LEDE_BRIEFING_HOURS):
        return None
    return b["headline"].strip().rstrip(".")


def _within(delta: datetime.timedelta, hours: float) -> bool:
    return datetime.timedelta(0) <= delta <= datetime.timedelta(hours=hours)


def _lede(user: User, now: datetime.datetime, events: Optional[List[Dict[str, Any]]],
          weather: Optional[Dict[str, Any]] = None, items: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """One line above the queue, never contradicting it. The morning briefing's
    headline while it's fresh and there is something in the queue (an empty
    queue under "urgent tasks and inbox backlog" reads wrong); otherwise, from
    data at hand (no model): the next event today and its time, else null (the
    queue's header already says how many things need the user). Rain likely is
    added when the cached weather says so."""
    n = len(items or [])
    head = _fresh_briefing(user, now) if n else None
    if head:
        return head + "."
    parts = []
    upcoming = [(st, e) for e in events or [] for st in [_event_start(e)] if st and st >= now and st.date() == now.date()]
    if upcoming:
        st, e = min(upcoming, key=lambda x: x[0])
        parts.append(f"{cards.clip(e.get('title'), 60)} at {st.strftime('%H:%M')}")
    # No "N things need you" fallback: the queue's own header already counts.
    rain = (weather or {}).get("rain_chance")
    if isinstance(rain, (int, float)) and rain >= 50:
        parts.append(f"rain likely ({int(rain)}%)")
    if not parts:
        return None
    s = ", ".join(parts)
    return s[0].upper() + s[1:] + "."


# --- The whole screen ------------------------------------------------------------

async def build(user: User) -> Dict[str, Any]:
    """The Now screen for ``user``: only their items, from the addons they have connected."""
    with users.acting_as(user):  # the formatting helpers (home zone) and to_thread see them
        now = clock.now()
        events = await calendar_today(user)
        weather = weather_cached(user)  # here, on the loop: a refresh is started in the background
        hidden = await asyncio.to_thread(now_hidden.active, user)
        ctx = NowContext(now=now, hidden=hidden, events=events)

        def compose() -> Dict[str, Any]:
            sugg_items, quiet, quiet_total = _suggestions(user, now, hidden)
            items = [i for i in _approval_items(now, user.id) if i["id"] not in hidden]
            items += _notification_items(user, now)
            items += sugg_items
            items += _hook_cards(user, ctx)
            items += _money_items(user, now, hidden)
            items += _savings_items(user, now, hidden)
            items += _check_items(user, now, hidden)
            items += _review_items(user, hidden)
            counts = notification_overview.counts(user) if _has_phone(user) else {"total": 0, "noise": 0, "needs_you": 0}
            return {
                "now": now.isoformat(timespec="seconds"),
                "lede": _lede(user, now, events, weather, items),
                "items": items,
                "quiet": quiet,
                "quiet_total": quiet_total,
                "notifications": {"total": counts["total"], "noise": counts["noise"], "needs_you": counts["needs_you"]},
                "day": {"slots": day_slots(user, now, events, ctx), "calendar": events is not None},
            }

        return await asyncio.to_thread(compose)


# ---------------------------------------------------------------------------
# Acting
# ---------------------------------------------------------------------------

async def fix_home(user: User, triggers: Dict[str, Any]) -> Dict[str, Any]:
    """A "home" location from a client (the choices offer it) → the real HA zone name."""
    loc = triggers.get("location")
    if isinstance(loc, dict) and str(loc.get("location") or "").lower() in ("home", _home_zone()):
        triggers["location"] = {"location": await _resolve_home_zone(user), "event": loc.get("event") or "arrive"}
    return triggers


async def slot_trigger(user: User, slot: str, now: datetime.datetime) -> Dict[str, Any]:
    if slot == "home":
        return {"location": {"location": await _resolve_home_zone(user), "event": "arrive"}}
    if slot == "wake":
        return {"wake": True}
    return {"time": cards.slot_time(slot, now).strftime("%Y-%m-%d %H:%M")}


def _hide_until_for_slot(slot: str, now: datetime.datetime) -> datetime.datetime:
    """Checks / review dropped on home or wake: no exact time, so hide until a
    sensible one (wake → tomorrow 06:00, home → 4 hours)."""
    if slot == "wake":
        return (now + datetime.timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    if slot == "home":
        return now + datetime.timedelta(hours=4)
    return cards.slot_time(slot, now)


async def act(user: User, item_id: str, action: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Carry out one action on ``user``'s Now card. Raises ActError (message +
    HTTP status) and ``NotConnected`` (403) when the card's addon is not theirs."""
    args = args if isinstance(args, dict) else {}
    action = (action or "").strip().lower()
    kind, _, ref = (item_id or "").partition(":")
    if not ref:
        raise cards.ActError("id must look like '<kind>:<id>'")
    with users.acting_as(user):
        if item_id == "quiet:all":
            return await _quiet_all(user, action)
        handler = {"sug": _act_suggestion, "notif": _act_notification, "check": _act_check,
                   "review": _act_review, "approval": _act_approval, "money": _act_money,
                   "money_save": _act_money_save, "money_target": _act_money_target}.get(kind)
        if handler:
            return await handler(user, ref, action, args, clock.now())
        hook = registry.hook_for("now_action", kind)
        if hook is None:
            raise cards.ActError(f"unknown kind '{kind}'")
        store.require(user, hook.addon)  # NotConnected → 403 not_connected
        return await hook.func(user, ref, action, args, clock.now())


async def _act_approval(user, ref, action, args, now):
    raise cards.ActError("Approvals are answered in the conversation; open the thread.")


# --- Suggestions ------------------------------------------------------------------

def _restore_suggestion(sid: str, payload: Dict[str, Any], expect: str) -> None:
    with get_db_connection() as conn:
        row = conn.execute("SELECT status FROM call_suggestions WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise RuntimeError("The suggestion no longer exists.")
        if row["status"] != expect:
            raise RuntimeError("The suggestion changed since, so it was left as it is.")
        conn.execute("UPDATE call_suggestions SET status = 'pending', result_ref = NULL, resolved_at = NULL, "
                     "payload = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), sid))
        conn.commit()


async def _accept_suggestion(user: User, sid: str, s: Dict[str, Any], overrides: Dict[str, Any],
                             now: datetime.datetime) -> Dict[str, Any]:
    if isinstance(overrides.get("triggers"), dict):
        overrides["triggers"] = await fix_home(user, dict(overrides["triggers"]))
    original = dict(s["payload"] or {})
    try:
        with undo.collect() as inner:
            res = await suggestion_actions.accept_suggestion(user, sid, overrides)
    except LookupError as e:
        raise cards.ActError(str(e), 404)
    except ValueError as e:
        raise cards.ActError(str(e))
    if res.get("already"):
        raise cards.ActError(f"This suggestion is already {res.get('status')}.", 409)
    if res.get("kind") == "task":
        task_id = res["task_id"]
        triggers = (phone_suggestions.get_suggestion(user, sid) or {}).get("payload", {}).get("triggers") or {}
        msg = _set_message(triggers, now)

        def _undo():
            suggestion_actions.undo_created_task(user, task_id)
            _restore_suggestion(sid, original, "accepted")
        return cards.result(msg, undo.record(msg, _undo, source="now.accept"), task_id=task_id)
    if res.get("kind") == "event":
        start = cards.parse_local((overrides.get("start") or original.get("start")))
        msg = "Added to calendar" + (f" · {when_phrase(start, now)}" if start else "")
        event_ref = (phone_suggestions.get_suggestion(user, sid) or {}).get("result_ref") or ""
        inner_ids = [i["id"] for i in inner]

        async def _undo_event():
            done = False
            for uid in inner_ids:
                try:
                    await undo.run(uid)
                    done = True
                except KeyError:
                    pass
            if not done and event_ref.startswith("event:") and len(event_ref) > 6:
                out = await asyncio.to_thread(calendar_tools.delete_event,
                                              store.require(user, calendar_config.ADDON), event_ref[6:])
                if str(out).startswith("Error"):
                    raise RuntimeError(out)
            _restore_suggestion(sid, original, "accepted")
        return cards.result(msg, undo.record(msg, _undo_event, source="now.accept"))
    # alert → dismissed by accept
    return cards.result("Dismissed", undo.record("Dismissed", lambda: _restore_suggestion(sid, original, "dismissed"),
                                            source="now.accept"))


async def _act_suggestion(user, sid, action, args, now):
    store.require(user, PHONE)  # suggestions come from the phone; NotConnected → 403 not_connected
    s = phone_suggestions.get_suggestion(user, sid)
    if not s:
        raise cards.ActError("Suggestion not found", 404)
    if s["status"] != "pending":
        raise cards.ActError(f"This suggestion is already {s['status']}.", 409)
    if action == "accept":
        overrides = {k: v for k, v in args.items() if k in _SUGGESTION_OVERRIDES}
        if args.get("slot"):
            overrides["triggers"] = await slot_trigger(user, str(args["slot"]), now)
        return await _accept_suggestion(user, sid, s, overrides, now)
    if action == "schedule":
        slot = str(args.get("slot") or "")
        if not slot:
            raise cards.ActError("schedule needs args.slot")
        if s["kind"] != "task":
            raise cards.ActError("Only reminders can be placed on the day")
        return await _accept_suggestion(user, sid, s, {"triggers": await slot_trigger(user, slot, now)}, now)
    if action == "dismiss":
        suggestion_actions.dismiss_suggestion(user, sid)
        payload = dict(s["payload"] or {})
        return cards.result("Dismissed", undo.record(
            "Dismissed suggestion", lambda: _restore_suggestion(sid, payload, "dismissed"), source="now.dismiss"))
    if action == "snooze":
        until = cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Snoozed · back {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"sug:{sid}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a suggestion")


async def _quiet_all(user: User, action: str) -> Dict[str, Any]:
    if action != "dismiss":
        raise cards.ActError("quiet:all only supports dismiss")
    store.require(user, PHONE)
    _, quiet, _ = await asyncio.to_thread(_suggestions, user, clock.now(), now_hidden.active(user))
    done = []
    for q in quiet:
        sid = q["id"].split(":", 1)[1]
        s = phone_suggestions.get_suggestion(user, sid)
        if s and s["status"] == "pending" and suggestion_actions.dismiss_suggestion(user, sid):
            done.append((sid, dict(s["payload"] or {})))
    if not done:
        return cards.result("Nothing to dismiss")

    def _undo():
        for sid, payload in done:
            try:
                _restore_suggestion(sid, payload, "dismissed")
            except RuntimeError:
                pass
    msg = f"{len(done)} dismissed"
    return cards.result(msg, undo.record(msg, _undo, source="now.quiet_all"), count=len(done))


# --- Notifications ------------------------------------------------------------------

async def _act_notification(user, nid, action, args, now):
    store.require(user, PHONE)  # NotConnected → 403 not_connected
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (nid, user.id)).fetchone()
    if not row:
        raise cards.ActError("Notification not found", 404)
    n = dict(row)
    if action in ("open", "reply"):
        raise cards.ActError("Open and reply are done by the phone (its notification listener).")
    if action == "dismiss":
        snap = notification_shade.snapshot(user, [nid])
        res = notification_shade.set_status(user, [nid], "dismissed")
        return cards.result("Dismissed", undo.record(
            "Dismissed notification", lambda: notification_shade.restore(user, snap), source="now.dismiss"),
            device_keys=res["device_keys"])
    if action == "snooze" or (action == "schedule" and str(args.get("slot") or "").isdigit()):
        until = cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        snap = notification_shade.snapshot(user, [nid])
        try:
            res = notification_shade.snooze(user, [nid], until)
        except ValueError as e:
            raise cards.ActError(str(e))
        msg = f"Snoozed · back {when_phrase(until, now)}"
        return cards.result(msg, undo.record(msg, lambda: notification_shade.restore(user, snap), source="now.snooze"),
                       device_keys=res["device_keys"])
    if action == "schedule":
        slot = str(args.get("slot") or "")
        if slot not in ("home", "wake"):
            raise cards.ActError("schedule needs args.slot: an hour, 'home' or 'wake'")
        trig = await slot_trigger(user, slot, now)
        who = n.get("sender") or n.get("title") or n.get("app_name")
        title = f"Reply to {who}" if (n.get("sender") or n.get("conversation")) else \
            cards.clip(notification_parts(n)["headline"], 160)
        desc = f"From a {n.get('app_name')} notification:\n“{cards.clip(n.get('text'), 500)}”"
        try:
            task_id = await suggestion_actions.create_task_record(
                user, {"title": title, "description": desc, "triggers": trig}, link=("notification", nid))
        except ValueError as e:
            raise cards.ActError(str(e))
        snap = notification_shade.snapshot(user, [nid])
        # Out of Now (the reminder carries it); not cancelled in the phone's shade.
        notification_shade.set_status(user, [nid], "archived", reason="reminder")
        msg = _set_message(trig, now)

        def _undo():
            suggestion_actions.undo_created_task(user, task_id)
            notification_shade.restore(user, snap)
        return cards.result(msg, undo.record(msg, _undo, source="now.schedule"), task_id=task_id)
    raise cards.ActError(f"'{action}' isn't an action for a notification")


# --- Money -----------------------------------------------------------------------------------

async def _act_money(user, txn_id, action, args, now):
    store.require(user, money_config.ADDON)  # NotConnected → 403 not_connected
    if money_store.get_transaction(user, txn_id) is None:
        raise cards.ActError("Transaction not found", 404)
    if action == "dismiss":
        msg = "Dismissed"
        return cards.result(msg, cards.hide_with_undo(user, f"money:{txn_id}", cards.FAR, msg, reason="dismiss"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = _hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"money:{txn_id}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a charge")


async def _act_money_save(user, key, action, args, now):
    """A way-to-save card (``money_save:<key>``)."""
    return await _hide_saving_card(user, key, f"money_save:{key}", action, args, now)


async def _act_money_target(user, ref, action, args, now):
    """A target card (``money_target:<key>:<YYYY-MM>``)."""
    key, _, month = ref.rpartition(":")
    if not key or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise cards.ActError("id must look like 'money_target:<key>:<YYYY-MM>'")
    return await _hide_saving_card(user, key, f"money_target:{ref}", action, args, now)


async def _hide_saving_card(user, key, item_id, action, args, now):
    """Hide a saving card; the saving's own status stays as it is."""
    store.require(user, money_config.ADDON)  # NotConnected → 403 not_connected
    if await asyncio.to_thread(money_savings.get_item, user, key, now.date()) is None:
        raise cards.ActError("Saving not found", 404)
    if action == "dismiss":
        msg = "Dismissed"
        return cards.result(msg, cards.hide_with_undo(user, item_id, cards.FAR, msg, reason="dismiss"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = _hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, item_id, until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a saving")


# --- Checks / review -------------------------------------------------------------------

async def _act_check(user, key, action, args, now):
    if key not in checks.keys_for(user):
        raise cards.ActError(f"unknown check '{key}'", 404)
    if action in ("ignore", "dismiss"):
        days = args.get("days", 7)
        try:
            days = float(days)
        except (TypeError, ValueError):
            raise cards.ActError("days must be a number")
        res = checks.ignore(user, key, days)
        return cards.result(res["message"], res["undo_id"], until=res["until"])
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = _hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"check:{key}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a check")


async def _act_review(user, rid, action, args, now):
    weekly_review.require(user)  # NotConnected → 403 not_connected
    r = weekly_review.get_review(user, rid)
    if not r:
        raise cards.ActError("Review not found", 404)
    if action == "apply":
        if r["status"] != "pending":
            raise cards.ActError(f"This review is already {r['status']}.", 409)
        decisions = None
        chosen = args.get("items")
        if isinstance(chosen, list):
            picked = {str(x) for x in chosen}
            decisions = [{"ref": i["ref"], "enabled": i["ref"] in picked} for i in r["items"]]
        try:
            res = await weekly_review.apply(user, rid, decisions)
        except (LookupError, ValueError) as e:
            raise cards.ActError(str(e))
        n = res["applied"]
        msg = f"Review applied · {n} change{'s' if n != 1 else ''}" + (f", {res['failed']} failed" if res["failed"] else "")
        return cards.result(msg, None, applied=n, failed=res["failed"])  # not undoable: many changes at once
    if action in ("skip", "dismiss"):
        if not weekly_review.dismiss(user, rid):
            raise cards.ActError(f"This review is already {r['status']}.", 409)

        def _undo():
            if not weekly_review.restore_pending(user, rid):
                raise RuntimeError("The review changed since, so it was left as it is.")
        return cards.result("Skipped this week", undo.record("Skipped the weekly review", _undo, source="now.skip"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = _hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"review:{rid}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for the weekly review")
