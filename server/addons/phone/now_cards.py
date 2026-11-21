"""The phone addon's Now cards and actions: needs-you notifications, pending call/notification
suggestions, the quiet list and notification counts (docs/now.md)."""
from __future__ import annotations

import asyncio
import datetime
import difflib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core import cards, clock, now as now_screen, now_hidden, undo
from core.addons import Card, NowContext, helpers, store
from core.db import get_db_connection
from core.users import User
from addons.calendar import config as calendar_config, tools as calendar_tools
from addons.phone import suggestions as phone_suggestions
from addons.phone.addon import addon
from addons.phone.calls import actions as suggestion_actions
from addons.phone.calls.store import parse_call_time
from addons.phone.notifications import (format as notif_format, overview as notification_overview,
                                        shade as notification_shade, store as notification_store)

MAX_NOTIFICATIONS = int(os.getenv("NOW_MAX_NOTIFICATIONS", "3"))
MAX_SUGGESTIONS = int(os.getenv("NOW_MAX_SUGGESTIONS", "4"))
MAX_QUIET = 20
QUIET_CONFIDENCE = float(os.getenv("NOW_QUIET_CONFIDENCE", "0.7"))
_SUGGESTION_OVERRIDES = ("title", "triggers", "due_date", "priority", "group", "description",
                         "start", "end", "location")


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", re.sub(r"\s+", " ", s or "")).strip().lower()


def _sentences(transcript: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+|\n+", transcript or "")
    return [p.strip() for p in parts if p and p.strip()]


def transcript_lines(transcript: str, evidence: str, around: int = 2) -> Optional[List[Dict[str, Any]]]:
    """A few transcript lines around the one the evidence quotes (``hit``), or None."""
    sents = _sentences(transcript)
    ev = _norm(evidence)
    if not sents or len(ev) < 4:
        return None
    normed = [_norm(s) for s in sents]
    hits = [i for i, s in enumerate(normed) if s and (s in ev or ev in s)]
    if not hits:
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


# --- Notification cards ---------------------------------------------------------------------

@addon.now_cards(order=20)
def notification_cards(user: User, ctx: NowContext) -> List[Card]:
    now = ctx.now
    out: List[Card] = []
    with get_db_connection() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND status = 'active' AND bucket = 'needs_you' "
            "AND (snoozed_until IS NULL OR snoozed_until <= ?) ORDER BY posted_at DESC LIMIT ?",
            (user.id, now_hidden.utc_iso(), MAX_NOTIFICATIONS * 6)).fetchall()]
        seen = set()
        for n in rows:
            if len(out) >= MAX_NOTIFICATIONS:
                break
            api = notification_store.to_api(n)
            rule, subject = notif_format.rule_subject(conn, n)
            if rule == "mute":
                continue
            posted = cards.parse_local(n.get("posted_at"))
            parts = notif_format.notification_parts(n)
            same = (n["package"], _norm(parts["headline"]))
            if same in seen:
                continue
            seen.add(same)
            chatty = bool(n.get("sender") or n.get("conversation"))
            title = cards.clip(parts["headline"], 140)
            can_reply = api["on_phone"] and any(a.get("reply") for a in api["actions"])
            blocks = []
            chat = notif_format.chat_block(conn, n) if chatty else None
            if chat:
                blocks.append(chat)
            elif parts["rest"]:
                blocks.append({"type": "text", "label": parts["who"] or n.get("app_name") or "Notification",
                               "text": cards.clip(parts["rest"], 400)})
            primary = cards.action("reply", "Reply", client=True) if can_reply else cards.action("open", "Open", client=True)
            why = notif_format.notification_why(n, rule, subject)
            out.append(Card(
                id=f"notif:{n['id']}", kind="notification",
                source=" · ".join(x for x in (n.get("app_name"), parts["who"], helpers.ago(posted, now)) if x),
                title=title, why=why, primary=primary,
                secondary=[cards.action("snooze", "In 1 h", {"minutes": 60}), cards.action("dismiss", "Dismiss")],
                schedulable=True, ref={"type": "notification", "id": n["id"]}, blocks=blocks,
                extra={"notification": {"id": n["id"], "key": n.get("device_key"), "package": n["package"],
                                        "app": n.get("app_name"), "can_reply": can_reply, "on_phone": api["on_phone"],
                                        "actions": api["actions"]}},
            ))
    return out


