"""Live activities: something ongoing an addon describes in plain terms, kept
current on the user's clients (docs/live.md).

An addon starts one for a user under its own ``key``, updates it and ends it::

    from core import live

    await live.start(user, addon, "watch-12", live.Activity(title="Departs 17:46", icon="train-front",
                                                            countdown=live.Countdown(departs, "Departs")))
    await live.update(user, addon, "watch-12", activity)        # None when it is not running
    await live.end(user, addon, "watch-12", live.Final("Arrived", tone="good"))

Dana stores each activity (``live_activities``, one row per user, addon and
key; the wire id is ``<addon>:<key>``), validates it (:class:`LiveError`),
drops updates that change nothing on the wire, counts changes (``rev``),
keeps the user's selected choice, and pushes the whole state as the ``live``
event (:func:`to_json` of each, newest-updated first) through
``core.notify.send`` with ``replace=True``: the phone addon's ``push``
channel delivers it, so only users who have the phone addon get it. At most
one push per user every :data:`LIVE_PUSH_INTERVAL` seconds; a start, an end,
a new non-empty ``alert`` or a selection change go at once, and a trailing
push carries the latest of the rest.

An ended activity keeps its row: with a final, listed (ended) for
:data:`FINAL_KEEP`; without one (or once its final went or was dismissed)
as a hidden *gone* row (``ended_at`` set, ``final`` NULL) for
:data:`GONE_KEEP`, so that starting the same key again carries on its
``rev`` (clients comparing revs see the change) instead of starting at 1.

One loop (:func:`run_loop`, started with the server) ticks every
:data:`TICK_SECONDS`: it ends activities whose ``ends_at`` passed, drops
finals after :data:`FINAL_KEEP` and gone rows after :data:`GONE_KEEP`,
starts each addon's ``@addon.live_refresh`` for its due activities as a
background task (as their user, only while the addon is active for them;
never two at once for one activity; a failure is logged and never ends one;
a slow one never holds up the loop) and sends the trailing pushes. People act through core/live_api.py (:func:`act`,
:func:`select`, :func:`stop`), which calls the addon's ``@addon.live_action``.
"""

import asyncio
import datetime
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from core import clock, notify, users
from core.addons import registry
from core.addons.addon import Addon, get_addon
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

TONES = ("neutral", "good", "warn", "bad", "muted")
# Lucide icon names; the phone app ships each one as a drawable.
ICONS = ("train-front", "tram-front", "bus", "car", "bike", "footprints", "plane", "ship", "timer", "clock",
         "bot", "terminal", "package", "truck", "chef-hat", "utensils", "shopping-cart", "dumbbell", "sprout",
         "droplets", "flame", "bell", "map-pin", "house", "sparkles", "zap")
ACTION_KINDS = ("server", "next", "prev", "stop")
# Action ids a server action may not take: live_action gets them for selections and stops.
RESERVED_ACTIONS = ("select", "stop")

LIVE_PUSH_INTERVAL = 10.0
TICK_SECONDS = 5.0
FINAL_KEEP = datetime.timedelta(minutes=10)
# How long an ended activity's hidden row keeps its rev for a start with the same key.
GONE_KEEP = datetime.timedelta(days=1)
HOOK_TIMEOUT = 30.0
MAX_STATE_BYTES = 16 * 1024
MAX_TITLE, MAX_TEXT, MAX_CONTEXT, MAX_LABEL, MAX_CHIP, MAX_ALERT = 120, 240, 120, 24, 7, 80
MAX_ACTIONS, MAX_CHOICES = 3, 8
# Total of a progress bar without segments.
DEFAULT_PROGRESS_TOTAL = 100
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,99}$")


@dataclass(frozen=True)
class Countdown:
    """A timer counting down to ``to`` (aware), with an optional short ``label`` ("Departs")."""
    to: datetime.datetime
    label: str = ""


@dataclass(frozen=True)
class Seg:
    """A segment of a progress bar: ``length`` (at least 1) units drawn in ``tone``."""
    length: int
    tone: str = "neutral"


@dataclass(frozen=True)
class Point:
    """A marker on a progress bar at unit ``at`` (0 to the bar's total)."""
    at: int
    tone: str = "neutral"


@dataclass(frozen=True)
class Progress:
    """A progress bar: its ``segments`` (the total is the sum of their lengths,
    100 without any), ``points`` on it, the ``value`` reached, ``tracker`` (an
    ICONS name riding the bar at ``value``), ``indeterminate`` (no value
    known), ``styled_by_progress`` (False: every segment drawn filled, the
    colours carry the meaning)."""
    segments: Tuple[Seg, ...] = ()
    points: Tuple[Point, ...] = ()
    value: int = 0
    tracker: str = ""
    indeterminate: bool = False
    styled_by_progress: bool = True


