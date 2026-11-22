"""Self-check: the signals Dana depends on, from stored timestamps.

Several parts of Dana fail silently: the wake signal was dead for almost three
months before anyone noticed. Each check reads a stored timestamp (no network
calls) and says whether the signal still arrives:

- ``signals`` / ``wake`` / ``briefing``: core's own checks (a source of the
  wake, car and place signals: core/signals.py; confirmed wakes in
  ``wake_log``; today's briefing in ``briefings``);
- ``budget`` / ``transcription``: core's server checks, for whoever may manage
  the server (background work paused by the monthly budget, core/budget.py;
  a transcription refused for want of a model, core/llm.py ``transcriber``);
- every addon's ``@addon.selfcheck`` (phone's ``push``, ``notification_sync``,
  ``triage``, ``calls``; Home Assistant's ``location``, ``car``, ``ha_entities``,
  ``ha_watcher``; tasks' ``reminders``; calendar's ``calendar``; money's
  ``money``; and any installed addon's checks).

Per user: :func:`for_user` gives the checks of the addons the user has active
plus core's own when available, and for users with ``see_everyone``, the
server's own (an addon check declared ``server=True``); ignoring one hides it
only for them (core/now_hidden.py). :func:`compute` answers every check for one
user, an addon they lack as ok "not connected".

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
from typing import Any, Callable, Dict, List, Optional, Tuple

from core import briefing, budget, clock, llm, now_hidden, signals, undo, users, wake, wake_window
from core.addons import helpers, registry
from core.addons.helpers import parse_time
from core.addons.hooks import Check, Hook
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

WAKE_DAYS = int(os.getenv("CHECK_WAKE_DAYS", "3"))

LABELS: Dict[str, str] = {
    "signals": "Wake, car and place signals",
    "wake": "Wake signal",
    "briefing": "Morning briefing",
    "budget": "Monthly budget",
    "transcription": "Transcription",
}
# A check about the server itself (``server=True``) is shown only to users who may see everyone.
SERVER_CAPABILITY = "see_everyone"

# Which user core's own checks apply to (a user without their prerequisites does not see them).
APPLIES: Dict[str, Callable[[User], bool]] = {
    "signals": lambda user: _uses_signals(user),
    "wake": lambda user: wake.available(user),
    "briefing": lambda user: briefing.available(user),
    "budget": lambda user: user.level.allows("manage_server"),
    "transcription": lambda user: user.level.allows("manage_server"),
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

def span(seconds: float) -> str:
    """'4 min' / '14 h' / '3 days'."""
    return helpers.span(seconds)


def ago(dt: Optional[datetime.datetime], now: Optional[datetime.datetime] = None) -> str:
    """:func:`core.addons.helpers.ago`, but "never" without a time."""
    return helpers.ago(dt, now) if dt else "never"


def _iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


# ---------------------------------------------------------------------------
# Core checks
# ---------------------------------------------------------------------------

def _result(key: str, ok: bool, detail: str, since: Optional[datetime.datetime], *,
            title: str = "", why: str = "", blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"key": key, "label": label_of(key), "ok": ok, "detail": detail, "since": _iso(since),
            "title": title, "why": why, "blocks": blocks or []}


def wake_week(conn, now: datetime.datetime, user: User) -> List[Dict[str, Any]]:
    """``user``'s last 7 mornings, oldest first: {label, value, ok} (ok None = today, not yet)."""
    times: Dict[str, str] = {}
    window = wake_window.get(user)
    start = (now.date() - datetime.timedelta(days=6)).isoformat()
    for r in conn.execute("SELECT date, created_at FROM briefings WHERE user_id = ? AND trigger = 'wake' AND date >= ?",
                          (user.id, start)).fetchall():
        t = parse_time(r["created_at"])
        if t:
            times[r["date"]] = t.strftime("%H:%M")
    for r in conn.execute("SELECT date, at FROM wake_log WHERE user_id = ? AND date >= ?", (user.id, start)).fetchall():
        t = parse_time(r["at"])
        if t:
            times[r["date"]] = t.strftime("%H:%M")
        elif r["date"] not in times:
            times[r["date"]] = "✓"
    cells = []
    for i in range(6, -1, -1):
        d = now.date() - datetime.timedelta(days=i)
        v = times.get(d.isoformat())
        pending = i == 0 and not v and now.time() < window.end_time
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
    blocks: List[Dict[str, Any]] = [{"type": "week", "label": "Last 7 mornings", "cells": wake_week(conn, now, user)}]
    window = wake_window.get(user)
    facts = [f"Wake window {window.start}–{window.end}" + (" (learned)" if window.learned else "")]
    for s in signals.sources(user, "wake"):
        facts.append(f"{s.label}: last wake signal ({s.detail}) {ago(s.last_at, now)}" if s.last_at
                     else f"{s.label}: no wake signal yet" + (f" ({s.missing})" if s.missing else ""))
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


