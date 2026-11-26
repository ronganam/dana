"""What the watch engine did, in memory since the server started, per user:
the last :data:`KEEP` pipeline steps, counters, and live subscribers (the
``/api/watches/pipeline/stream`` SSE). Nothing here is stored.

A PipelineEvent is ``{seq, at, stage, event_type, event_id, summary, watch_id,
watch_title, trigger_id, result, candidates, us, cost_usd, action_type}`` (``action_type`` on ``action`` steps) with ``stage`` one
of :data:`STAGES` (docs/watches.md, "Pipeline").
"""

import asyncio
import collections
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set

from core import clock

STAGES = ("event", "filter", "semantic", "fire", "action", "time", "deadline", "evidence")
KEEP = 200
QUEUE_SIZE = 500


@dataclass
class _UserLog:
    since: str
    recent: Deque[Dict[str, Any]] = field(default_factory=lambda: collections.deque(maxlen=KEEP))
    seq: int = 0
    events_in: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    filter_pass: int = 0
    filter_runs: int = 0
    filter_us: float = 0.0
    semantic_checks: int = 0
    semantic_yes: int = 0
    fires: int = 0
    actions: Dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    clock: int = 0
    queues: Set["asyncio.Queue[Dict[str, Any]]"] = field(default_factory=set)


_logs: Dict[str, _UserLog] = {}


def _log(user_id: str) -> _UserLog:
    log = _logs.get(user_id)
    if log is None:
        log = _logs[user_id] = _UserLog(since=clock.now().isoformat(timespec="seconds"))
    return log


def record(user_id: str, stage: str, *, event_type: Optional[str] = None, event_id: Optional[str] = None,
           summary: str = "", watch_id: Optional[str] = None, watch_title: Optional[str] = None,
           trigger_id: Optional[str] = None, result: Optional[str] = None, candidates: Optional[int] = None,
           us: Optional[float] = None, cost_usd: Optional[float] = None, action: str = "") -> Dict[str, Any]:
    """Record one pipeline step of ``user_id`` (counted, kept, sent to their subscribers); returns it.
    ``action``: the action type of an ``action`` step (its ``action_type``, counted per type)."""
    log = _log(user_id)
    log.seq += 1
    ev = {"seq": log.seq, "at": clock.now().isoformat(timespec="seconds"), "stage": stage,
          "event_type": event_type, "event_id": event_id, "summary": summary[:120], "watch_id": watch_id,
          "watch_title": watch_title, "trigger_id": trigger_id, "result": result, "candidates": candidates,
          "us": round(us, 1) if us is not None else None,
          "cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
          "action_type": (action or None) if stage == "action" else None}
    if stage == "event":
        log.events_in += 1
        if event_type:
            log.by_type[event_type] = log.by_type.get(event_type, 0) + 1
    elif stage == "filter" and us is not None:
        log.filter_runs += 1
        log.filter_us += us
        if result == "pass":
            log.filter_pass += 1
    elif stage in ("semantic", "evidence") and result in ("yes", "no"):
        log.semantic_checks += 1
        log.semantic_yes += result == "yes"
    elif stage in ("time", "deadline"):
        log.clock += 1
    elif stage == "fire":
        log.fires += 1
    elif stage == "action" and action:
        log.actions[action] = log.actions.get(action, 0) + 1
    if cost_usd:
        log.cost_usd += cost_usd
    log.recent.append(ev)
    for q in list(log.queues):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass
    return ev


def count_unwatched(user_id: str, event_type: str) -> None:
    """Count an event no active watch listens to, without a step in the log or the stream (a busy home's
    sensors would otherwise crowd out the steps that matter)."""
    log = _log(user_id)
    log.events_in += 1
    log.by_type[event_type] = log.by_type.get(event_type, 0) + 1


def stats(user_id: str) -> Dict[str, Any]:
    """The counters of ``user_id`` since the server started."""
    log = _log(user_id)
    return {"since": log.since, "events_in": log.events_in, "by_type": dict(log.by_type),
            "filter_pass": log.filter_pass, "semantic_checks": log.semantic_checks,
            "semantic_yes": log.semantic_yes, "fires": log.fires, "actions": dict(log.actions),
            "avg_filter_us": round(log.filter_us / log.filter_runs, 1) if log.filter_runs else 0.0,
            "cost_usd": round(log.cost_usd, 6), "clock": log.clock}


def recent(user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """The last ``limit`` steps of ``user_id``, oldest first."""
    items = list(_log(user_id).recent)
    return items[-limit:]


def subscribe(user_id: str) -> "asyncio.Queue[Dict[str, Any]]":
    """A queue that gets every new step of ``user_id`` (call :func:`unsubscribe` when done)."""
    q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=QUEUE_SIZE)
    _log(user_id).queues.add(q)
    return q


def unsubscribe(user_id: str, q: "asyncio.Queue[Dict[str, Any]]") -> None:
    """Stop sending steps to ``q``."""
    _log(user_id).queues.discard(q)


def reset() -> None:
    """Forget everything (tests)."""
    _logs.clear()