@dataclass(frozen=True)
class Action:
    """A button: ``kind`` ``server`` (calls the addon's ``live_action`` with
    ``id``), ``next`` / ``prev`` (the next / previous choice, handled by the
    client), ``stop`` (ends the activity)."""
    id: str
    label: str
    kind: str = "server"


@dataclass(frozen=True)
class Choice:
    """An alternative the user can switch to (a later train): its display
    fields override the activity's while it is selected (its own choices and
    actions are ignored)."""
    key: str
    activity: "Activity"


@dataclass(frozen=True)
class Activity:
    """What a live activity shows (server/docs/live.md, "The activity").

    ``countdown`` and ``since`` (a timer counting up) exclude each other;
    ``chip`` (up to 7 characters) replaces the default status-bar chip text;
    ``selected`` is a choice key ("" = the first); ``open`` an https URL or a
    Dana path ("/israel_rail"); ``alert`` a key: an update alerts only the
    first time each non-empty value is seen; ``ends_at``: it expires by
    itself; ``refresh_at``: when the addon's ``live_refresh`` should run next."""
    title: str
    text: str = ""
    context: str = ""
    icon: str = ""
    tone: str = "neutral"
    countdown: Optional[Countdown] = None
    since: Optional[datetime.datetime] = None
    chip: str = ""
    progress: Optional[Progress] = None
    actions: Tuple[Action, ...] = ()
    choices: Tuple[Choice, ...] = ()
    selected: str = ""
    open: str = ""
    alert: str = ""
    ends_at: Optional[datetime.datetime] = None
    refresh_at: Optional[datetime.datetime] = None


@dataclass(frozen=True)
class Final:
    """What an ended activity leaves behind for a few minutes ("Arrived")."""
    title: str
    text: str = ""
    tone: str = "neutral"
    open: str = ""


@dataclass(frozen=True)
class Current:
    """A stored live activity, as hooks get it: ``id`` is ``<addon>:<key>``,
    ``selected`` the choice the user is on, ``rev`` its change count;
    ``ended_at`` / ``final`` only for an ended one kept for its final."""
    id: str
    addon: str
    key: str
    activity: Activity
    selected: str
    rev: int
    started_at: datetime.datetime
    updated_at: datetime.datetime
    ended_at: Optional[datetime.datetime] = None
    final: Optional[Final] = None


class _End:
    def __repr__(self) -> str:
        return "live.END"


# A hook returns it to end the activity.
END = _End()
# What live_refresh / live_action return: an Activity (update), END (end it) or None (leave it).
HookResult = Union[Activity, _End, None]


class LiveError(ValueError):
    """An activity, final or key Dana refuses; the message says what."""


class UnknownAction(ValueError):
    """``act`` named an action the activity does not have."""


class UnknownChoice(ValueError):
    """``select`` / ``act`` named a choice the activity does not have."""


# --- Validation --------------------------------------------------------------------------

def _words(what: str, value: Any, limit: int, *, required: bool = False) -> None:
    if not isinstance(value, str):
        raise LiveError(f"{what} must be text")
    if required and not value.strip():
        raise LiveError(f"{what} is required")
    if len(value) > limit:
        raise LiveError(f"{what} is {len(value)} characters; keep it to {limit}")


def _tone(what: str, tone: Any) -> None:
    if tone not in TONES:
        raise LiveError(f"{what} {tone!r} must be one of {', '.join(TONES)}")


def _icon(what: str, icon: Any) -> None:
    if icon != "" and icon not in ICONS:
        raise LiveError(f"{what} {icon!r} is not a live icon (core.live.ICONS)")