# --- Suggestion cards & quiet list ----------------------------------------------------------

def _choice_label(triggers: Dict[str, Any], now: datetime.datetime) -> str:
    phrase = now_screen.trigger_phrase(triggers, now)
    if triggers.get("time"):
        return phrase[:1].upper() + phrase[1:]
    if triggers.get("car"):
        return "In the car" if triggers["car"] != "leave" else "Getting out of the car"
    return phrase.replace("when you get", "When I get").replace("when you", "When I")


def suggestion_choices(user: User, triggers: Dict[str, Any], now: datetime.datetime) -> List[Dict[str, Any]]:
    tomorrow9 = (now + datetime.timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    options: List[Tuple[str, Dict[str, Any]]] = []
    if triggers:
        options.append((_choice_label(triggers, now), triggers))
    candidates = [("Tomorrow 09:00", {"time": tomorrow9.strftime("%Y-%m-%d %H:%M")})]
    if now_screen.has_home(user):
        candidates.append(("When I get home", {"location": {"location": now_screen.home_zone(), "event": "arrive"}}))
    if now_screen.has_wake(user):
        candidates.append(("When I wake", {"wake": True}))
    for label, trig in candidates:
        if not any(json.dumps(trig, sort_keys=True) == json.dumps(t, sort_keys=True) for _, t in options):
            options.append((label, trig))
    if not triggers:
        options.insert(0, ("No reminder", {}))
    out = []
    for i, (label, trig) in enumerate(options[:4]):
        out.append({"label": label, "value": trig, "selected": i == 0,
                    "action_label": now_screen.remind_label(trig, now),
                    "message": now_screen.set_message(trig, now)})
    return out


def _call_source(s: Dict[str, Any], now: datetime.datetime) -> str:
    c = s.get("call") or {}
    when = parse_call_time(c.get("call_timestamp") or "") if c.get("call_timestamp") else None
    return " · ".join(x for x in ("Call", c.get("contact"), helpers.ago(when, now)) if x)


def _notif_source(s: Dict[str, Any], now: datetime.datetime) -> str:
    n = s.get("notification") or {}
    who = notif_format.notification_parts(n)["who"] if n else ""
    return " · ".join(x for x in (n.get("app_name") or "Notification", who,
                                   helpers.ago(cards.parse_local(n.get("posted_at")), now)) if x)


def _suggestion_card(user: User, s: Dict[str, Any], now: datetime.datetime, transcript: Optional[str]) -> Card:
    p = s["payload"] or {}
    c = s.get("call") or {}
    why_parts = []
    if s["kind"] == "event":
        start = cards.parse_local(p.get("start"))
        why_parts.append(f"Event {helpers.when_phrase(start, now)}" if start else "Event")
    if p.get("due_date"):
        why_parts.append(f"Due {helpers.fmt_date(p['due_date'])}")
    if c.get("title"):
        why_parts.append(cards.clip(c["title"], 80))
    why = " · ".join(why_parts) or "From what was said."
    blocks: List[Dict[str, Any]] = []
    if s["kind"] == "alert":
        primary = cards.action("dismiss", "Got it")
        secondary: List[Dict[str, Any]] = []
        why = cards.clip(p.get("text") or why, 200)
    elif s["kind"] == "event":
        start = cards.parse_local(p.get("start"))
        primary = cards.action("accept", f"Add to calendar · {helpers.when_phrase(start, now)}" if start else "Add to calendar")
        secondary = [cards.action("dismiss", "Dismiss")]
    else:
        trig = p.get("triggers") or {}
        primary = cards.action("accept", now_screen.remind_label(trig, now), {"triggers": trig})
        secondary = [cards.action("dismiss", "Dismiss")]
        blocks.append({"type": "choices", "label": "Remind me", "key": "triggers",
                       "options": suggestion_choices(user, trig, now)})
    lines = transcript_lines(transcript or "", p.get("evidence") or "") if transcript else None
    if lines:
        when = parse_call_time(c.get("call_timestamp") or "") if c.get("call_timestamp") else None
        label = "The call" + (f" · {helpers.when_phrase(when, now)}" if when else "")
        blocks.insert(0, {"type": "lines", "label": label, "lines": lines})
    elif c.get("summary"):
        blocks.insert(0, {"type": "text", "label": "From the call", "text": cards.clip(c["summary"], 600)})
    elif p.get("evidence"):
        blocks.insert(0, {"type": "text", "label": "What was said", "text": f"“{cards.clip(p['evidence'], 400)}”"})
    return Card(
        id=f"sug:{s['id']}", kind="suggestion",
        source=_call_source(s, now) if s["source_type"] == "call" else _notif_source(s, now),
        title=cards.clip(p.get("title") or "Suggestion", 160), why=why,
        primary=primary, secondary=secondary, schedulable=s["kind"] == "task",
        ref={"type": "call" if s["source_type"] == "call" else "notification", "id": s["source_id"]},
        blocks=blocks,
    )


def _quiet_entry(s: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    p = s["payload"] or {}
    return {"id": f"sug:{s['id']}", "kind": "suggestion", "title": cards.clip(p.get("title") or "Suggestion", 140),
            "source": _call_source(s, now) if s["source_type"] == "call" else _notif_source(s, now),
            "primary": cards.action("accept", "Add"), "secondary": [cards.action("dismiss", "Dismiss")],
            "ref": {"type": "call" if s["source_type"] == "call" else "notification", "id": s["source_id"]}}


def _partition_suggestions(user: User, hidden: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
    return loud, quiet


@addon.now_cards(order=30)
def suggestion_cards(user: User, ctx: NowContext) -> List[Card]:
    loud, _ = _partition_suggestions(user, ctx.hidden)
    transcripts: Dict[str, str] = {}
    ids = [s["source_id"] for s in loud if s["source_type"] == "call"]
    if ids:
        with get_db_connection() as conn:
            marks = ",".join("?" * len(ids))
            transcripts = {r["id"]: r["transcript"] or "" for r in conn.execute(
                f"SELECT id, transcript FROM phone_calls WHERE id IN ({marks}) AND user_id = ?",
                (*ids, user.id)).fetchall()}
    return [_suggestion_card(user, s, ctx.now, transcripts.get(s["source_id"])) for s in loud]


@addon.provide("now_quiet")
def provide_quiet(user: User, now: datetime.datetime, hidden: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    _, quiet = _partition_suggestions(user, hidden)
    return [_quiet_entry(s, now) for s in quiet[:MAX_QUIET]], len(quiet)


@addon.provide("notification_counts")
def provide_notification_counts(user: User) -> Dict[str, int]:
    c = notification_overview.counts(user)
    return {"total": c["total"], "noise": c["noise"], "needs_you": c["needs_you"]}


# --- Acting on suggestions & notifications --------------------------------------------------

def _restore_suggestion(sid: str, payload: Dict[str, Any], expect: str) -> None:
    with get_db_connection() as conn:
        # scoped: undo of the user's own accept / dismiss; sid came from get_suggestion(user)
        row = conn.execute("SELECT status FROM call_suggestions WHERE id = ?", (sid,)).fetchone()
        if not row:
            raise RuntimeError("The suggestion no longer exists.")
        if row["status"] != expect:
            raise RuntimeError("The suggestion changed since, so it was left as it is.")
        # scoped: undo of the user's own accept / dismiss; sid came from get_suggestion(user)
        conn.execute("UPDATE call_suggestions SET status = 'pending', result_ref = NULL, resolved_at = NULL, "
                     "payload = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), sid))
        conn.commit()


async def _accept_suggestion(user: User, sid: str, s: Dict[str, Any], overrides: Dict[str, Any],
                             now: datetime.datetime) -> Dict[str, Any]:
    if isinstance(overrides.get("triggers"), dict):
        overrides["triggers"] = await now_screen.fix_home(user, dict(overrides["triggers"]))
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
        msg = now_screen.set_message(triggers, now)

        def _undo():
            suggestion_actions.undo_created_task(user, task_id)
            _restore_suggestion(sid, original, "accepted")
        return cards.result(msg, undo.record(msg, _undo, source="now.accept"), task_id=task_id)
    if res.get("kind") == "event":
        start = cards.parse_local((overrides.get("start") or original.get("start")))
        msg = "Added to calendar" + (f" · {helpers.when_phrase(start, now)}" if start else "")
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
    return cards.result("Dismissed", undo.record("Dismissed", lambda: _restore_suggestion(sid, original, "dismissed"),
                                                 source="now.accept"))


@addon.now_action("sug")
async def act_suggestion(user: User, sid: str, action: str, args: Dict[str, Any],
                         now: datetime.datetime) -> Dict[str, Any]:
    s = phone_suggestions.get_suggestion(user, sid)
    if not s:
        raise cards.ActError("Suggestion not found", 404)
    if s["status"] != "pending":
        raise cards.ActError(f"This suggestion is already {s['status']}.", 409)
    if action == "accept":
        overrides = {k: v for k, v in args.items() if k in _SUGGESTION_OVERRIDES}
        if args.get("slot"):
            overrides["triggers"] = await now_screen.slot_trigger(user, str(args["slot"]), now)
        return await _accept_suggestion(user, sid, s, overrides, now)
    if action == "schedule":
        slot = str(args.get("slot") or "")
        if not slot:
            raise cards.ActError("schedule needs args.slot")
        if s["kind"] != "task":
            raise cards.ActError("Only reminders can be placed on the day")
        return await _accept_suggestion(user, sid, s, {"triggers": await now_screen.slot_trigger(user, slot, now)}, now)
    if action == "dismiss":
        suggestion_actions.dismiss_suggestion(user, sid)
        payload = dict(s["payload"] or {})
        return cards.result("Dismissed", undo.record(
            "Dismissed suggestion", lambda: _restore_suggestion(sid, payload, "dismissed"), source="now.dismiss"))
    if action == "snooze":
        until = cards.until_from(args, now)
        if not until:
            raise cards.ActError("snooze needs minutes, until or slot")
        msg = f"Snoozed · back {helpers.when_phrase(until, now)}"
        return cards.result(msg, cards.hide_with_undo(user, f"sug:{sid}", until, msg))
    raise cards.ActError(f"'{action}' isn't an action for a suggestion")


@addon.now_action("quiet")
async def act_quiet(user: User, ref: str, action: str, args: Dict[str, Any],
                    now: datetime.datetime) -> Dict[str, Any]:
    if ref != "all" or action != "dismiss":
        raise cards.ActError("quiet:all only supports dismiss")
    quiet, _ = await asyncio.to_thread(provide_quiet, user, clock.now(), now_hidden.active(user))
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


@addon.now_action("notif")
async def act_notification(user: User, nid: str, action: str, args: Dict[str, Any],
                           now: datetime.datetime) -> Dict[str, Any]:
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
        msg = f"Snoozed · back {helpers.when_phrase(until, now)}"
        return cards.result(msg, undo.record(msg, lambda: notification_shade.restore(user, snap), source="now.snooze"),
                            device_keys=res["device_keys"])
    if action == "schedule":
        slot = str(args.get("slot") or "")
        if slot not in ("home", "wake"):
            raise cards.ActError("schedule needs args.slot: an hour, 'home' or 'wake'")
        trig = await now_screen.slot_trigger(user, slot, now)
        who = n.get("sender") or n.get("title") or n.get("app_name")
        title = f"Reply to {who}" if (n.get("sender") or n.get("conversation")) else \
            cards.clip(notif_format.notification_parts(n)["headline"], 160)
        desc = f"From a {n.get('app_name')} notification:\n“{cards.clip(n.get('text'), 500)}”"
        try:
            task_id = await suggestion_actions.create_task_record(
                user, {"title": title, "description": desc, "triggers": trig}, link=("notification", nid))
        except ValueError as e:
            raise cards.ActError(str(e))
        snap = notification_shade.snapshot(user, [nid])
        notification_shade.set_status(user, [nid], "archived", reason="reminder")
        msg = now_screen.set_message(trig, now)

        def _undo():
            suggestion_actions.undo_created_task(user, task_id)
            notification_shade.restore(user, snap)
        return cards.result(msg, undo.record(msg, _undo, source="now.schedule"), task_id=task_id)
    raise cards.ActError(f"'{action}' isn't an action for a notification")