_SIGNAL_WORDS = {"wake": "wake up", "car": "drive", "location": "arrive somewhere"}


def _uses_signals(user: User) -> bool:
    """``user`` has something that listens to the car / place signals (reminders) or a delivery channel."""
    return bool(registry.handlers("core.signal.location", user) or registry.handlers("core.signal.car", user)
                or registry.channels(user))


def _check_signals(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not _uses_signals(user):
        return _result("signals", True, "not connected", None)
    facts = []
    for signal in signals.NEEDED:
        states = signals.sources(user, signal)
        current = signals.active(user, signal)
        label = next((s.label for s in states if s.source == current), None)
        facts.append(f"{signal.capitalize()}: " + (label or "no source yet"))
    missing = signals.missing(user)
    if not missing:
        return _result("signals", True, "every signal has a source", None,
                       blocks=[{"type": "facts", "label": "Sources", "items": facts}])
    words = [_SIGNAL_WORDS[s] for s in missing]
    phrase = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]
    return _result("signals", False, f"no source for {', '.join(missing)}", None,
                   title=f"Dana can't tell when you {phrase}",
                   why="Wake briefings and car and place reminders need a source: pair Dana's phone app "
                       "(Settings → Devices), or connect Home Assistant.",
                   blocks=[{"type": "facts", "label": "Sources", "items": facts}])


