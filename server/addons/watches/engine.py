"""The live watch engine (docs/watches.md, "Engine").

Events: :func:`on_event` is a ``core.events.tap`` — called for every emitted
event. For a user with the addon and an event in their catalog
(``registry.event_types``) it runs, cheapest first: the structured filter
over that user's active event triggers (microseconds: type, ``during``,
``where``, keywords, ``skip_if_fired``, a reminder trigger that already
fired, the per-source cooldown, the same thing seen from another source),
then one small-model call (purpose ``watch_match``) for every semantic
question and evidence question the candidates raise, then the fires and
their actions. Every stage is reported to :mod:`.pipeline`.

Time: :func:`tick` (a 30 s job per user) fires due time triggers (``at``,
``relative_to``, ``every``), runs ``on_deadline`` when a window ends with no
event fire, and closes watches whose window ended (``expired`` / ``done``).

A watch with ``repeat: once`` is done after its first event fire; a reminder
fires each trigger once and closes when its task is completed
(``tasks.task.completed``) — evidence only suggests closing it.
"""

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

import litellm

from core import clock, events, llm, usage
from core.addons import registry
from core.addons.hooks import EventType
from core.text import untrusted
from core.users import User
from addons.watches import actions, pipeline, store
from addons.watches import spec as S
from addons.watches.addon import ADDON

logger = logging.getLogger(__name__)

# A time occurrence this late (the server was down) still fires; older ones are skipped.
GRACE = dt.timedelta(hours=2)
# The same amount from another event type within this long is the same real-world thing (±1%).
SAME_THING_HOURS = 72
SAME_THING_PCT = 0.01
CATALOG_TTL = 30.0
LOAD_TTL = 600.0
SEMANTIC_OUTPUT_TOKENS = 300

_active: Dict[str, List[Dict[str, Any]]] = {}
_catalogs: Dict[str, Tuple[float, Dict[str, EventType]]] = {}
_loads: Dict[str, Tuple[float, str, Dict[str, Any]]] = {}
_locks: Dict[str, asyncio.Lock] = {}
# Time occurrences already looked at and not fired (skip_if_fired, too late): (watch id, trigger id, time).
_passed: Set[Tuple[str, str, str]] = set()


def _invalidate(user: User) -> None:
    _active.pop(user.id, None)


store.on_change(_invalidate)


def _lock(user: User) -> asyncio.Lock:
    lock = _locks.get(user.id)
    if lock is None:
        lock = _locks[user.id] = asyncio.Lock()
    return lock


def active(user: User) -> List[Dict[str, Any]]:
    """``user``'s active watches (cached until one of their watches changes)."""
    rows = _active.get(user.id)
    if rows is None:
        rows = _active[user.id] = store.list_for(user, [store.ACTIVE])
    return rows


def catalog(user: User) -> Dict[str, EventType]:
    """The event types that can reach ``user``, by name (cached briefly)."""
    hit = _catalogs.get(user.id)
    if hit and time.monotonic() - hit[0] < CATALOG_TTL:
        return hit[1]
    types = {e.name: e for e in registry.event_types(user)}
    _catalogs[user.id] = (time.monotonic(), types)
    return types


def forget_caches() -> None:
    """Drop every cache (tests, after addons change)."""
    _active.clear()
    _catalogs.clear()
    _loads.clear()
    _passed.clear()


def history(user: User, days: int = S.DRY_RUN_DAYS, only: Optional[Set[str]] = None) -> Dict[str, List[S.Event]]:
    """``user``'s past events of the last ``days`` (each type with a history; ``only`` these types)."""
    since = clock.now() - dt.timedelta(days=days)
    types = [e for name, e in catalog(user).items() if only is None or name in only]
    return S.history_events(types, user, since)


def load(user: User, watch: Mapping[str, Any]) -> Dict[str, Any]:
    """A watch's ``load`` (its dry run over the last 14 days; cached for a while)."""
    key = json.dumps(watch["spec"].get("triggers"), sort_keys=True)
    hit = _loads.get(watch["id"])
    if hit and hit[1] == key and time.monotonic() - hit[0] < LOAD_TTL:
        return hit[2]
    types = {t.get("event_type") for t in watch["spec"].get("triggers") or [] if t.get("kind") == "event"}
    value = S.estimate_load(watch["spec"], history(user, only=types)) if types else {}
    _loads[watch["id"]] = (time.monotonic(), key, value)
    return value


def _event_id(data: Mapping[str, Any]) -> str:
    for k in ("notification_id", "transaction_id", "call_id", "task_id"):
        if data.get(k) not in (None, ""):
            return str(data[k])
    return uuid.uuid4().hex[:10]


