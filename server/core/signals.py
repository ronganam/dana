"""Signals: facts about a user and the sources they come from (docs/signals.md).

A signal is one kind of fact: ``wake``, ``screen``, ``car``, ``location``,
``sleep``, ``weather``. Its sources are the addons that declare
``@addon.provide("signal.<name>", rank=, source=, label=)``: a plain function
``f(user) -> str`` that says what is missing before that source can deliver
the signal for the user ("" when it can). Ranked lower ``rank`` first. The
shipped sources: Dana's phone app (``phone``, rank 10) for wake, screen, car,
location and sleep; Open-Meteo (essentials, rank 10) for weather. A user has
only the sources of the addons active for them.

Each delivery is recorded per user, signal and source (``signal_sources``:
when, and a short word such as "unlock" or "arrive Home"; the last one only,
never a track). A source is **recent** when it delivered within
:data:`ACTIVE_HOURS`. The **active** source of a signal (the one Dana takes it
from) is the highest-ranked recent one, else the highest-ranked one that is
set up.

Event signals (``wake``, ``car``, ``location``, ``sleep``) go through
:func:`deliver`: an event from a source is ignored while a higher-ranked
source is recent for that user (still recorded), so two sources never both
fire a reminder. An accepted ``car`` / ``location`` event is
emitted as ``core.signal.car`` (``action``: enter | leave, ``source``,
``late``) / ``core.signal.location`` (``action``: arrive | leave, ``target``:
the zone's name, ``source``, ``late``); ``late`` marks an event that happened
more than :data:`LATE_MINUTES` before it arrived (the tasks addon records the
transition but sends no reminder). The wake flow (core/wake.py) delivers
``wake`` and ``sleep`` itself; data signals (``screen``, ``weather``) are read
through their providers (``screen_sessions``, ``weather``) and recorded with
:func:`record` when one answers.
"""

import datetime
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core import clock, events
from core.addons import registry
from core.addons.helpers import parse_time
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

SIGNALS = ("wake", "screen", "car", "location", "sleep", "weather")
# Delivered as events (ranked in deliver); the others are read through providers.
EVENT_SIGNALS = ("wake", "car", "location", "sleep")
# What a missing source means for the user (the self-check names these).
NEEDED = ("wake", "car", "location")
# The provider each data signal is read through.
DATA_PROVIDERS = {"screen": "screen_sessions", "weather": "weather"}
ACTIVE_HOURS = float(os.getenv("SIGNAL_ACTIVE_HOURS", "72"))
LATE_MINUTES = float(os.getenv("SIGNAL_LATE_MINUTES", "30"))
DETAIL_MAX = 80


@dataclass(frozen=True)
class SourceState:
    """One source of a signal for one user, as the overview shows it."""
    source: str
    label: str
    addon: str
    last_at: Optional[datetime.datetime]
    detail: str
    missing: str  # what the source needs first ("" when it can deliver)
    recent: bool

    @property
    def ready(self) -> bool:
        """It delivered recently, or it is set up to."""
        return self.recent or not self.missing


def source_key(signal: str) -> str:
    """The provider key a source of ``signal`` declares."""
    return f"signal.{signal}"


def _utc_iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def _missing(hook: Any, user: User) -> str:
    try:
        value = hook.func(user)
    except Exception:
        logger.warning(f"signal source {hook.key} of {hook.addon} failed for user {user.id}", exc_info=True)
        return ""
    return value if isinstance(value, str) else ""


def sources(user: User, signal: str) -> List[SourceState]:
    """``user``'s sources of ``signal``, highest rank first (only those of
    addons active for them)."""
    hooks = registry.providers(source_key(signal), user)
    if not hooks:
        return []
    with get_db_connection() as conn:
        rows = {r["source"]: r for r in conn.execute(
            "SELECT source, last_at, detail FROM signal_sources WHERE user_id = ? AND signal = ?",
            (user.id, signal)).fetchall()}
    now = datetime.datetime.now(datetime.timezone.utc)
    out: List[SourceState] = []
    seen = set()
    for h in hooks:
        name = registry.source_of(h)
        if name in seen:
            continue
        seen.add(name)
        row = rows.get(name)
        last = parse_time(row["last_at"]) if row else None
        recent = last is not None and (now - last).total_seconds() <= ACTIVE_HOURS * 3600
        out.append(SourceState(name, str(h.option("label", "") or h.addon), h.addon, last,
                               str(row["detail"]) if row else "", _missing(h, user), recent))
    return out