def _aware(what: str, value: Any) -> None:
    if not isinstance(value, datetime.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LiveError(f"{what} must be an aware datetime (clock.now() based)")


def _open(what: str, value: Any) -> None:
    _words(what, value, 2000)
    if value and (any(c.isspace() for c in value) or not (
            (value.startswith("https://") and len(value) > len("https://"))
            or (value.startswith("/") and not value.startswith("//")))):
        raise LiveError(f"{what} must be an https:// address or a Dana path starting with /")


def _progress(p: Any) -> None:
    if not isinstance(p, Progress):
        raise LiveError("progress must be a Progress")
    for s in p.segments:
        if not isinstance(s, Seg) or isinstance(s.length, bool) or not isinstance(s.length, int) or s.length < 1:
            raise LiveError("every progress segment is a Seg with a length of at least 1")
        _tone("a segment's tone", s.tone)
    total = sum(s.length for s in p.segments) or DEFAULT_PROGRESS_TOTAL
    for pt in p.points:
        if not isinstance(pt, Point) or isinstance(pt.at, bool) or not isinstance(pt.at, int) or not 0 <= pt.at <= total:
            raise LiveError(f"every progress point is a Point at 0..{total}")
        _tone("a point's tone", pt.tone)
    if isinstance(p.value, bool) or not isinstance(p.value, int) or not 0 <= p.value <= total:
        raise LiveError(f"progress value must be 0..{total}")
    _icon("progress tracker", p.tracker)


def _display_check(a: Any, owner: str) -> None:
    if not isinstance(a, Activity):
        raise LiveError(f"{owner} must be an Activity")
    _words(f"{owner} title", a.title, MAX_TITLE, required=True)
    _words(f"{owner} text", a.text, MAX_TEXT)
    _words(f"{owner} context", a.context, MAX_CONTEXT)
    _words(f"{owner} chip", a.chip, MAX_CHIP)
    _tone(f"{owner} tone", a.tone)
    _icon(f"{owner} icon", a.icon)
    if a.countdown is not None and a.since is not None:
        raise LiveError(f"{owner}: countdown and since exclude each other")
    if a.countdown is not None:
        if not isinstance(a.countdown, Countdown):
            raise LiveError(f"{owner} countdown must be a Countdown")
        _aware(f"{owner} countdown.to", a.countdown.to)
        _words(f"{owner} countdown label", a.countdown.label, MAX_LABEL)
    if a.since is not None:
        _aware(f"{owner} since", a.since)
    if a.progress is not None:
        _progress(a.progress)
    _open(f"{owner} open", a.open)


def _check(a: Any) -> None:
    """Refuse an activity Dana cannot show (:class:`LiveError` says why)."""
    _display_check(a, "the activity")
    actions, choices = tuple(a.actions), tuple(a.choices)
    if len(actions) > MAX_ACTIONS:
        raise LiveError(f"an activity has at most {MAX_ACTIONS} actions")
    for act in actions:
        if not isinstance(act, Action):
            raise LiveError("actions must be Action")
        _words("an action id", act.id, 40, required=True)
        _words("an action label", act.label, MAX_LABEL, required=True)
        if act.kind not in ACTION_KINDS:
            raise LiveError(f"action kind {act.kind!r} must be one of {', '.join(ACTION_KINDS)}")
        if act.kind == "server" and act.id in RESERVED_ACTIONS:
            raise LiveError(f"a server action may not be called {act.id!r}")
    if len({act.id for act in actions}) != len(actions):
        raise LiveError("action ids must be unique")
    if sum(1 for act in actions if act.kind == "stop") > 1:
        raise LiveError("an activity has at most one stop action")
    if len(choices) > MAX_CHOICES:
        raise LiveError(f"an activity has at most {MAX_CHOICES} choices")
    for c in choices:
        if not isinstance(c, Choice):
            raise LiveError("choices must be Choice")
        _words("a choice key", c.key, 80, required=True)
        _display_check(c.activity, f"choice {c.key!r}")
    if len({c.key for c in choices}) != len(choices):
        raise LiveError("choice keys must be unique")
    if a.selected and a.selected not in {c.key for c in choices}:
        raise LiveError(f"selected {a.selected!r} is not a choice key")
    _words("alert", a.alert, MAX_ALERT)
    for what in ("ends_at", "refresh_at"):
        if getattr(a, what) is not None:
            _aware(what, getattr(a, what))
    size = len(json.dumps(_store_dict(a), ensure_ascii=False).encode("utf-8"))
    if size > MAX_STATE_BYTES:
        raise LiveError(f"the activity is {size} bytes as JSON; keep it to {MAX_STATE_BYTES}")


def _check_final(f: Any) -> None:
    if not isinstance(f, Final):
        raise LiveError("final must be a Final")
    _words("final title", f.title, MAX_TITLE, required=True)
    _words("final text", f.text, MAX_TEXT)
    _tone("final tone", f.tone)
    _open("final open", f.open)


def _check_key(key: Any) -> None:
    if not isinstance(key, str) or not KEY.match(key):
        raise LiveError("key must be 1-100 letters, digits or _ . : @ -, starting with a letter or digit")


# --- Shapes ------------------------------------------------------------------------------

def _wire_time(dt: datetime.datetime) -> str:
    """ISO 8601 in the user's zone, to the second."""
    return dt.astimezone(clock.get_timezone()).isoformat(timespec="seconds")


def _db_time(dt: datetime.datetime) -> str:
    """UTC with microseconds: compares as text."""
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="microseconds")


def _parse_time(raw: Optional[str]) -> Optional[datetime.datetime]:
    return datetime.datetime.fromisoformat(raw) if raw else None


def _time(dt: datetime.datetime, wire: bool) -> str:
    return _wire_time(dt) if wire else dt.isoformat()


