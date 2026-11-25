"""The phone addon's background work, push channel and composer hooks (../docs/addons.md, "The manifest").

- at start: finish call processing and notification triage a restart interrupted;
- every 6 hours: drop call audio older than ``CALL_AUDIO_RETENTION_DAYS``
  (transcripts are kept) and notifications older than 30 days;
- every minute: the notification digest at ``NOTIFICATION_DIGEST_TIMES``;
- every 5 minutes: try again the notifications the triage judge did not answer;
- every 30 seconds: re-nudge unseen alerts;
- the ``push`` channel of ``core.notify.send``: a message's ``kind`` and
  ``data`` as a push event to the user's phone (addons/phone/push.py);
- context chats for ``suggestion``, ``call`` and ``notification``, plus the
  ``call`` context block for tasks linked to a call;
- global search over the user's calls and active notifications;
- self-checks: ``push``, ``notification_sync``, ``triage``, ``calls``;
- signals (docs/signals.md): the ``phone`` source (rank 10, before Home
  Assistant) of ``wake``, ``screen``, ``car``, ``location`` and ``sleep``,
  and the ``screen_sessions`` provider (addons/phone/signals.py);
- ``core.zones.changed``: the ``zones_changed`` event to the user's phone,
  when one is registered for push (it registers its geofences again).
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

from core import cards, clock, devices, users
from core.addons import BriefingContext, Check, ContextRequest, Fix, Hit, Section, helpers, hours, minutes
from core.db import get_db_connection
from core.notify import Message
from core.users import User
from addons.phone import now_cards as _now_cards, push, signals as phone_signals, suggestions as phone_suggestions  # noqa: F401
from addons.phone.addon import addon
from addons.phone.calls import (actions as suggestion_actions, chat as phone_chat,
                                processing as calls_processing, retention as calls_retention)
from addons.phone.config import TOKEN_FETCH, mark_key
from addons.phone.notifications import (digest, escalation, format as notif_format, judges,
                                        processing as notification_processing, retention as notification_retention,
                                        store as notification_store, summary as notification_summary)

logger = logging.getLogger(__name__)
SUGGESTION_DAYS = int(os.getenv("REVIEW_SUGGESTION_DAYS", "7"))

PUSH_FETCH_HOURS = float(os.getenv("CHECK_PUSH_FETCH_HOURS", "24"))
NOTIFICATION_SYNC_HOURS = float(os.getenv("CHECK_NOTIFICATION_SYNC_HOURS", "12"))
CALLS_DAYS = float(os.getenv("CHECK_CALLS_DAYS", "7"))


# --- Background jobs & push channel ---------------------------------------------------------

@addon.job(at_start=True)
async def resume_unfinished(user: User) -> None:
    """Finish ``user``'s call processing and notification triage interrupted by a restart."""
    calls_processing.resume_unfinished(user)
    notification_processing.resume_unfinished(user)


@addon.job(every=minutes(5))
async def retry_triage(user: User) -> int:
    """Queue ``user``'s notifications still waiting for a triage answer."""
    return notification_processing.retry_pending(user)


@addon.job(every=hours(6))
async def purge_old_audio(user: User) -> int:
    return await asyncio.to_thread(calls_retention.purge_old_audio, user)


@addon.job(every=hours(6))
async def purge_old_notifications(user: User) -> int:
    return await asyncio.to_thread(notification_retention.purge_old, user)


@addon.job(every=minutes(1))
async def send_digest(user: User) -> int:
    return await digest.digest_due(user)


@addon.job(every=datetime.timedelta(seconds=30))
async def renudge(user: User) -> int:
    return await escalation.renudge_due(user)


# --- Signals --------------------------------------------------------------------------------

PHONE_SOURCE = {"rank": 10, "source": phone_signals.SOURCE, "label": phone_signals.LABEL}


@addon.provide("screen_sessions", **PHONE_SOURCE)
def screen_sessions(user: User, since_utc: datetime.datetime, until_utc: datetime.datetime) -> Optional[List[float]]:
    """Seconds of each of ``user``'s phone screen sessions in [since, until] (None while the phone sends none)."""
    return phone_signals.screen_seconds(user, since_utc, until_utc)


def _phone_source(user: User) -> str:
    return phone_signals.missing(user)


for _signal in ("wake", "screen", "car", "location", "sleep"):
    addon.provide(f"signal.{_signal}", **PHONE_SOURCE)(_phone_source)


