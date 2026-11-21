"""Self-check: the signals Dana depends on, from stored timestamps.

Several parts of Dana fail silently: the wake signal was dead for almost three
months before anyone noticed. Each check here reads a stored timestamp (no
network calls) and says whether the signal still arrives:

- ``push``: a phone of the user is registered for direct push, FCM accepts
  pings to it and it keeps fetching events (marked per user by
  addons/phone/push.py).
- ``notification_sync``: the user's phone sends notification batches.
- ``location`` / ``car``: the user's Home Assistant watcher still sees their
  person entity / car sensor change.
- ``ha_entities`` / ``ha_watcher``: the entities selected in the user's Home
  Assistant settings exist, and the watcher is connected
  (addons/homeassistant/watcher.py, marks in its ``status`` module).
- ``wake``: the user's wake was confirmed within ``CHECK_WAKE_DAYS`` (3).
- ``reminders``: the time-trigger loop is alive (the server's own: only for
  users with ``see_everyone``).
- ``triage``: TypeSafe answers (the user's notifications aren't all failing to sort).
- ``calls``: the user's recordings arrive and get processed.
- ``calendar``: the user's Google Calendar can be read (marked per user by
  addons/calendar/today.py, the Now screen's cache).
- ``briefing``: the user's briefing of today exists by late morning.
- ``money``: each of the user's bank / card logins syncs: none waits for an
  SMS code or needs its login fixed, and each had a good sync within
  24 hours (addons/money, table ``money_sources``).

Per user: :func:`for_user` gives the checks of the addons the user has
connected (:data:`APPLIES`) plus, for users with ``see_everyone``, the
server's own (:data:`SERVER_CHECKS`); ignoring one hides it only for them
(core/now_hidden.py). :func:`compute` answers every check for one user, a
addon they lack as ok "not connected".

Signals without a timestamp elsewhere are recorded with :func:`mark` (table
``system_marks``); confirmed wakes are in ``wake_log`` (one row per user and
date, written by core/wake.py) so the "last 7 mornings" view has a history. A
check that is not ok becomes a ``check:<key>`` card in ``GET /api/now`` unless
it is ignored (``POST /api/checks/{key}/ignore``).
"""
from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import briefing, clock, now_hidden, undo, users, wake
from core.db import get_db_connection
from core.users import User
from addons.calendar import config as calendar_config, today as calendar_today
from core.addons import store
from addons.homeassistant import config as ha_config, status as ha_status, watcher as ha_watcher
from addons.money import companies as money_companies, config as money_config
from addons.people.values import ago as people_ago
from addons.phone import push as phone_push
from addons.phone.config import ADDON as PHONE, mark_key as phone_mark_key
from addons.tasks import triggers as task_triggers

logger = logging.getLogger(__name__)

PUSH_FETCH_HOURS = float(os.getenv("CHECK_PUSH_FETCH_HOURS", "24"))
NOTIFICATION_SYNC_HOURS = float(os.getenv("CHECK_NOTIFICATION_SYNC_HOURS", "12"))
LOCATION_DAYS = float(os.getenv("CHECK_LOCATION_DAYS", "3"))
CAR_DAYS = float(os.getenv("CHECK_CAR_DAYS", "7"))
WAKE_DAYS = int(os.getenv("CHECK_WAKE_DAYS", "3"))
CALLS_DAYS = float(os.getenv("CHECK_CALLS_DAYS", "7"))
HA_WATCHER_MINUTES = float(os.getenv("CHECK_HA_WATCHER_MINUTES", "10"))
TIME_CHECKER_MINUTES = 5

_STARTED = time.monotonic()