def _display(a: Activity, wire: bool) -> Dict[str, Any]:
    """The display fields of an activity (also a choice's), empty ones left out (tone always there)."""
    d: Dict[str, Any] = {"title": a.title}
    for name in ("text", "context", "icon"):
        if getattr(a, name):
            d[name] = getattr(a, name)
    d["tone"] = a.tone
    if a.countdown is not None:
        d["countdown"] = {"to": _time(a.countdown.to, wire), **({"label": a.countdown.label} if a.countdown.label else {})}
    if a.since is not None:
        d["since"] = _time(a.since, wire)
    if a.chip:
        d["chip"] = a.chip
    if a.progress is not None:
        p = a.progress
        prog: Dict[str, Any] = {}
        if p.segments:
            prog["segments"] = [{"length": s.length, "tone": s.tone} for s in p.segments]
        if p.points:
            prog["points"] = [{"at": pt.at, "tone": pt.tone} for pt in p.points]
        prog["value"] = p.value
        if p.tracker:
            prog["tracker"] = p.tracker
        prog["indeterminate"] = p.indeterminate
        prog["styled_by_progress"] = p.styled_by_progress
        d["progress"] = prog
    if a.open:
        d["open"] = a.open
    return d


def _body(a: Activity, wire: bool, selected: str) -> Dict[str, Any]:
    """The activity's fields in the wire order; ``selected`` written when there are choices."""
    d = _display(a, wire)
    opened = d.pop("open", "")
    if a.actions:
        d["actions"] = [{"id": x.id, "label": x.label, "kind": x.kind} for x in a.actions]
    if a.choices:
        d["choices"] = [{"key": c.key, **_display(c.activity, wire)} for c in a.choices]
        if selected:
            d["selected"] = selected
    if opened:
        d["open"] = opened
    if a.alert:
        d["alert"] = a.alert
    if a.ends_at is not None:
        d["ends_at"] = _time(a.ends_at, wire)
    if not wire and a.refresh_at is not None:
        d["refresh_at"] = a.refresh_at.isoformat()
    return d


def _store_dict(a: Activity) -> Dict[str, Any]:
    """The activity as stored (the addon's own ``selected``, ``refresh_at`` kept)."""
    return _body(a, False, a.selected)


def _view(a: Activity, selected: str) -> Dict[str, Any]:
    """What clients see of an activity (no id, rev or times): equal views are a no-op update."""
    return _body(a, True, selected)


def _display_from(d: Dict[str, Any], **more: Any) -> Activity:
    cd = d.get("countdown")
    pg = d.get("progress")
    return Activity(
        title=d["title"], text=d.get("text", ""), context=d.get("context", ""), icon=d.get("icon", ""),
        tone=d.get("tone", "neutral"),
        countdown=Countdown(_parse_time(cd["to"]), cd.get("label", "")) if cd else None,  # type: ignore[arg-type]
        since=_parse_time(d.get("since")), chip=d.get("chip", ""),
        progress=Progress(segments=tuple(Seg(s["length"], s["tone"]) for s in pg.get("segments", ())),
                          points=tuple(Point(p["at"], p["tone"]) for p in pg.get("points", ())),
                          value=pg.get("value", 0), tracker=pg.get("tracker", ""),
                          indeterminate=pg.get("indeterminate", False),
                          styled_by_progress=pg.get("styled_by_progress", True)) if pg else None,
        open=d.get("open", ""), **more)


def _activity_from(d: Dict[str, Any]) -> Activity:
    return _display_from(
        d, actions=tuple(Action(x["id"], x["label"], x["kind"]) for x in d.get("actions", ())),
        choices=tuple(Choice(c["key"], _display_from(c)) for c in d.get("choices", ())),
        selected=d.get("selected", ""), alert=d.get("alert", ""), ends_at=_parse_time(d.get("ends_at")),
        refresh_at=_parse_time(d.get("refresh_at")))


def _final_dict(f: Final) -> Dict[str, Any]:
    d: Dict[str, Any] = {"title": f.title}
    if f.text:
        d["text"] = f.text
    d["tone"] = f.tone
    if f.open:
        d["open"] = f.open
    return d


def _source(addon: str) -> str:
    try:
        return get_addon(addon).title
    except KeyError:
        return addon


def to_json(c: Current) -> Dict[str, Any]:
    """The wire form of a live activity (``GET /api/live``, the ``live`` push; docs/live.md, "Wire JSON")."""
    out: Dict[str, Any] = {"id": c.id, "addon": c.addon, "source": _source(c.addon), "rev": c.rev,
                           "updated_at": _wire_time(c.updated_at), "started_at": _wire_time(c.started_at)}
    out.update(_view(c.activity, c.selected))
    if c.ended_at is not None:
        out["ended"] = True
        if c.final is not None:
            out["final"] = _final_dict(c.final)
    return out


# --- Storage -----------------------------------------------------------------------------