@addon.on("core.zones.changed")
async def zones_changed(user: User, data: Dict[str, Any]) -> None:
    """``user``'s zones changed: their phone re-registers its geofences (none
    registered for push: nothing; a phone reads the zones when it starts)."""
    if not push.devices(user):
        return
    await push.send(user, "zones_changed", {"changed_at": clock.now().isoformat(timespec="seconds")}, replace=True)


@addon.channel("push")
async def push_channel(user: User, message: Message) -> bool:
    """A message with a ``kind`` goes to ``user``'s phone as that push event."""
    if not message.kind:
        return False
    if message.kind == "notify":
        d = message.data
        return await push.notify(user, str(d.get("title") or ""), str(d.get("text") or ""),
                                 tag=str(d.get("tag") or ""), channel=str(d.get("channel") or "messages"))
    return await push.send(user, message.kind, dict(message.data))


# --- Briefing & weekly review ---------------------------------------------------------------

@addon.briefing("inbox", counts=("needs_you",), default={"needs_you": 0, "active": 0, "points": []}, rank=40)
def briefing_inbox(user: User, ctx: BriefingContext) -> Section:
    try:
        c = notification_store.counts(user)
        s = notification_summary.get_summaries(user, refresh=False)
        overall = (s.get("overall") or {}).get("points") or []
        data = {"needs_you": c.get("needs_you", 0), "active": c.get("active", 0), "points": overall[:3]}
    except Exception as e:
        logger.debug(f"Briefing inbox failed: {e}")
        data = {"needs_you": 0, "active": 0, "points": []}
    pts = [f"{data['needs_you']} notification(s) need you"] if data["needs_you"] else []
    return Section(data, points=pts, counts={"needs_you": data["needs_you"]})


@addon.briefing("suggestions", default={"count": 0, "top": []}, rank=45)
def briefing_suggestions(user: User, ctx: BriefingContext) -> Section:
    with get_db_connection() as conn:
        rows = conn.execute("SELECT payload FROM call_suggestions WHERE status = 'pending' AND user_id = ? "
                            "ORDER BY created_at DESC", (user.id,)).fetchall()
    titles = []
    for r in rows[:3]:
        try:
            titles.append(json.loads(r["payload"] or "{}").get("title") or "")
        except Exception:
            pass
    data = {"count": len(rows), "top": [t for t in titles if t]}
    pts = [f"{data['count']} suggestion(s) waiting"] if data["count"] else []
    return Section(data, points=pts)


@addon.review("phone")
def review_suggestions(user: User, ctx: BriefingContext) -> Section:
    vis, vis_args = phone_suggestions.visible_sql(user)
    with get_db_connection() as conn:
        rows = conn.execute(
            f"SELECT s.id, s.kind, s.payload, s.created_at FROM call_suggestions s WHERE s.status = 'pending' AND {vis} "
            "ORDER BY s.created_at", vis_args).fetchall()
    items = []
    for s in rows:
        age = helpers.age_days(s["created_at"], ctx.now)
        if age is not None and age >= SUGGESTION_DAYS:
            p = cards.loads(s["payload"], {})
            items.append({"ref": s["id"], "type": "suggestion", "title": p.get("title") or "Suggestion",
                          "why": "old_suggestion", "facts": [f"pending {age} days", f"a {s['kind']}"],
                          "due_date": p.get("due_date")})
    return Section(items=items)


@addon.review_action("suggestion", ("accept", "dismiss", "keep"), primary=False)
async def apply_review_suggestion(user: User, item: Dict[str, Any], action: str, decision: Dict[str, Any],
                                  today: datetime.date) -> str:
    ref = item["ref"]
    if action == "accept":
        res = await suggestion_actions.accept_suggestion(user, ref)
        return str(res.get("result_ref") or "accepted")
    if not suggestion_actions.dismiss_suggestion(user, ref):
        raise LookupError("suggestion is no longer pending")
    return "dismissed"


# --- Context chats --------------------------------------------------------------------------

@addon.context_card("suggestion")
def suggestion_card(user: User, ref_id: str) -> Dict[str, Any]:
    return phone_chat.suggestion_card(user, ref_id)


@addon.context("suggestion")
def suggestion_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return phone_chat.suggestion_prompt(user, ref_id)