def _fire_event(fire: Mapping[str, Any]) -> S.Event:
    return S.Event(fire["event_type"], fire["event"] if isinstance(fire["event"], dict) else {},
                   S.parse_local(fire["at"]) or clock.now(), fire["id"])


def _event_fires(fires: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The fires that came from events (not times, deadlines or evidence)."""
    return [f for f in fires if f["event_type"] and f["trigger_id"] not in ("deadline", "evidence")]


# --- Semantic checks ------------------------------------------------------------------


def _json_from(text: str) -> Dict[str, Any]:
    t = (text or "").strip()
    if "{" in t:
        t = t[t.index("{"):t.rindex("}") + 1]
    data = json.loads(t)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _cost(prompt: str) -> Optional[float]:
    try:
        name = llm.model_for("watch_match")
        cin, cout = litellm.cost_per_token(model=name, prompt_tokens=usage.estimate_tokens(prompt),
                                           completion_tokens=SEMANTIC_OUTPUT_TOKENS)
        return float(cin + cout)
    except Exception:
        return None


async def semantic_check(event: S.Event, questions: List[Dict[str, str]]) -> Tuple[Optional[Set[str]], Optional[float]]:
    """One small-model call: which of ``questions`` (``{id, question, request, evidence}``) ``event`` satisfies.
    Returns (the ids answered yes — None when the call failed —, its estimated cost)."""
    if not questions:
        return set(), None
    items = [{"id": q["id"], "question": q["question"], "user_request": q.get("request", "")[:300],
              **({"kind": "evidence: is the task DONE?"} if q.get("evidence") else {})} for q in questions]
    data = {k: (v[:600] if isinstance(v, str) else v) for k, v in event.data.items()}
    prompt = ("You check one event for Dana's watches against yes/no questions. Answer strictly: an id is yes "
              "only when the event clearly satisfies its question. For an evidence question, yes only when the "
              "event shows the task WAS DONE (or is no longer needed), never when it is only talked about.\n"
              f"Questions: {json.dumps(items, ensure_ascii=False)}\n"
              + untrusted(f"a {event.type} event", f"time: {event.ts.strftime('%Y-%m-%d %H:%M')}\n"
                          + json.dumps(data, ensure_ascii=False))
              + '\nReply with JSON only: {"yes": [ids of the questions the event satisfies]}')
    cost = _cost(prompt)
    try:
        text = await llm.complete([{"role": "user", "content": prompt}], purpose="watch_match", max_tokens=2048)
        yes = _json_from(text).get("yes") or []
        return {str(x) for x in yes}, cost
    except Exception as e:
        logger.warning(f"Watch semantic check failed: {e}")
        return None, cost


# --- Events ---------------------------------------------------------------------------


def _skip_reason(watch: Dict[str, Any], t: Dict[str, Any], e: S.Event, fires: List[Dict[str, Any]]) -> str:
    """Why an event trigger that passed its filters does not fire now ("" : it may)."""
    fired = {f["trigger_id"] for f in fires}
    if any(x in fired for x in t.get("skip_if_fired") or []):
        return "skipped: another trigger already fired"
    sp = watch["spec"]
    if watch["kind"] == "reminder" and t["id"] in fired:
        return "this reminder trigger already fired"
    amount = S._num(e.data.get("amount"))
    if amount is not None:
        cutoff = e.ts - dt.timedelta(hours=SAME_THING_HOURS)
        for f in _event_fires(fires):
            prev = _fire_event(f)
            other = S._num(prev.data.get("amount"))
            if (prev.type != e.type and other is not None and prev.ts >= cutoff
                    and abs(other - amount) <= abs(amount) * SAME_THING_PCT):
                return "same as an earlier fire"
    if watch["kind"] == "watch" and sp.get("repeat") == "each":
        cool = dt.timedelta(minutes=int(sp.get("cooldown_minutes") or S.DEFAULT_COOLDOWN))
        src = S.source_of(e)
        for f in _event_fires(fires):
            prev = _fire_event(f)
            if S.source_of(prev) == src and e.ts - prev.ts < cool:
                return "cooldown: the same source fired a moment ago"
    return ""


def _in_window(watch: Mapping[str, Any], now: dt.datetime) -> bool:
    start, end = S.window(watch["spec"])
    return (start is None or start <= now) and (end is None or now <= end)


async def on_event(name: str, user: User, data: Dict[str, Any]) -> None:
    """The ``core.events`` tap: run one event through this user's watches."""
    if name == "tasks.task.completed" and data.get("task_id"):
        close_for_task(user, str(data["task_id"]))
    if data.get("late") is True:
        return  # a car / location signal that arrived late: like the tasks addon, not acted on
    if ADDON not in registry.active_addons(user):
        return
    types = catalog(user)
    if name not in types:
        return
    e = S.Event(name, dict(data), clock.now(), _event_id(data))
    summary = S.summary(name, e.data)
    pipeline.record(user.id, "event", event_type=name, event_id=e.id, summary=summary)
    async with _lock(user):
        fires_todo = await _match(user, e, summary)
    for watch, trigger_id, fire_id in fires_todo:
        await _run_fire(user, watch, watch["spec"].get("on_match") or [], e, trigger_id, fire_id)


async def _match(user: User, e: S.Event, summary: str) -> List[Tuple[Dict[str, Any], str, str]]:
    """Filter, check and record the fires of one event; returns (watch, trigger id, fire id) to run."""
    watches = active(user)
    t0 = time.perf_counter()
    cands: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    evidence: List[Dict[str, Any]] = []
    for w in watches:
        if not _in_window(w, e.ts):
            continue
        sp = w["spec"]
        for t in sp.get("triggers") or []:
            if t.get("kind") == "event" and S.structured_ok(t, e):
                cands.append((w, t))
        if (w["kind"] == "reminder" and sp.get("evidence_question") and e.type in (sp.get("evidence_event_types") or [])
                and S.keywords_ok(sp.get("evidence_keywords") or [], e.data)):
            evidence.append(w)
    us = (time.perf_counter() - t0) * 1e6
    fires_of: Dict[str, List[Dict[str, Any]]] = {}
    kept: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for w, t in cands:
        fires = fires_of.setdefault(w["id"], store.fires(user, w["id"], 200))
        why = _skip_reason(w, t, e, fires)
        if why:
            pipeline.record(user.id, "filter", event_type=e.type, event_id=e.id, summary=why, watch_id=w["id"],
                            watch_title=w["title"], trigger_id=t["id"], result="drop")
            continue
        kept.append((w, t))
    evidence = [w for w in evidence if not any(f["trigger_id"] == "evidence"
                                               for f in store.fires(user, w["id"], 50))]
    pipeline.record(user.id, "filter", event_type=e.type, event_id=e.id, summary=summary,
                    result="pass" if kept or evidence else "drop", candidates=len(kept) + len(evidence), us=us)
    if not kept and not evidence:
        return []
    questions: List[Dict[str, str]] = []
    for i, (w, t) in enumerate(kept):
        if t.get("semantic"):
            questions.append({"id": f"t{i}", "question": t["semantic"], "request": w["request"]})
    for i, w in enumerate(evidence):
        questions.append({"id": f"e{i}", "question": w["spec"]["evidence_question"], "request": w["request"],
                          "evidence": "1"})
    yes: Set[str] = set()
    failed = False
    cost: Optional[float] = None
    if questions:
        got, cost = await semantic_check(e, questions)
        failed = got is None
        yes = got or set()
    share = (cost / len(questions)) if cost and questions else None
    out: List[Tuple[Dict[str, Any], str, str]] = []
    fired_watches: Set[str] = set()
    for i, (w, t) in enumerate(kept):
        if t.get("semantic"):
            ok = f"t{i}" in yes
            pipeline.record(user.id, "semantic", event_type=e.type, event_id=e.id, summary=t["semantic"][:80],
                            watch_id=w["id"], watch_title=w["title"], trigger_id=t["id"],
                            result="failed" if failed else ("yes" if ok else "no"), cost_usd=share)
            if not ok:
                continue
        if w["id"] in fired_watches:
            continue
        fired_watches.add(w["id"])
        fid = store.add_fire(user, w["id"], trigger_id=t["id"], event_type=e.type, event=e.data)
        pipeline.record(user.id, "fire", event_type=e.type, event_id=e.id, summary=summary, watch_id=w["id"],
                        watch_title=w["title"], trigger_id=t["id"])
        if w["kind"] == "watch" and w["spec"].get("repeat", "once") == "once":
            store.close(user, w["id"], store.DONE)
        out.append((w, t["id"], fid))
    for i, w in enumerate(evidence):
        ok = f"e{i}" in yes
        pipeline.record(user.id, "evidence", event_type=e.type, event_id=e.id, summary=summary, watch_id=w["id"],
                        watch_title=w["title"], result="failed" if failed else ("yes" if ok else "no"),
                        cost_usd=share)
        if ok:
            store.add_fire(user, w["id"], trigger_id="evidence", event_type=e.type, event=e.data, count=False,
                           actions=[actions.result("suggest_complete", actions.HELD,
                                                   "asks on the Now screen whether it is done")])
    return out


async def _run_fire(user: User, watch: Dict[str, Any], todo: List[Dict[str, Any]], e: Optional[S.Event],
                    trigger_id: str, fire_id: str) -> None:
    results = await actions.run_all(user, watch, todo, e, trigger_id, fire_id)
    for r in results:
        pipeline.record(user.id, "action", event_type=e.type if e else None, event_id=e.id if e else None,
                        summary=f"{r['type']}: {r['detail']}"[:120], watch_id=watch["id"], watch_title=watch["title"],
                        trigger_id=trigger_id, result=r["status"] if r["status"] != "skipped" else None,
                        action=r["type"])


def close_for_task(user: User, task_id: str) -> int:
    """Close ``user``'s reminders about a task that was completed; returns how many."""
    rows = store.by_task(user, task_id)
    for w in rows:
        store.close(user, w["id"], store.DONE)
    return len(rows)


# --- Time -----------------------------------------------------------------------------


def _done_times(fires: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    return {(f["trigger_id"], str(f["event"].get("scheduled"))) for f in fires
            if isinstance(f["event"], dict) and f["event"].get("scheduled")}


async def tick(user: User, now: Optional[dt.datetime] = None) -> int:
    """Fire ``user``'s due time triggers, run deadlines and close ended watches; returns how many fires."""
    now = now or clock.now()
    todo: List[Tuple[Dict[str, Any], List[Dict[str, Any]], str, str]] = []
    async with _lock(user):
        for w in list(active(user)):
            todo += _tick_watch(user, w, now)
    for watch, acts, trigger_id, fire_id in todo:
        await _run_fire(user, watch, acts, None, trigger_id, fire_id)
    return len(todo)


def _tick_watch(user: User, w: Dict[str, Any], now: dt.datetime) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]], str, str]]:
    sp = w["spec"]
    start, end = S.window(sp)
    if start is None or end is None:
        return []
    fires = store.fires(user, w["id"], 500)
    done = _done_times(fires)
    fired_ids = {f["trigger_id"] for f in fires}
    out: List[Tuple[Dict[str, Any], List[Dict[str, Any]], str, str]] = []
    for t in sp.get("triggers") or []:
        if t.get("kind") != "time":
            continue
        due = [o for o in S.occurrences(sp, t["id"], min(now, end)) if o >= start and (t["id"], S.fmt(o)) not in done]
        if not due:
            continue
        o = due[-1]
        key = (w["id"], t["id"], S.fmt(o) or "")
        if key in _passed:
            continue
        if now - o > GRACE:
            _passed.add(key)
            continue
        if any(x in fired_ids for x in t.get("skip_if_fired") or []):
            _passed.add(key)
            pipeline.record(user.id, "time", summary="skipped: another trigger already fired", watch_id=w["id"],
                            watch_title=w["title"], trigger_id=t["id"], result="drop")
            continue
        pipeline.record(user.id, "time", summary=f"{t['id']} at {S.fmt(o)}", watch_id=w["id"], watch_title=w["title"],
                        trigger_id=t["id"], result="pass")
        fid = store.add_fire(user, w["id"], trigger_id=t["id"], event_type="", event={"scheduled": S.fmt(o)})
        pipeline.record(user.id, "fire", summary=f"time {S.fmt(o)}", watch_id=w["id"], watch_title=w["title"],
                        trigger_id=t["id"])
        fired_ids.add(t["id"])
        done.add((t["id"], S.fmt(o) or ""))
        out.append((w, sp.get("on_match") or [], t["id"], fid))
    event_fired = bool(_event_fires(fires))
    has_events = any(t.get("kind") == "event" for t in sp.get("triggers") or [])
    if now >= end:
        if sp.get("on_deadline") and not event_fired:
            pipeline.record(user.id, "deadline", summary=f"window ended {S.fmt(end)} with no match", watch_id=w["id"],
                            watch_title=w["title"], result="pass")
            fid = store.add_fire(user, w["id"], trigger_id="deadline", event_type="", event={"deadline": S.fmt(end)})
            out.append((w, sp["on_deadline"], "deadline", fid))
            store.close(user, w["id"], store.EXPIRED)
            return out
        real = any(f["trigger_id"] not in ("deadline", "evidence") for f in fires) or bool(out)
        store.close(user, w["id"], store.DONE if real else store.EXPIRED)
        return out
    nxt = S.next_time(sp, now, done)
    if nxt is None and w["kind"] == "watch" and not has_events and not sp.get("on_deadline"):
        store.close(user, w["id"], store.DONE)
    elif S.fmt(nxt) != (w.get("next_time") or None):
        store.update(user, w["id"], next_time=S.fmt(nxt))
    return out


events.tap(on_event)