def _resolve(stored: str, a: Activity) -> str:
    """The choice the user is on: the stored one while the choices still have
    it, else the activity's ``selected``, else the first ("" without choices)."""
    keys = [c.key for c in a.choices]
    if not keys:
        return ""
    if stored in keys:
        return stored
    return a.selected if a.selected in keys else keys[0]


def _current(row: sqlite3.Row) -> Current:
    final = json.loads(row["final"]) if row["final"] else None
    return Current(id=f"{row['addon']}:{row['key']}", addon=row["addon"], key=row["key"],
                   activity=_activity_from(json.loads(row["state"])), selected=row["selected"], rev=int(row["rev"]),
                   started_at=_parse_time(row["started_at"]), updated_at=_parse_time(row["updated_at"]),  # type: ignore[arg-type]
                   ended_at=_parse_time(row["ended_at"]),
                   final=Final(final["title"], final.get("text", ""), final.get("tone", "neutral"),
                               final.get("open", "")) if final else None)


def _row(user: User, addon: str, key: str, *, running: bool = True) -> Optional[sqlite3.Row]:
    with get_db_connection() as conn:
        return conn.execute("SELECT * FROM live_activities WHERE user_id = ? AND addon = ? AND key = ?"
                            + (" AND ended_at IS NULL" if running else ""), (user.id, addon, key)).fetchone()


def _refresh_every(addon: str) -> Optional[float]:
    """The ``every`` of the addon's ``live_refresh`` (None: it has none)."""
    try:
        declared = get_addon(addon)
    except KeyError:
        return None
    hook = next((h for h in declared.hooks if h.kind == "live_refresh"), None)
    return float(hook.option("every", 60)) if hook is not None else None


def _next_refresh(addon: str, a: Activity, refreshed: datetime.datetime) -> Optional[str]:
    """When the addon's ``live_refresh`` is due next (DB time; None: never):
    the activity's ``refresh_at`` when it is later than the last call, else
    ``every`` seconds after it."""
    every = _refresh_every(addon)
    if every is None:
        return None
    if a.refresh_at is not None and a.refresh_at > refreshed:
        return _db_time(a.refresh_at)
    return _db_time(refreshed + datetime.timedelta(seconds=every))


def get(user: User, addon: Addon, key: str) -> Optional[Current]:
    """``user``'s running activity of ``addon`` under ``key`` (None: not running)."""
    row = _row(user, addon.name, key)
    return _current(row) if row else None


def active(user: User) -> List[Current]:
    """``user``'s running activities, newest-updated first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM live_activities WHERE user_id = ? AND ended_at IS NULL "
                            "ORDER BY updated_at DESC", (user.id,)).fetchall()
    return [_current(r) for r in rows]


def listed(user: User) -> List[Current]:
    """What ``user``'s clients get: the running activities and the ended ones
    kept for their final, newest-updated first (never the gone rows)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM live_activities WHERE user_id = ? "
                            "AND (ended_at IS NULL OR final IS NOT NULL) ORDER BY updated_at DESC",
                            (user.id,)).fetchall()
    return [_current(r) for r in rows]


def payload(user: User) -> Dict[str, Any]:
    """``GET /api/live`` and the ``live`` push: ``{"activities": [to_json(...), ...]}``."""
    return {"activities": [to_json(c) for c in listed(user)]}


async def start(user: User, addon: Addon, key: str, activity: Activity) -> Current:
    """Start ``addon``'s live activity ``key`` for ``user`` (replacing one with
    the same key), pushed at once. Its ``rev`` carries on from the last one
    under that key (running, ended or gone), so it always rises.

    Raises:
        LiveError: an activity or key Dana refuses.
    """
    _check_key(key)
    _check(activity)
    now = clock.now()
    old = _row(user, addon.name, key, running=False)
    rev = int(old["rev"]) + 1 if old else 1
    selected = _resolve("", activity)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO live_activities (user_id, addon, key, state, selected, rev, alert_seen, started_at, "
            "updated_at, refreshed_at, refresh_at, ends_at, ended_at, final) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL) "
            "ON CONFLICT(user_id, addon, key) DO UPDATE SET state = excluded.state, selected = excluded.selected, "
            "rev = excluded.rev, alert_seen = excluded.alert_seen, started_at = excluded.started_at, "
            "updated_at = excluded.updated_at, refreshed_at = excluded.refreshed_at, "
            "refresh_at = excluded.refresh_at, ends_at = excluded.ends_at, ended_at = NULL, final = NULL",
            (user.id, addon.name, key, json.dumps(_store_dict(activity), ensure_ascii=False), selected, rev,
             activity.alert, _db_time(now), _db_time(now), _db_time(now), _next_refresh(addon.name, activity, now),
             _db_time(activity.ends_at) if activity.ends_at else None))
        conn.commit()
    _queue_push(user, urgent=True)
    return Current(id=f"{addon.name}:{key}", addon=addon.name, key=key, activity=activity, selected=selected,
                   rev=rev, started_at=now, updated_at=now)