@addon.context_card("call")
def call_card(user: User, ref_id: str) -> Dict[str, Any]:
    return phone_chat.call_card(user, ref_id)


@addon.context("call")
def call_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return phone_chat.call_prompt(user, ref_id)


@addon.context_block("call")
def call_context_block(user: User, ref_id: str) -> str:
    _, block = phone_chat.call_block(user, ref_id)
    return block or ""


@addon.context_card("notification")
def notification_card(user: User, ref_id: str) -> Dict[str, Any]:
    return phone_chat.notification_card(user, ref_id)


@addon.context("notification")
def notification_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    return phone_chat.notification_prompt(user, ref_id)


# --- Search ---------------------------------------------------------------------------------

def _local_ago(value: Any) -> str:
    dt = helpers.parse_time(value)
    return helpers.ago(dt, clock.now()) if dt else ""


def _search_calls(user: User, q: str, limit: int) -> List[Hit]:
    uid = user.id
    found: Dict[str, float] = {}
    fts = helpers.fts_query(q)
    with get_db_connection() as conn:
        if fts:
            try:
                for r in conn.execute("SELECT f.id FROM phone_calls_fts f JOIN phone_calls c ON c.id = f.id "
                                      "WHERE phone_calls_fts MATCH ? AND c.user_id = ? ORDER BY rank LIMIT 30",
                                      (fts, uid)).fetchall():
                    found.setdefault(r["id"], 0.5)
            except Exception:
                pass
        like = helpers.like_pattern(q)
        for r in conn.execute("SELECT id FROM phone_calls WHERE user_id = ? AND (caller LIKE ? ESCAPE '\\' "
                              "OR receiver LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\') "
                              "LIMIT 30", (uid, like, like, like, like)).fetchall():
            found.setdefault(r["id"], 0.5)
        if not found:
            return []
        ids = list(found)
        marks = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT id, caller, receiver, title, call_timestamp, duration_seconds FROM phone_calls "
                            f"WHERE id IN ({marks}) AND user_id = ?", (*ids, uid)).fetchall()
    scored = []
    for r in rows:
        contact = r["receiver"] if r["caller"] == "Me" else r["caller"]
        score = max(helpers.match_score(contact or "", q), helpers.match_score(r["title"] or "", q))
        if score <= 0.5 and q.casefold() not in f"{contact} {r['title']}".casefold():
            score = 0.5
        mins = int(r["duration_seconds"] or 0) // 60
        sub = " · ".join(x for x in (contact, _local_ago(r["call_timestamp"]), f"{mins} min" if mins else "") if x)
        scored.append((score, r["call_timestamp"] or "",
                       Hit("call", r["id"], r["title"] or f"Call with {contact}", sub, score=score)))
    scored.sort(key=lambda x: x[1], reverse=True)
    scored.sort(key=lambda x: -x[0])
    return [h for _, _, h in scored[:limit]]


def _search_notifications(user: User, q: str, limit: int) -> List[Hit]:
    out: List[Hit] = []
    for n in notification_store.list_notifications(user, status="active", q=q, limit=30, with_suggestions=False):
        parts = notif_format.notification_parts(n)
        title = " ".join((parts["headline"] or "").split())[:140]
        score = max(helpers.match_score(title, q) if q.casefold() in title.casefold() else 0.5,
                    helpers.match_score(parts["who"], q) if parts["who"] and q.casefold() in parts["who"].casefold() else 0.5)
        sub = " · ".join(x for x in (n.get("app_name"), parts["who"], _local_ago(n.get("posted_at"))) if x)
        out.append(Hit("notification", n["id"], title, sub, score=score, extra={"package": n["package"]}))
    return sorted(out, key=lambda h: -h.score)[:limit]


@addon.search
def search_phone(user: User, q: str, limit: int) -> List[Hit]:
    out: List[Hit] = []
    try:
        out += _search_calls(user, q, limit)
    except Exception:
        pass
    try:
        out += _search_notifications(user, q, limit)
    except Exception:
        pass
    return out


# --- Self-checks ----------------------------------------------------------------------------