LABELS: Dict[str, str] = {
    "push": "Push to the phone",
    "notification_sync": "Notification sync",
    "location": "Location updates",
    "car": "Car updates",
    "ha_entities": "Home Assistant entities",
    "ha_watcher": "Home Assistant connection",
    "wake": "Wake signal",
    "reminders": "Reminder checks",
    "triage": "Notification sorting",
    "calls": "Call recordings",
    "calendar": "Calendar",
    "briefing": "Morning briefing",
    "money": "Bank and card syncs",
}
KEYS = tuple(LABELS)
# The checks about the server itself: shown only to users who may see everyone.
SERVER_CHECKS = ("reminders",)
SERVER_CAPABILITY = "see_everyone"


def _addon(name: str) -> Callable[[User], bool]:
    return lambda user: store.is_connected(user, name)


# Which user a check is for: the addons it reads (a user without them does not see it).
APPLIES: Dict[str, Callable[[User], bool]] = {
    "push": _addon(PHONE),
    "notification_sync": _addon(PHONE),
    "location": _addon(ha_config.ADDON),
    "car": _addon(ha_config.ADDON),
    "ha_entities": _addon(ha_config.ADDON),
    "ha_watcher": _addon(ha_config.ADDON),
    "wake": wake.available,
    "triage": _addon(PHONE),
    "calls": _addon(PHONE),
    "calendar": _addon(calendar_config.ADDON),
    "briefing": briefing.available,
    "money": _addon(money_config.ADDON),
}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _utc_iso(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc).isoformat(timespec="seconds")


def mark(key: str, ok: bool = True, detail: str = "") -> None:
    """Record that a signal arrived (``ok``) or an attempt failed. Never raises."""
    try:
        now = _utc_iso()
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET last_at = excluded.last_at, ok = excluded.ok, detail = excluded.detail, "
                "last_ok_at = COALESCE(excluded.last_ok_at, system_marks.last_ok_at), "
                "last_fail_at = COALESCE(excluded.last_fail_at, system_marks.last_fail_at)",
                (key, now, 1 if ok else 0, now if ok else None, None if ok else now, (detail or "")[:300]))
            conn.commit()
    except Exception as e:
        logger.debug(f"checks.mark({key}) failed: {e}")