async def _update(user: User, addon: str, key: str, activity: Activity) -> Optional[Current]:
    _check(activity)
    row = _row(user, addon, key)
    if row is None:
        return None
    old = _current(row)
    selected = _resolve(row["selected"], activity)
    state = json.dumps(_store_dict(activity), ensure_ascii=False)
    refresh_at = _next_refresh(addon, activity, _parse_time(row["refreshed_at"]))  # type: ignore[arg-type]
    if _view(old.activity, old.selected) == _view(activity, selected):
        with get_db_connection() as conn:  # nothing a client sees changed: no rev, no push
            conn.execute("UPDATE live_activities SET state = ?, refresh_at = ? WHERE user_id = ? AND addon = ? "
                         "AND key = ? AND ended_at IS NULL", (state, refresh_at, user.id, addon, key))
            conn.commit()
        return replace(old, activity=activity)
    now = clock.now()
    new_alert = bool(activity.alert) and activity.alert != row["alert_seen"]
    alert_seen = activity.alert if new_alert else row["alert_seen"]
    with get_db_connection() as conn:
        conn.execute("UPDATE live_activities SET state = ?, selected = ?, rev = rev + 1, alert_seen = ?, "
                     "updated_at = ?, refresh_at = ?, ends_at = ? WHERE user_id = ? AND addon = ? AND key = ? "
                     "AND ended_at IS NULL",
                     (state, selected, alert_seen, _db_time(now), refresh_at,
                      _db_time(activity.ends_at) if activity.ends_at else None, user.id, addon, key))
        conn.commit()
    _queue_push(user, urgent=new_alert or selected != old.selected)
    return replace(old, activity=activity, selected=selected, rev=old.rev + 1, updated_at=now)


async def update(user: User, addon: Addon, key: str, activity: Activity) -> Optional[Current]:
    """Show ``activity`` for ``user``'s running activity ``key`` of ``addon``.
    One that changes nothing on the wire is dropped (no rev, no push). None
    when it is not running.

    Raises:
        LiveError: an activity Dana refuses.
    """
    return await _update(user, addon.name, key, activity)


async def _end(user: User, addon: str, key: str, final: Optional[Final]) -> bool:
    if final is not None:
        _check_final(final)
    now = _db_time(clock.now())
    with get_db_connection() as conn:  # without a final the row stays hidden (gone), keeping its rev
        cur = conn.execute("UPDATE live_activities SET ended_at = ?, final = ?, rev = rev + 1, updated_at = ?, "
                           "refresh_at = NULL WHERE user_id = ? AND addon = ? AND key = ? AND ended_at IS NULL",
                           (now, json.dumps(_final_dict(final), ensure_ascii=False) if final is not None else None,
                            now, user.id, addon, key))
        conn.commit()
    if not cur.rowcount:
        return False
    _queue_push(user, urgent=True)
    return True


async def end(user: User, addon: Addon, key: str, final: Optional[Final] = None) -> bool:
    """End ``user``'s running activity ``key`` of ``addon``, pushed at once.
    With a ``final`` it stays listed (ended) for :data:`FINAL_KEEP`, else it
    goes at once. False when it was not running.

    Raises:
        LiveError: a final Dana refuses.
    """
    return await _end(user, addon.name, key, final)


# --- Acting (core/live_api.py) -----------------------------------------------------------

def _split(live_id: str) -> Tuple[str, str]:
    addon, _, key = (live_id or "").partition(":")
    return addon, key


def find(user: User, live_id: str) -> Optional[Current]:
    """``user``'s running activity with wire id ``<addon>:<key>`` (None: none of theirs)."""
    addon, key = _split(live_id)
    row = _row(user, addon, key) if addon and key else None
    return _current(row) if row else None


async def _apply(user: User, c: Current, result: Any, label: str) -> None:
    """A hook's answer: an Activity updates ``c``, END ends it, None leaves it."""
    if result is None:
        return
    if result is END:
        await _end(user, c.addon, c.key, None)
    elif isinstance(result, Activity):
        try:
            await _update(user, c.addon, c.key, result)
        except LiveError as e:
            logger.error(f"{label} of {c.addon} returned an activity Dana refuses ({e}); kept the last one")
    else:
        logger.error(f"{label} of {c.addon} returned {type(result).__name__}, not an Activity, live.END or None")


async def _call(user: User, kind: str, c: Current, *args: Any) -> Tuple[bool, Any]:
    """Call ``c``'s addon's hook of ``kind`` as ``user``: (whether it ran, its answer)."""
    hook = registry.addon_hook(kind, c.addon, user)
    if hook is None:
        return False, None
    try:
        with users.acting_as(user):
            return True, await asyncio.wait_for(hook.func(user, c, *args), timeout=HOOK_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"{kind} of {c.addon} failed for user {user.id} ({c.key}); the activity goes on")
        return False, None