def _latest(*values: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    vals = [v for v in values if v]
    return max(vals) if vals else None


def _ago_or_never(dt: Optional[datetime.datetime], now: datetime.datetime) -> str:
    return helpers.ago(dt, now) if dt else "never"


def _old_token_fetch(conn, user: User):
    """``(when, label, last used)`` of the user's newest events fetch made with
    a token of no device (the ``token_fetch`` mark, api.py), or None: no such
    fetch, or the token is gone (revoked) or is now a device's."""
    m = conn.execute("SELECT last_ok_at, detail FROM system_marks WHERE key = ?",
                     (mark_key(TOKEN_FETCH, user.id),)).fetchone()
    at = helpers.parse_time(m["last_ok_at"]) if m else None
    if not at or not m["detail"]:
        return None
    tok = next((t for t in users.list_tokens(user.id) if t.id == m["detail"]), None)
    if tok is None or devices.by_token_id(tok.id) is not None:
        return None
    return at, tok.label, helpers.parse_time(tok.last_used_at)


@addon.selfcheck("push", "Push to the phone")
def check_push(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        devs = [dict(r) for r in conn.execute(
            "SELECT p.device_id, d.name, p.transport, p.address, p.app_version, p.updated_at, p.last_fetch_at "
            "FROM phone_push p "
            "JOIN devices d ON d.id = p.device_id WHERE p.user_id = ?", (user.id,)).fetchall()]
        m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                              (mark_key("push", user.id),)).fetchone() or {})
        old_token = _old_token_fetch(conn, user)
    last_ok, last_fail = helpers.parse_time(m.get("last_ok_at")), helpers.parse_time(m.get("last_fail_at"))
    fetch = _latest(*(helpers.parse_time(d.get("last_fetch_at")) for d in devs))
    seen = _latest(fetch, *(helpers.parse_time(d.get("updated_at")) for d in devs))
    facts = []
    for d in devs[:3]:
        via = (f"UnifiedPush via {push.endpoint_host(d['address'])}" if d.get("transport") == "unifiedpush" else "FCM")
        facts.append(f"{d.get('name') or 'Phone'} · {via}" + (f" · app {d['app_version']}" if d.get("app_version") else "")
                     + f" · last check-in {_ago_or_never(helpers.parse_time(d.get('last_fetch_at')) or helpers.parse_time(d.get('updated_at')), now)}")
    if last_ok:
        facts.append(f"Last push accepted {_ago_or_never(last_ok, now)}")
    if last_fail:
        facts.append(f"Last push failure {_ago_or_never(last_fail, now)}"
                     + (f": {m.get('detail')}" if m.get("detail") else ""))
    blocks = [{"type": "facts", "label": "Push", "items": facts}] if facts else []
    why = "Reminders, alerts and the briefing may not reach the phone."
    if not push.configured() and any(d.get("transport") == "fcm" for d in devs):
        return Check(False, "FCM key missing on the server", None, title="Push isn't configured",
                     why=why + " A phone is registered for FCM but the server has no FCM_SERVICE_ACCOUNT_JSON.",
                     blocks=blocks)
    if old_token and not (fetch and fetch >= old_token[0]):
        at, label, last_used = old_token
        return Check(False, "Fetches with a token of no device", at, title="This phone signs in with an old token",
                     why="It still gets push, but Dana can't tell which phone it is, and push will stop if Firebase "
                     "changes the phone's address. Pair it again: Devices → Pair a device, then in the app "
                     "Settings → Server → Pair again.",
                     blocks=[{"type": "facts", "label": "Token", "items": [
                         f"{label or 'Unnamed token'} · last used {_ago_or_never(last_used or at, now)}"]}] + blocks,
                     fix=Fix.screen("devices", "Open Devices"))
    if not devs:
        return Check(False, "No phone registered", last_ok, title="No phone registered for push",
                     why=why + " Open the paired Dana app once so it registers again "
                     "(not paired yet: Devices → Pair a device).", blocks=blocks,
                     fix=Fix.screen("devices", "Open Devices"))
    if m.get("ok") == 0 and not (fetch and fetch > last_fail):
        return Check(False, f"Last push failed {_ago_or_never(last_fail, now)}", last_ok,
                     title="Push to the phone is failing", why=why, blocks=blocks,
                     fix=Fix.run(PUSH_TEST, "Send a test push"))
    if seen and (now - seen).total_seconds() > PUSH_FETCH_HOURS * 3600:
        return Check(False, f"Phone quiet for {helpers.span((now - seen).total_seconds())}", seen,
                     title=f"The phone hasn't checked in for {helpers.span((now - seen).total_seconds())}",
                     why=why, blocks=blocks)
    return Check(True, f"Last check-in {_ago_or_never(seen, now)}", seen, blocks=blocks)