def get_mark(key: str) -> Dict[str, Any]:
    with get_db_connection() as conn:
        r = conn.execute("SELECT * FROM system_marks WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else {}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse(value: Any) -> Optional[datetime.datetime]:
    """ISO with or without zone, or SQLite's CURRENT_TIMESTAMP (naive = UTC) → local aware."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(clock.get_timezone())


def span(seconds: float) -> str:
    """'4 min' / '14 h' / '3 days'."""
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)} h"
    return f"{int(seconds // 86400)} days"


def ago(dt: Optional[datetime.datetime], now: Optional[datetime.datetime] = None) -> str:
    if not dt:
        return "never"
    return people_ago(dt, now or clock.now())


def _iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _latest(*values: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    vals = [v for v in values if v]
    return max(vals) if vals else None


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def _result(key: str, ok: bool, detail: str, since: Optional[datetime.datetime], *,
            title: str = "", why: str = "", blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"key": key, "label": LABELS[key], "ok": ok, "detail": detail, "since": _iso(since),
            "title": title, "why": why, "blocks": blocks or []}


def _check_push(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, PHONE):
        return _result("push", True, "not connected", None)
    devs = [dict(r) for r in conn.execute(
        "SELECT device_id, name, app_version, updated_at, last_fetch_at FROM phone_devices WHERE user_id = ?",
        (user.id,)).fetchall()]
    m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                          (phone_mark_key("push", user.id),)).fetchone() or {})
    last_ok, last_fail = parse(m.get("last_ok_at")), parse(m.get("last_fail_at"))
    fetch = _latest(*(parse(d.get("last_fetch_at")) for d in devs))
    seen = _latest(fetch, *(parse(d.get("updated_at")) for d in devs))
    facts = []
    for d in devs[:3]:
        facts.append(f"{d.get('name') or 'Phone'}" + (f" · app {d['app_version']}" if d.get("app_version") else "")
                     + f" · last check-in {ago(parse(d.get('last_fetch_at')) or parse(d.get('updated_at')), now)}")
    if last_ok:
        facts.append(f"Last push accepted {ago(last_ok, now)}")
    if last_fail:
        facts.append(f"Last push failure {ago(last_fail, now)}" + (f": {m.get('detail')}" if m.get("detail") else ""))
    blocks = [{"type": "facts", "label": "Push", "items": facts}] if facts else []
    why = "Reminders, alerts and the briefing may not reach the phone."
    if not phone_push.configured():
        return _result("push", False, "FCM key missing on the server", None, title="Push isn't configured",
                       why=why, blocks=blocks)
    if not devs:
        return _result("push", False, "No phone registered", last_ok, title="No phone registered for push",
                       why=why + " Open the Dana app once so it registers again.", blocks=blocks)
    if m.get("ok") == 0 and not (fetch and fetch > last_fail):
        return _result("push", False, f"Last push failed {ago(last_fail, now)}", last_ok,
                       title="Push to the phone is failing", why=why, blocks=blocks)
    if seen and (now - seen).total_seconds() > PUSH_FETCH_HOURS * 3600:
        return _result("push", False, f"Phone quiet for {span((now - seen).total_seconds())}", seen,
                       title=f"The phone hasn't checked in for {span((now - seen).total_seconds())}",
                       why=why, blocks=blocks)
    return _result("push", True, f"Last check-in {ago(seen, now)}", seen, blocks=blocks)


def _check_notification_sync(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, PHONE):
        return _result("notification_sync", True, "not connected", None)
    m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                          (phone_mark_key("notification_sync", user.id),)).fetchone() or {})
    row = conn.execute("SELECT MAX(created_at) AS t FROM notifications WHERE user_id = ?", (user.id,)).fetchone()
    last = _latest(parse(m.get("last_at")), parse(row["t"] if row else None))
    why = "Dana can't see what needs you. Check notification access for the Dana app."
    if not last:
        return _result("notification_sync", False, "No batch received yet", None,
                       title="No notifications from the phone yet", why=why)
    quiet = (now - last).total_seconds()
    blocks = [{"type": "facts", "label": "Sync", "items": [f"Last batch {ago(last, now)}"]}]
    if quiet > NOTIFICATION_SYNC_HOURS * 3600:
        return _result("notification_sync", False, f"quiet for {span(quiet)}", last,
                       title=f"No notifications from the phone for {span(quiet)}", why=why, blocks=blocks)
    return _result("notification_sync", True, f"last {ago(last, now)}", last, blocks=blocks)


def _check_trigger_feed(conn, now: datetime.datetime, user: Optional[User], key: str, prefix: str, role: str,
                        setting: str, days: float, what: str, why: str) -> Dict[str, Any]:
    # The user's feed: their latest transition (fired by their Home Assistant
    # watcher) or the latest change of the watched entity; anyone's without a user.
    last = task_triggers.last_transition(prefix, user)
    if user is not None:
        last = _latest(last, parse(ha_status.read(user.id).last_event.get(role)))
    if not last:
        return _result(key, False, "never received", None, title=f"No {what} updates received yet", why=why)
    quiet = (now - last).total_seconds()
    if quiet > days * 86400:
        return _result(key, False, f"quiet for {span(quiet)}", last,
                       title=f"No {what} updates for {span(quiet)}", why=why,
                       blocks=[{"type": "text", "label": "Where it comes from",
                                "text": f"Dana watches the entity in the {setting} setting of your Home Assistant "
                                        "addon. After a phone change, set the new phone's entity there."}])
    return _result(key, True, f"last {ago(last, now)}", last)


def _ha_watch(user: Optional[User]) -> Tuple[Any, Optional[ha_watcher.WatchSpec]]:
    """The user's Home Assistant connection and what its watcher watches."""
    conn = store.get(user, ha_config.ADDON) if user is not None else None
    return conn, ha_watcher.spec_for(conn)


def _check_ha_entities(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    ha, spec = _ha_watch(user)
    if ha is None or user is None:
        return _result("ha_entities", True, "not connected", None)
    if spec is None:
        return _result("ha_entities", True, "nothing selected", None)
    st = ha_status.read(user.id)
    seen = {role: st.entities.get(role) for role in spec.entities}
    missing = [(role, eid) for role, eid in spec.entities.items()
               if seen[role] is not None and seen[role].entity_id == eid and not seen[role].exists]
    if missing:
        names = ", ".join(eid for _, eid in missing)
        return _result("ha_entities", False, f"{names} not found", None,
                       title=f"Home Assistant has no {names}",
                       why="Location and car reminders need the entities selected in your Home Assistant "
                           "addon settings. Pick the right ones there.",
                       blocks=[{"type": "facts", "label": "Not found",
                                "items": [f"{role}: {eid}" for role, eid in missing]}])
    checked = all(seen[role] is not None and seen[role].entity_id == eid for role, eid in spec.entities.items())
    return _result("ha_entities", True, "all found" if checked else "not checked yet", None)


def _check_ha_watcher(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    ha, spec = _ha_watch(user)
    if ha is None or user is None:
        return _result("ha_watcher", True, "not connected", None)
    if spec is None:
        return _result("ha_watcher", True, "nothing to watch", None)
    st = ha_status.read(user.id)
    since = parse(st.since)
    why = "Location and car reminders won't fire while Dana can't watch your Home Assistant."
    blocks = [{"type": "text", "label": "Error", "text": st.detail}] if st.detail else []
    if st.connected:
        return _result("ha_watcher", True, "connected", since)
    if st.connected is None:
        if time.monotonic() - _STARTED < HA_WATCHER_MINUTES * 60:
            return _result("ha_watcher", True, "starting", None)
        return _result("ha_watcher", False, "never connected", None,
                       title="Dana can't reach your Home Assistant", why=why, blocks=blocks)
    down = (now - since).total_seconds() if since else float("inf")
    if down < HA_WATCHER_MINUTES * 60:
        return _result("ha_watcher", True, "reconnecting", since)
    return _result("ha_watcher", False, f"disconnected for {span(down)}", since,
                   title=f"Dana lost your Home Assistant {ago(since, now)}", why=why, blocks=blocks)


def wake_week(conn, now: datetime.datetime, user: User) -> List[Dict[str, Any]]:
    """``user``'s last 7 mornings, oldest first: {label, value, ok} (ok None = today, not yet)."""
    times: Dict[str, str] = {}
    start = (now.date() - datetime.timedelta(days=6)).isoformat()
    for r in conn.execute("SELECT date, created_at FROM briefings WHERE user_id = ? AND trigger = 'wake' AND date >= ?",
                          (user.id, start)).fetchall():
        t = parse(r["created_at"])
        if t:
            times[r["date"]] = t.strftime("%H:%M")
    for r in conn.execute("SELECT date, at FROM wake_log WHERE user_id = ? AND date >= ?", (user.id, start)).fetchall():
        t = parse(r["at"])
        if t:
            times[r["date"]] = t.strftime("%H:%M")
        elif r["date"] not in times:
            times[r["date"]] = "✓"  # the day is known, not the time (moved from the old wake date)
    cells = []
    for i in range(6, -1, -1):
        d = now.date() - datetime.timedelta(days=i)
        v = times.get(d.isoformat())
        pending = i == 0 and not v and now.hour < wake.WAKE_WINDOW[1]
        cells.append({"label": d.strftime("%a"), "value": v or "–", "ok": None if pending else bool(v)})
    return cells


def _check_wake(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not wake.available(user):
        return _result("wake", True, "not connected", None)
    row = conn.execute("SELECT MAX(date) AS d FROM wake_log WHERE user_id = ?", (user.id,)).fetchone()
    last_date = None
    if row and row["d"]:
        try:
            last_date = datetime.date.fromisoformat(row["d"])
        except ValueError:
            last_date = None
    signal = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                               (phone_mark_key(wake.SIGNAL_MARK, user.id),)).fetchone() or {})
    sig_at = parse(signal.get("last_at"))
    blocks: List[Dict[str, Any]] = [{"type": "week", "label": "Last 7 mornings", "cells": wake_week(conn, now, user)}]
    facts = []
    if sig_at:
        facts.append(f"Last wake signal ({signal.get('detail') or 'unlock'}) {ago(sig_at, now)}")
    else:
        facts.append("No unlock reported by the phone since this check started")
    blocks.append({"type": "facts", "label": "Signals", "items": facts})
    since = datetime.datetime.combine(last_date, datetime.time(0, 0), clock.get_timezone()) if last_date else None
    why = "Morning reminders and the briefing depend on it."
    if not last_date:
        return _result("wake", False, "no wake confirmed yet", None, title="No wake signal yet", why=why, blocks=blocks)
    days = (now.date() - last_date).days
    if days >= WAKE_DAYS:
        return _result("wake", False, f"quiet for {days} days", since,
                       title=f"No wake signal for {days} days", why=why, blocks=blocks)
    return _result("wake", True, "confirmed today" if days == 0 else
                   ("confirmed yesterday" if days == 1 else f"confirmed {days} days ago"), since, blocks=blocks)


def _check_reminders(conn, now: datetime.datetime) -> Dict[str, Any]:
    m = dict(conn.execute("SELECT * FROM system_marks WHERE key = 'time_checker'").fetchone() or {})
    last = parse(m.get("last_at"))
    why = "Time reminders won't fire until the server restarts."
    if not last:
        if time.monotonic() - _STARTED < TIME_CHECKER_MINUTES * 60:
            return _result("reminders", True, "starting", None)
        return _result("reminders", False, "never ran", None, title="Reminder checks aren't running", why=why)
    quiet = (now - last).total_seconds()
    if quiet > TIME_CHECKER_MINUTES * 60:
        return _result("reminders", False, f"stopped {ago(last, now)}", last,
                       title="Reminder checks stopped", why=why)
    return _result("reminders", True, f"last {ago(last, now)}", last)


def _check_triage(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, PHONE):
        return _result("triage", True, "not connected", None)
    since = (now - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    rows = {r["triage_status"]: r["n"] for r in conn.execute(
        "SELECT triage_status, COUNT(*) AS n FROM notifications WHERE user_id = ? AND posted_at >= ? "
        "GROUP BY triage_status", (user.id, since)).fetchall()}
    failed, done = rows.get("failed", 0), rows.get("done", 0)
    total = failed + done
    if failed >= 3 and failed >= 0.5 * total:
        last = conn.execute("SELECT MAX(updated_at) AS t FROM notifications WHERE user_id = ? "
                            "AND triage_status = 'failed'", (user.id,)).fetchone()
        return _result("triage", False, f"{failed} of {total} failed in the last 24 h", parse(last["t"]),
                       title="Notification sorting is failing",
                       why="TypeSafe calls fail, so nothing is marked as needing you or as noise.")
    return _result("triage", True, f"{done} sorted in the last 24 h" if total else "nothing to sort", None)


def _check_calls(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, PHONE):
        return _result("calls", True, "not connected", None)
    last = parse((conn.execute("SELECT MAX(created_at) AS t FROM phone_calls WHERE user_id = ?",
                               (user.id,)).fetchone() or {"t": None})["t"])
    since = (now - datetime.timedelta(days=3)).astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    failed = [dict(r) for r in conn.execute(
        "SELECT id, title, error, created_at FROM phone_calls WHERE user_id = ? AND status = 'failed' "
        "AND created_at >= ? AND COALESCE(error, '') NOT LIKE 'No speech%' ORDER BY created_at DESC",
        (user.id, since)).fetchall()]
    if failed:
        facts = [f"{f.get('title') or 'Call'}: {(f.get('error') or 'failed')[:120]}" for f in failed[:4]]
        n = len(failed)
        return _result("calls", False, f"{n} failed in the last 3 days", parse(failed[0]["created_at"]),
                       title=f"{n} call recording{'s' if n != 1 else ''} failed to process",
                       why="Their summaries and suggestions are missing. Reanalyze them from the call screen.",
                       blocks=[{"type": "facts", "label": "Failed", "items": facts}])
    if not last:
        return _result("calls", True, "no calls yet", None)
    quiet = (now - last).total_seconds()
    if quiet > CALLS_DAYS * 86400:
        return _result("calls", False, f"quiet for {span(quiet)}", last,
                       title=f"No call recording uploaded for {span(quiet)}",
                       why="The phone may have stopped uploading recordings.")
    return _result("calls", True, f"last upload {ago(last, now)}", last)


def _check_calendar(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, calendar_config.ADDON):
        return _result("calendar", True, "not connected", None)
    m = dict(conn.execute("SELECT * FROM system_marks WHERE key = ?",
                          (calendar_today.mark_key(user.id),)).fetchone() or {})
    ok_at, fail_at = parse(m.get("last_ok_at")), parse(m.get("last_fail_at"))
    if not ok_at and not fail_at:
        return _result("calendar", True, "not checked yet", None)
    if m.get("ok") == 0:
        return _result("calendar", False, f"failing since {ago(fail_at, now)}", ok_at,
                       title="The calendar can't be read",
                       why="Events are missing from Now and the briefing.",
                       blocks=[{"type": "text", "label": "Error", "text": m.get("detail") or "Unknown error"}])
    return _result("calendar", True, f"read {ago(ok_at, now)}", ok_at)


def _check_briefing(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not briefing.available(user):
        return _result("briefing", True, "not connected", None)
    r = conn.execute("SELECT created_at, trigger FROM briefings WHERE user_id = ? AND date = ?",
                     (user.id, now.date().isoformat())).fetchone()
    if r:
        return _result("briefing", True, f"made {ago(parse(r['created_at']), now)} ({r['trigger']})",
                       parse(r["created_at"]))
    if now.hour >= 11:
        last = conn.execute("SELECT MAX(created_at) AS t FROM briefings WHERE user_id = ?", (user.id,)).fetchone()
        return _result("briefing", False, "not made today", parse(last["t"] if last else None),
                       title="No briefing today", why="The 10:00 fallback didn't run; the server may be stuck.")
    return _result("briefing", True, "not due yet", None)


def _check_money(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not store.is_connected(user, money_config.ADDON):
        return _result("money", True, "not connected", None)
    rows = [dict(r) for r in conn.execute(
        "SELECT company, label, state, state_reason, last_ok_at, created_at FROM money_sources WHERE user_id = ? "
        "ORDER BY created_at, id", (user.id,)).fetchall()]
    if not rows:
        return _result("money", True, "no bank or card added", None)
    problems: List[Tuple[str, str]] = []  # (title, fact)
    for r in rows:
        name = (r.get("label") or "").strip() or money_companies.name_of(r["company"])
        last_ok = parse(r.get("last_ok_at"))
        if r["state"] == "needs_code":
            problems.append((f"{name} is waiting for a sign-in code",
                             f"{name}: waiting for the SMS sign-in code (enter it on the Money page or in Telegram)"))
        elif r["state"] == "needs_attention":
            problems.append((f"{name} needs its login fixed", f"{name}: {r.get('state_reason') or 'the login failed'}"))
        else:
            since = last_ok or parse(r.get("created_at"))
            if since and (now - since).total_seconds() > money_config.STALE_HOURS * 3600:
                quiet = span((now - since).total_seconds())
                why = f" ({r['state_reason']})" if r.get("state_reason") else ""
                problems.append((f"{name} hasn't synced for {quiet}",
                                 f"{name}: no successful sync for {quiet}{why}" if last_ok
                                 else f"{name}: never synced{why}"))
    latest = _latest(*(parse(r.get("last_ok_at")) for r in rows))
    if problems:
        detail = problems[0][1] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
        return _result("money", False, detail, latest, title=problems[0][0],
                       why="New transactions are missing from the Money page and Dana's answers.",
                       blocks=[{"type": "facts", "label": "Banks and cards", "items": [p[1] for p in problems]}])
    return _result("money", True, f"synced {ago(latest, now)}", latest)


def _server(fn):
    """A check of the server's own feeds (the same for every user who may see it)."""
    return lambda conn, now, user: fn(conn, now)


_CHECKS = {
    "push": _check_push,
    "notification_sync": _check_notification_sync,
    "location": lambda conn, now, user: _check_trigger_feed(
        conn, now, user, "location", "location", "person", ha_config.PERSON, LOCATION_DAYS, "location",
        "Reminders for arriving at or leaving places won't fire."),
    "car": lambda conn, now, user: _check_trigger_feed(
        conn, now, user, "car", "car", "car", ha_config.CAR_SENSOR, CAR_DAYS, "car",
        "In-the-car reminders won't fire."),
    "ha_entities": _check_ha_entities,
    "ha_watcher": _check_ha_watcher,
    "wake": _check_wake,
    "reminders": _server(_check_reminders),
    "triage": _check_triage,
    "calls": _check_calls,
    "calendar": _check_calendar,
    "briefing": _check_briefing,
    "money": _check_money,
}


def compute(now: Optional[datetime.datetime] = None, user: Optional[User] = None,
            keys: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """Every check (or only ``keys``) for ``user`` (default: the current user),
    with ``ignored_until`` when they chose to ignore it; a check of an addon
    they lack is ok "not connected". Internal fields (``title``, ``why``,
    ``blocks``) feed the Now card."""
    now = now or clock.now()
    user = user or users.current()
    out = []
    with get_db_connection() as conn:
        for key, fn in _CHECKS.items():
            if keys is not None and key not in keys:
                continue
            try:
                c = fn(conn, now, user)
            except Exception as e:
                logger.warning(f"Check {key} failed: {e}", exc_info=True)
                c = _result(key, True, "couldn't check", None)
            c["ignored_until"] = now_hidden.until(user, f"check:{key}") if user is not None else None
            out.append(c)
    return out


def keys_for(user: User) -> Tuple[str, ...]:
    """The checks ``user`` sees: those of the addons they have connected, and
    the server's own when they may see everyone (in :data:`KEYS` order)."""
    out = []
    for key in KEYS:
        if key in SERVER_CHECKS:
            if user.level.allows(SERVER_CAPABILITY):
                out.append(key)
            continue
        try:
            if APPLIES[key](user):
                out.append(key)
        except Exception as e:
            logger.warning(f"Check {key}: who it applies to failed for user {user.id}: {e}")
    return tuple(out)


def for_user(user: User, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """The checks ``user`` sees (:func:`keys_for`), computed for them."""
    return compute(now, user, keys_for(user))


def public(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("key", "label", "ok", "detail", "since", "ignored_until")}


def ignore(user: User, key: str, days: float = 7) -> Dict[str, Any]:
    """Hide the Now card of ``user``'s check for ``days`` (0 = stop ignoring),
    only for them. Returns {until, undo_id}.

    Raises:
        LookupError: not one of the checks they see.
    """
    if key not in keys_for(user):
        raise LookupError(f"unknown check '{key}'")
    item = f"check:{key}"
    before = now_hidden.row(user, item)
    if days and days > 0:
        until = clock.now() + datetime.timedelta(days=float(days))
        now_hidden.hide(user, item, until, "ignore")
        label = f"Ignored “{LABELS[key]}” for {int(days) if float(days).is_integer() else days} days"
    else:
        now_hidden.unhide(user, item)
        until = None
        label = f"Watching “{LABELS[key]}” again"
    uid = undo.record(label, lambda: now_hidden.restore(user, item, before), source="checks.ignore")
    return {"ok": True, "message": label, "until": _iso(until), "undo_id": uid}