async def _action(user: User, c: Current, action: str, choice: str) -> Optional[Current]:
    ran, result = await _call(user, "live_action", c, action, choice)
    if ran:
        await _apply(user, c, result, "live_action")
    return find(user, c.id)


async def select(user: User, live_id: str, choice: str) -> Optional[Current]:
    """The user switched ``live_id`` to ``choice``: stored (pushed at once when
    it changed), then the addon's ``live_action`` gets ``"select"``. Returns
    the activity after it (None: the addon ended it).

    Raises:
        LookupError: no running activity of ``user``'s with that id.
        UnknownChoice: the activity has no such choice.
    """
    c = find(user, live_id)
    if c is None:
        raise LookupError(live_id)
    if choice not in {x.key for x in c.activity.choices}:
        raise UnknownChoice(choice)
    if choice == c.selected:
        return c
    now = clock.now()
    with get_db_connection() as conn:
        conn.execute("UPDATE live_activities SET selected = ?, rev = rev + 1, updated_at = ? WHERE user_id = ? "
                     "AND addon = ? AND key = ? AND ended_at IS NULL", (choice, _db_time(now), user.id, c.addon, c.key))
        conn.commit()
    _queue_push(user, urgent=True)
    return await _action(user, replace(c, selected=choice, rev=c.rev + 1, updated_at=now), "select", choice)


async def act(user: User, live_id: str, action: str, choice: Optional[str] = None) -> Optional[Current]:
    """Press the activity's action ``action``: ``stop`` ends it, ``next`` /
    ``prev`` select the next / previous choice, a ``server`` one goes to the
    addon's ``live_action`` (with ``choice``, default the selected one).
    Returns the activity after it (None: ended).

    Raises:
        LookupError: no running activity of ``user``'s with that id.
        UnknownAction: the activity has no such action.
        UnknownChoice: ``choice`` is not one of its choices.
    """
    c = find(user, live_id)
    if c is None:
        raise LookupError(live_id)
    pressed = next((x for x in c.activity.actions if x.id == action), None)
    if pressed is None:
        raise UnknownAction(action)
    keys = [x.key for x in c.activity.choices]
    if choice is not None and choice not in keys:
        raise UnknownChoice(choice)
    if pressed.kind == "stop":
        await stop(user, live_id)
        return None
    if pressed.kind in ("next", "prev"):
        if not keys:
            return c
        at = keys.index(c.selected) if c.selected in keys else 0
        at = min(len(keys) - 1, at + 1) if pressed.kind == "next" else max(0, at - 1)
        return await select(user, live_id, keys[at])
    return await _action(user, c, pressed.id, choice if choice is not None else c.selected)


async def stop(user: User, live_id: str) -> bool:
    """The user stopped ``live_id``: it ends (pushed at once), then the
    addon's ``live_action`` gets ``"stop"`` to clean up. An ended one kept for
    its final is dismissed (pushed at once, so the other clients drop it).
    False when ``user`` has no such activity."""
    addon, key = _split(live_id)
    row = _row(user, addon, key, running=False) if addon and key else None
    if row is None or (row["ended_at"] is not None and row["final"] is None):
        return False
    c = _current(row)
    if c.ended_at is not None:
        with get_db_connection() as conn:
            conn.execute("UPDATE live_activities SET final = NULL, rev = rev + 1, updated_at = ? WHERE user_id = ? "
                         "AND addon = ? AND key = ? AND final IS NOT NULL",
                         (_db_time(clock.now()), user.id, addon, key))
            conn.commit()
        _queue_push(user, urgent=True)
        return True
    await _end(user, addon, key, None)
    await _call(user, "live_action", c, "stop", c.selected)
    return True


# --- Pushes ------------------------------------------------------------------------------

_last_push: Dict[str, datetime.datetime] = {}
_pending: Dict[str, User] = {}
_locks: Dict[str, asyncio.Lock] = {}
_tasks: Set["asyncio.Task[None]"] = set()