def active(user: User, signal: str) -> Optional[str]:
    """The source Dana takes ``signal`` from for ``user`` now: the
    highest-ranked one that delivered recently, else the highest-ranked one
    that is set up; None when there is none."""
    states = sources(user, signal)
    for s in states:
        if s.recent:
            return s.source
    for s in states:
        if not s.missing:
            return s.source
    return None


def usable(user: User, signal: str) -> bool:
    """``user`` has a source of ``signal`` that delivered recently or is set up."""
    return any(s.ready for s in sources(user, signal))


def record(user: User, signal: str, source: str, detail: str = "",
           at: Optional[datetime.datetime] = None) -> None:
    """Remember that ``source`` delivered ``signal`` for ``user`` (``at``:
    when it happened, default now; an older time never replaces a newer one)."""
    when = _utc_iso(at or datetime.datetime.now(datetime.timezone.utc))
    with get_db_connection() as conn:
        conn.execute("INSERT INTO signal_sources (user_id, signal, source, last_at, detail) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id, signal, source) DO UPDATE SET "
                     "detail = CASE WHEN excluded.last_at >= signal_sources.last_at THEN excluded.detail "
                     "ELSE signal_sources.detail END, "
                     "last_at = MAX(signal_sources.last_at, excluded.last_at)",
                     (user.id, signal, source, when, (detail or "")[:DETAIL_MAX]))
        conn.commit()


def deliver(user: User, signal: str, source: str, *, at: Optional[datetime.datetime] = None, detail: str = "",
            action: str = "", target: str = "") -> bool:
    """An event of ``signal`` (an event signal) for ``user`` from ``source``,
    which happened ``at`` (aware; default now). Recorded either way; True when
    it counts: ``source`` is one of the user's sources of the signal and no
    higher-ranked one is recent. A counted ``car`` / ``location`` event is
    emitted (``core.signal.<signal>``)."""
    if signal not in EVENT_SIGNALS:
        raise ValueError(f"{signal!r} is not an event signal")
    states = sources(user, signal)
    names = [s.source for s in states]
    when = at or datetime.datetime.now(datetime.timezone.utc)
    if source not in names:
        logger.info(f"Signal {signal} from {source} for user {user.id}: not one of their sources; ignored")
        return False
    above = [s for s in states[:names.index(source)] if s.recent]
    record(user, signal, source, detail, when)
    if above:
        logger.info(f"Signal {signal} from {source} for user {user.id}: {above[0].source} is active; ignored")
        return False
    if signal in ("car", "location"):
        late = (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds() > LATE_MINUTES * 60
        data: Dict[str, Any] = {"action": action, "source": source, "late": late}
        if signal == "location":
            data["target"] = target
        events.emit(f"core.signal.{signal}", user, **data)
    return True


def _local_iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.astimezone(clock.get_timezone()).isoformat(timespec="seconds") if dt else None


def overview(user: User) -> List[Dict[str, Any]]:
    """Every signal with ``user``'s sources (the ``signals`` of ``GET /api/signals``)."""
    out = []
    for signal in SIGNALS:
        states = sources(user, signal)
        current = active(user, signal) if states else None
        out.append({"signal": signal, "sources": [
            {"source": s.source, "label": s.label, "active": s.source == current, "last_at": _local_iso(s.last_at),
             "detail": s.detail if s.last_at else s.missing} for s in states]})
    return out


def missing(user: User) -> List[str]:
    """The signals of :data:`NEEDED` ``user`` has no source for yet."""
    return [signal for signal in NEEDED if not usable(user, signal)]
