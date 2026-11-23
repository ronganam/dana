"""The Now screen: one short queue of things that need a decision.

``build()`` composes ``GET /api/now`` from plain DB reads, cached outside data
(calendar + weather via addon providers, never awaited for long) and each
active addon's ``@addon.now_cards`` / ``@addon.day_items`` hooks, so it is fast
and makes no model call. ``act()`` carries out ``POST /api/now/act`` — core's
own cards (``approval``, ``check``, ``review``, ``budget``) here, and every addon's card
through its ``@addon.now_action(<prefix>)`` — registering an undo
(``core/undo.py``) for every reversible action.

Cards without a snooze of their own (checks, review, suggestions) are hidden
until a time in ``now_hidden`` (per user: core/now_hidden.py). The contract is
in docs/now.md.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
from typing import Any, Dict, List, Optional

from core import approval_text, briefing, budget, cards, checks, clock, now_hidden, undo, users, wake, weekly_review, zones
from core.addons import DayItem, NowContext, registry
from core.addons.helpers import ago, fmt_date, parse_time, when_phrase
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

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

# Per user: weather cache.
_weather: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Formatting & home/wake availability
# ---------------------------------------------------------------------------

def home_zone() -> str:
    """The current user's Home zone name in lower case ("home" without one; core/zones.py)."""
    return zones.home_name(users.current())


def has_home(user: Optional[User]) -> bool:
    """Whether "at home" means something for ``user`` (they have a Home zone)."""
    return zones.home(user) is not None


def has_wake(user: Optional[User]) -> bool:
    """Whether "on waking" means something for ``user`` (the wake flow runs for them)."""
    return wake.available(user)


def is_home(loc: Any) -> bool:
    return isinstance(loc, dict) and str(loc.get("location") or "").lower() in (home_zone(), "home") \
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


def remind_label(triggers: Dict[str, Any], now: Optional[datetime.datetime] = None) -> str:
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


def set_message(triggers: Dict[str, Any], now: Optional[datetime.datetime] = None) -> str:
    phrase = trigger_phrase(triggers, now)
    return f"Reminder set · {phrase}" if phrase else "Added to reminders"


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
    """``user``'s events today via the ``events_today`` provider (None when unavailable)."""
    hook = registry.provider("events_today", user)
    if hook is None:
        return None
    return await hook.func(user, wait)


async def _fetch_weather(user: User, cache: Dict[str, Any]) -> None:
    try:
        cache["value"] = await briefing.weather(user)
    except Exception as e:
        logger.debug(f"Now: weather failed ({e})")
    finally:
        cache["at"] = time.monotonic()


def weather_cached(user: User) -> Optional[Dict[str, Any]]:
    """``user``'s cached weather (None without a ``weather`` provider)."""
    if registry.provider("weather", user) is None:
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
# Core items: approvals, checks, weekly review
# ---------------------------------------------------------------------------

def _approval_items(now: datetime.datetime, user_id: str) -> List[Dict[str, Any]]:
    """The chat of ``user_id`` waiting for an answer (approvals are in the conversation core)."""
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT session_id, pending_approval, title, updated_at FROM sessions "
                "WHERE user_id = ? AND pending_approval IS NOT NULL "
                "ORDER BY (session_id = 'main') DESC, updated_at DESC LIMIT 5", (user_id,)).fetchall()
    except Exception:
        return []
    for r in rows:
        at = parse_time(r["updated_at"])
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
            d = approval_text.describe(str(p.get("tool_name") or ""), p.get("tool_args"))
            title = cards.clip(d["title"], 160)
            label = "Review"
            blocks = []
            if d["body"]:
                blocks.append({"type": "text", "label": d["body_label"] or "Content", "text": cards.clip(d["body"], 600)})
            if d["facts"]:
                blocks.append({"type": "facts", "label": "Details",
                               "items": [cards.clip(f"{k}: {v}", 200) for k, v in d["facts"]]})
        return [{
            "id": f"approval:{sid}", "kind": "approval",
            "source": f"Chat · {r['title'] or 'Dana'}" + (f" · {ago(at, now)}" if at else ""),
            "title": title,
            **({"why": f"You asked: “{cards.clip(p['user_text'], 100)}”"} if p.get("user_text") else {}),
            "primary": cards.action("open", label, client=True),
            "secondary": [],
            "schedulable": False,
            "ref": {"type": "thread", "id": sid},
            "blocks": blocks,
        }]
    return []


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


def _hook_cards(user: User, ctx: NowContext) -> List[Dict[str, Any]]:
    """The cards of every ``@addon.now_cards`` of an addon ``user`` has, ordered by ``order=``."""
    out: List[Dict[str, Any]] = []
    for hook in sorted(registry.hooks("now_cards", user), key=lambda h: h.option("order", 50)):
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