def _queue_push(user: User, urgent: bool) -> None:
    """Push ``user``'s activities now (``urgent``, or the interval passed), else once it has."""
    now = clock.now()
    last = _last_push.get(user.id)
    if not urgent and last is not None and (now - last).total_seconds() < LIVE_PUSH_INTERVAL:
        _pending[user.id] = user
        return
    _pending.pop(user.id, None)
    _last_push[user.id] = now
    task = asyncio.get_running_loop().create_task(_push(user), name=f"live-push-{user.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _push(user: User) -> None:
    lock = _locks.setdefault(user.id, asyncio.Lock())
    async with lock:  # one at a time per user: each carries the state at its turn
        try:
            await notify.send(user, notify.Message(kind="live", data=payload(user), replace=True))
        except Exception:
            logger.exception(f"Live push for user {user.id} failed")


async def drain() -> None:
    """Wait for the pushes and the refreshes under way, and the pushes they queue (tests)."""
    while _tasks or _inflight:
        await asyncio.gather(*list(_tasks), *list(_inflight.values()), return_exceptions=True)


def _flush() -> int:
    """Send the trailing pushes whose interval passed; how many."""
    now = clock.now()
    due = [u for uid, u in list(_pending.items())
           if uid not in _last_push or (now - _last_push[uid]).total_seconds() >= LIVE_PUSH_INTERVAL]
    for user in due:
        _queue_push(user, urgent=True)
    return len(due)


# --- The loop ----------------------------------------------------------------------------

async def _expire(now: datetime.datetime) -> int:
    with get_db_connection() as conn:
        # scoped: the server-wide expiry pass; each row is ended for its own user below
        rows = conn.execute("SELECT user_id, addon, key FROM live_activities WHERE ended_at IS NULL "
                            "AND ends_at IS NOT NULL AND ends_at <= ?", (_db_time(now),)).fetchall()
    ended = 0
    for r in rows:
        user = users.get(r["user_id"])
        if user is not None and await _end(user, r["addon"], r["key"], None):
            ended += 1
    return ended


def _purge(now: datetime.datetime) -> int:
    """Finals older than FINAL_KEEP leave the list (their rows stay, gone); gone
    rows older than GONE_KEEP are deleted. How many finals left."""
    with get_db_connection() as conn:
        # scoped: server-wide retention of ended activities (FINAL_KEEP listed, GONE_KEEP for their rev)
        cur = conn.execute("UPDATE live_activities SET final = NULL WHERE ended_at IS NOT NULL "
                           "AND final IS NOT NULL AND ended_at <= ?", (_db_time(now - FINAL_KEEP),))
        # scoped: server-wide retention of the gone rows kept for their rev (GONE_KEEP)
        conn.execute("DELETE FROM live_activities WHERE ended_at IS NOT NULL AND final IS NULL AND ended_at <= ?",
                     (_db_time(now - GONE_KEEP),))
        conn.commit()
    return cur.rowcount


# The refreshes under way, by (user id, addon, key): never two at once for one activity.
_inflight: Dict[Tuple[str, str, str], "asyncio.Task[None]"] = {}


async def _refresh(user: User, c: Current) -> None:
    """One due ``live_refresh``, in a task of its own: its answer applied."""
    try:
        ran, result = await _call(user, "live_refresh", c)
        if ran:
            await _apply(user, c, result, "live_refresh")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"A live refresh of {c.addon} failed for user {user.id} ({c.key})")


def _refresh_due(now: datetime.datetime) -> int:
    """Start the due ``live_refresh`` calls as background tasks, each marked
    first (a slow or failing one is not called again at once) and skipped
    while the last one for that activity is still running. How many started."""
    with get_db_connection() as conn:
        # scoped: the server-wide refresh pass; each row's hook runs as its own user below
        rows = conn.execute("SELECT * FROM live_activities WHERE ended_at IS NULL AND refresh_at IS NOT NULL "
                            "AND refresh_at <= ? ORDER BY refresh_at", (_db_time(now),)).fetchall()
    started = 0
    loop = asyncio.get_running_loop()
    for r in rows:
        k = (r["user_id"], r["addon"], r["key"])
        user = users.get(r["user_id"])
        if k in _inflight or user is None:
            continue
        c = _current(r)
        with get_db_connection() as conn:
            conn.execute("UPDATE live_activities SET refreshed_at = ?, refresh_at = ? WHERE user_id = ? "
                         "AND addon = ? AND key = ? AND ended_at IS NULL",
                         (_db_time(now), _next_refresh(c.addon, c.activity, now), user.id, c.addon, c.key))
            conn.commit()
        task = loop.create_task(_refresh(user, c), name=f"live-refresh-{c.addon}-{user.id}")
        _inflight[k] = task
        task.add_done_callback(lambda t, k=k: _inflight.pop(k) if _inflight.get(k) is t else None)
        started += 1
    return started


async def tick() -> Dict[str, int]:
    """One pass of the loop: expire, purge, start the due refreshes (not
    awaited: :func:`drain` waits for them), trailing pushes."""
    now = clock.now()
    return {"expired": await _expire(now), "purged": _purge(now), "refreshed": _refresh_due(now),
            "pushed": _flush()}


async def run_loop() -> None:
    """Tick every :data:`TICK_SECONDS` for as long as the server runs (core/server.py)."""
    try:
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Live activities: a pass failed; the loop goes on")
            await asyncio.sleep(TICK_SECONDS)
    finally:  # the server is stopping: the refreshes under way stop with it
        for task in list(_inflight.values()):
            task.cancel()