PUSH_TEST = "test"


@addon.fix("push")
async def fix_push(user: User, action: str) -> str:
    """"Send a test push" on the failing push check: one notification to the user's phones, and whether one took it."""
    if await push.notify(user, "Dana", "A test from Needs attention: push to this phone works.", tag="push_test"):
        return "Your phone accepted the test push"
    raise ValueError("The test push failed too: open the Dana app on your phone so it registers again.")


@addon.selfcheck("notification_sync", "Notification sync")
def check_notification_sync(user: User, now: datetime.datetime) -> Check:
    with get_db_connection() as conn:
        m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                              (mark_key("notification_sync", user.id),)).fetchone() or {})
        row = conn.execute("SELECT MAX(created_at) AS t FROM notifications WHERE user_id = ?", (user.id,)).fetchone()
    last = _latest(helpers.parse_time(m.get("last_at")), helpers.parse_time(row["t"] if row else None))
    why = "Dana can't see what needs you. Check notification access for the Dana app."
    if not last:
        return Check(False, "No batch received yet", None, title="No notifications from the phone yet", why=why)
    quiet = (now - last).total_seconds()
    blocks = [{"type": "facts", "label": "Sync", "items": [f"Last batch {_ago_or_never(last, now)}"]}]
    if quiet > NOTIFICATION_SYNC_HOURS * 3600:
        return Check(False, f"quiet for {helpers.span(quiet)}", last,
                     title=f"No notifications from the phone for {helpers.span(quiet)}", why=why, blocks=blocks)
    return Check(True, f"last {_ago_or_never(last, now)}", last, blocks=blocks)


@addon.selfcheck("triage", "Notification sorting")
def check_triage(user: User, now: datetime.datetime) -> Check:
    since = (now - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        rows = {r["triage_status"]: r["n"] for r in conn.execute(
            "SELECT triage_status, COUNT(*) AS n FROM notifications WHERE user_id = ? AND posted_at >= ? "
            "GROUP BY triage_status", (user.id, since)).fetchall()}
        failed, done = rows.get("failed", 0), rows.get("done", 0)
        total = failed + done
        if failed >= 3 and failed >= 0.5 * total:
            last = conn.execute("SELECT MAX(updated_at) AS t FROM notifications WHERE user_id = ? "
                                "AND triage_status = 'failed'", (user.id,)).fetchone()
            return Check(False, f"{failed} of {total} failed in the last 24 h", helpers.parse_time(last["t"]),
                         title="Notification sorting is failing",
                         why=f"Triage calls to {judges.judge().model()} fail, so nothing is marked as needing you or as noise.")
    return Check(True, f"{done} sorted in the last 24 h" if total else "nothing to sort")


@addon.selfcheck("calls", "Call recordings")
def check_calls(user: User, now: datetime.datetime) -> Check:
    since = (now - datetime.timedelta(days=3)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        last = helpers.parse_time((conn.execute("SELECT MAX(created_at) AS t FROM phone_calls WHERE user_id = ?",
                                                (user.id,)).fetchone() or {"t": None})["t"])
        failed = [dict(r) for r in conn.execute(
            "SELECT id, title, error, created_at FROM phone_calls WHERE user_id = ? AND status = 'failed' "
            "AND created_at >= ? AND COALESCE(error, '') NOT LIKE 'No speech%' ORDER BY created_at DESC",
            (user.id, since)).fetchall()]
    if failed:
        facts = [f"{f.get('title') or 'Call'}: {(f.get('error') or 'failed')[:120]}" for f in failed[:4]]
        n = len(failed)
        return Check(False, f"{n} failed in the last 3 days", helpers.parse_time(failed[0]["created_at"]),
                     title=f"{n} call recording{'s' if n != 1 else ''} failed to process",
                     why="Their summaries and suggestions are missing. Reanalyze them from the call screen.",
                     blocks=[{"type": "facts", "label": "Failed", "items": facts}])
    if not last:
        return Check(True, "no calls yet")
    quiet = (now - last).total_seconds()
    if quiet > CALLS_DAYS * 86400:
        return Check(False, f"quiet for {helpers.span(quiet)}", last,
                     title=f"No call recording uploaded for {helpers.span(quiet)}",
                     why="The phone may have stopped uploading recordings.")
    return Check(True, f"last upload {_ago_or_never(last, now)}", last)