# The fix kinds a client carries out itself, and the arg naming their target.
_CLIENT_FIXES = {"connect": "addon", "screen": "screen", "url": "url"}


def fix_action(fix: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A check's fix (``Fix.to_json()``) as a card's action: a server fix posts
    ``fix`` with ``args.fix`` = its action; the others are client actions
    (``connect`` {addon}, ``screen`` {screen}, ``url`` {url})."""
    if not fix:
        return None
    if fix.get("kind") == "server":
        return cards.action("fix", fix["label"], {"fix": fix["action"]})
    arg = _CLIENT_FIXES.get(str(fix.get("kind")))
    return cards.action(fix["kind"], fix["label"], {arg: fix[arg]}, client=True) if arg else None


def _check_items(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``user``'s failing checks (only theirs: core/checks.py ``for_user``):
    a Reconnect card for each connection that needs it (dismissible for a day),
    then the other failing checks, leaving out those of an addon that already
    has a Reconnect card (Settings → Needs attention lists them)."""
    reconnects: List[Dict[str, Any]] = []
    out: List[Dict[str, Any]] = []
    failing = [c for c in checks.for_user(user, now) if not c["ok"]]
    broken = {c["addon"] for c in failing if checks.is_reconnect(c["key"])}
    for c in failing:
        if f"check:{c['key']}" in hidden:
            continue
        fix = fix_action(c.get("fix"))
        if checks.is_reconnect(c["key"]):
            reconnects.append({
                "id": f"check:{c['key']}", "kind": "reconnect", "source": f"Reconnect · {c['label']}",
                "title": c["title"], "why": c["why"],
                "primary": fix,
                "secondary": [cards.action("ignore", "Not today", {"days": 1})],
                "schedulable": False,
                "ref": {"type": "check", "id": c["key"]},
                "blocks": [],
            })
            continue
        if c["addon"] in broken:
            continue
        out.append({
            "id": f"check:{c['key']}", "kind": "check", "source": "Dana check",
            "title": c.get("title") or f"{c['label']}: {c['detail']}",
            "why": c.get("why") or c["detail"],
            "primary": fix or cards.action("open", "Details", client=True),
            "secondary": ([cards.action("open", "Details", client=True)] if fix else [])
                         + [cards.action("ignore", "Ignore for a week", {"days": 7})],
            "schedulable": True,
            "ref": {"type": "check", "id": c["key"]},
            "blocks": c.get("blocks") or [{"type": "text", "label": c["label"], "text": c["detail"]}],
        })
    return reconnects[:3] + out[:3]


def _budget_items(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The monthly budget's card, for whoever may manage the server, from 80 %
    of the limit until it is spent (then the ``budget`` check takes over);
    dismissed, it stays away for the rest of the month (``budget:<YYYY-MM>``)."""
    if not user.level.allows("manage_server"):
        return []
    st = budget.state(now.date())
    item_id = f"budget:{st['month']}"
    if st["state"] != budget.WARN or item_id in hidden:
        return []
    share = round(st["spent"] / st["limit"] * 100)
    return [{
        "id": item_id, "kind": "budget", "source": "Usage",
        "title": f"{share}% of this month's model budget used",
        "why": (f"${st['spent']:,.2f} of ${st['limit']:,.2f}, on course for ${st['forecast']:,.2f}. At 100% "
                "background work (triage, briefs, learning, summaries) pauses until the month ends; chat and "
                "voice keep working."),
        "primary": cards.action("open", "Details", client=True),
        "secondary": [cards.action("dismiss", "Dismiss for this month")],
        "ref": {"type": "usage", "id": st["month"]},
        "blocks": [{"type": "facts", "label": "This month",
                    "items": [f"Spent ${st['spent']:,.2f}", f"Budget ${st['limit']:,.2f}",
                              f"Forecast ${st['forecast']:,.2f}"]}],
    }]


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
    r = weekly_review.latest(user)
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
    mlines = weekly_review.money_lines(r.get("money"))
    if mlines:
        blocks.append({"type": "facts", "label": "Money", "items": mlines})
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
    ``DAY_LAST_HOUR`` (23:00)."""
    first = max(now.hour + 1, DAY_FIRST_HOUR)
    return list(range(first, DAY_LAST_HOUR + 1))


def day_slots(user: User, now: datetime.datetime, events: Optional[List[Dict[str, Any]]],
              ctx: Optional[NowContext] = None) -> List[Dict[str, Any]]:
    """The day strip of ``user``: hour slots, then "At home" and "On waking"."""
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
    return [slots[str(h)] for h in hours] + ([home] if has_home(user) else []) + ([waking] if has_wake(user) else [])


# --- Lede ------------------------------------------------------------------------

def _fresh_briefing(user: User, now: datetime.datetime) -> Optional[str]:
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
    n = len(items or [])
    head = _fresh_briefing(user, now) if n else None
    if head:
        return head + "."
    parts = []
    upcoming = [(st, e) for e in events or [] for st in [_event_start(e)] if st and st >= now and st.date() == now.date()]
    if upcoming:
        st, e = min(upcoming, key=lambda x: x[0])
        parts.append(f"{cards.clip(e.get('title'), 60)} at {st.strftime('%H:%M')}")
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
    with users.acting_as(user):
        now = clock.now()
        events = await calendar_today(user)
        weather = weather_cached(user)
        hidden = await asyncio.to_thread(now_hidden.active, user)
        ctx = NowContext(now=now, hidden=hidden, events=events)

        def compose() -> Dict[str, Any]:
            items = [i for i in _approval_items(now, user.id) if i["id"] not in hidden]
            items += _hook_cards(user, ctx)
            items += _budget_items(user, now, hidden)
            items += _check_items(user, now, hidden)
            items += _review_items(user, hidden)
            quiet_hook = registry.provider("now_quiet", user)
            quiet, quiet_total = quiet_hook.func(user, now, hidden) if quiet_hook is not None else ([], 0)
            counts_hook = registry.provider("notification_counts", user)
            counts = counts_hook.func(user) if counts_hook is not None else {"total": 0, "noise": 0, "needs_you": 0}
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
    """A "home" location from a client (the choices offer it) → the name of the user's Home zone."""
    loc = triggers.get("location")
    if isinstance(loc, dict) and str(loc.get("location") or "").lower() in ("home", home_zone()):
        triggers["location"] = {"location": zones.home_name(user), "event": loc.get("event") or "arrive"}
    return triggers


async def slot_trigger(user: User, slot: str, now: datetime.datetime) -> Dict[str, Any]:
    if slot == "home":
        return {"location": {"location": zones.home_name(user), "event": "arrive"}}
    if slot == "wake":
        return {"wake": True}
    return {"time": cards.slot_time(slot, now).strftime("%Y-%m-%d %H:%M")}


def hide_until_for_slot(slot: str, now: datetime.datetime) -> datetime.datetime:
    """Cards dropped on home or wake: no exact time, so hide until a sensible one."""
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
        handler = {"check": _act_check, "review": _act_review, "approval": _act_approval,
                   "budget": _act_budget}.get(kind)
        if handler:
            return await handler(user, ref, action, args, clock.now())
        hook = registry.hook_for("now_action", kind, user)  # NotConnected → 403 not_connected
        if hook is None:
            raise cards.ActError(f"unknown kind '{kind}'")
        return await hook.func(user, ref, action, args, clock.now())


async def _act_approval(user, ref, action, args, now):
    raise cards.ActError("Approvals are answered in the conversation; open the thread.")


async def _act_check(user, key, action, args, now):
    if key not in checks.keys_for(user):
        raise cards.ActError(f"unknown check '{key}'", 404)
    if action == "fix":
        try:
            res = await checks.run_fix(user, key, str(args.get("fix") or ""))
        except LookupError as e:
            raise cards.ActError(str(e), 404)
        except ValueError as e:
            raise cards.ActError(str(e), 409)
        return cards.result(res["message"])
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
        until = hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        if checks.is_reconnect(key):
            until = min(until, now + datetime.timedelta(days=checks.RECONNECT_IGNORE_DAYS))
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"check:{key}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a check")


async def _act_budget(user, month, action, args, now):
    if not user.level.allows("manage_server"):
        raise cards.ActError("unknown kind 'budget'")
    if action != "dismiss":
        raise cards.ActError(f"'{action}' isn't an action for the budget card")
    first = now.date().replace(day=1)
    if month != first.strftime("%Y-%m"):
        raise cards.ActError("That month is over", 409)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    until = datetime.datetime.combine(nxt, datetime.time(0, 0), clock.get_timezone())
    msg = "Budget card dismissed for this month"
    return cards.result(msg, cards.hide_with_undo(user, f"budget:{month}", until, msg, reason="dismiss"))


async def _act_review(user, rid, action, args, now):
    weekly_review.require(user)
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
        return cards.result(msg, None, applied=n, failed=res["failed"])
    if action in ("skip", "dismiss"):
        if not weekly_review.dismiss(user, rid):
            raise cards.ActError(f"This review is already {r['status']}.", 409)

        def _undo():
            if not weekly_review.restore_pending(user, rid):
                raise RuntimeError("The review changed since, so it was left as it is.")
        return cards.result("Skipped this week", undo.record("Skipped the weekly review", _undo, source="now.skip"))
    if action in ("snooze", "schedule"):
        slot = str(args.get("slot") or "")
        until = hide_until_for_slot(slot, now) if slot else cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Hidden until {when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"review:{rid}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for the weekly review")