def _check_briefing(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not briefing.available(user):
        return _result("briefing", True, "not connected", None)
    r = conn.execute("SELECT created_at, trigger FROM briefings WHERE user_id = ? AND date = ?",
                     (user.id, now.date().isoformat())).fetchone()
    if r:
        return _result("briefing", True, f"made {ago(parse_time(r['created_at']), now)} ({r['trigger']})",
                       parse_time(r["created_at"]))
    if now.hour >= 11:
        last = conn.execute("SELECT MAX(created_at) AS t FROM briefings WHERE user_id = ?", (user.id,)).fetchone()
        return _result("briefing", False, "not made today", parse_time(last["t"] if last else None),
                       title="No briefing today", why="The 10:00 fallback didn't run; the server may be stuck.")
    return _result("briefing", True, "not due yet", None)


def _check_budget(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not APPLIES["budget"](user):
        return _result("budget", True, "not connected", None)
    st = budget.state(now.date())
    if st["limit"] is None:
        return _result("budget", True, "no budget set", None)
    used = f"${st['spent']:,.2f} of ${st['limit']:,.2f}"
    if st["state"] == budget.PAUSED:
        day = datetime.date.fromisoformat(st["resets"])
        resets = f"{day.day} {day.strftime('%B')}"
        return _result("budget", False, "paused by the budget", None, title="Background work paused by the budget",
                       why=f"This month's model budget is spent ({used}). Triage, briefs, learning and summaries "
                           f"wait until {resets} or until the budget is raised on the Usage page; chat and voice "
                           "keep working.",
                       blocks=[{"type": "facts", "label": "This month",
                                "items": [f"Spent {used}", f"Forecast ${st['forecast']:,.2f}"]}])
    return _result("budget", True, f"{used} this month", None)


def _check_transcription(conn, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if user is None or not APPLIES["transcription"](user):
        return _result("transcription", True, "not connected", None)
    t = llm.transcriber()
    if t is not None:
        return _result("transcription", True, t.model, None)
    refused = llm.transcription_refused()
    if refused is None:
        return _result("transcription", True, "no model; nothing asked for one yet", None)
    since = datetime.datetime.fromtimestamp(refused["at"], clock.get_timezone())
    return _result("transcription", False, "needs a transcription model", since,
                   title="Transcription needs a model",
                   why="Voice notes, call recordings and voice turns are not turned into text: Claude does not hear "
                       "audio. Add an OpenAI or Gemini key in Settings → The model, or run a local Whisper server "
                       "and set WHISPER_URL.")


_CHECKS = {
    "signals": _check_signals,
    "wake": _check_wake,
    "briefing": _check_briefing,
    "budget": _check_budget,
    "transcription": _check_transcription,
}


def _hooks() -> List[Hook]:
    """The addons' checks (every addon enabled on the server), skipping any under a key of core's own checks."""
    out = []
    for h in registry.hooks("selfcheck", None):
        if h.key in LABELS:
            logger.warning(f"The check {h.key!r} of {h.addon} is one of core's own; left out")
            continue
        out.append(h)
    return out


def _hook(key: str) -> Optional[Hook]:
    return None if key in LABELS else registry.hook_for("selfcheck", key, None)


def all_keys() -> Tuple[str, ...]:
    """Every check: core's, with each addon's placed after the one it names (else at the end)."""
    out = list(LABELS)
    for hook in _hooks():
        after = hook.option("after", "")
        out.insert(out.index(after) + 1 if after in out else len(out), hook.key)
    return tuple(out)


def label_of(key: str) -> str:
    """How the check ``key`` is named."""
    if key in LABELS:
        return LABELS[key]
    hook = _hook(key)
    return str(hook.option("label", key)) if hook else key


def _hook_check(hook: Hook, now: datetime.datetime, user: Optional[User]) -> Dict[str, Any]:
    if not hook.option("server", False) and (user is None or hook.addon not in registry.active_addons(user)):
        return _result(hook.key, True, "not connected", None)
    c: Check = hook.func(user, now)
    return c.to_json(hook.key, label_of(hook.key))


def compute(now: Optional[datetime.datetime] = None, user: Optional[User] = None,
            keys: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """Every check (or only ``keys``) for ``user`` (default: the current user),
    with ``ignored_until`` when they chose to ignore it; a check of an addon
    they lack is ok "not connected". Internal fields (``title``, ``why``,
    ``blocks``) feed the Now card."""
    now = now or clock.now()
    user = user or users.current()
    hooks = {h.key: h for h in _hooks()}
    out = []
    with get_db_connection() as conn:
        for key in all_keys():
            if keys is not None and key not in keys:
                continue
            try:
                c = _hook_check(hooks[key], now, user) if key in hooks else _CHECKS[key](conn, now, user)
            except Exception as e:
                logger.warning(f"Check {key} failed: {e}", exc_info=True)
                c = _result(key, True, "couldn't check", None)
            c["ignored_until"] = now_hidden.until(user, f"check:{key}") if user is not None else None
            out.append(c)
    return out


def _applies(key: str, user: User) -> bool:
    hook = _hook(key)
    if hook is None:
        return APPLIES[key](user)
    if hook.option("server", False):
        return user.level.allows(SERVER_CAPABILITY)
    return hook.addon in registry.active_addons(user)


def keys_for(user: User) -> Tuple[str, ...]:
    """The checks ``user`` sees: those of the addons they have connected, and
    the server's own when they may see everyone (in :func:`all_keys` order)."""
    out = []
    for key in all_keys():
        try:
            if _applies(key, user):
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
        label = f"Ignored “{label_of(key)}” for {int(days) if float(days).is_integer() else days} days"
    else:
        now_hidden.unhide(user, item)
        until = None
        label = f"Watching “{label_of(key)}” again"
    uid = undo.record(label, lambda: now_hidden.restore(user, item, before), source="checks.ignore")
    return {"ok": True, "message": label, "until": _iso(until), "undo_id": uid}
