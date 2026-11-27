"""The watch spec: its shape, validation, time rules and the cheap matching
(structured conditions, keywords, times of day) — pure functions, no database
and no model (docs/watches.md, "The spec").

A spec is plain JSON (stored in ``watches.spec``)::

    {"title", "kind": "watch|reminder", "task_title", "task_id",
     "triggers": [{"id", "kind": "time", "at": "YYYY-MM-DD HH:MM", "every", "skip_if_fired"},
                  {"id", "kind": "time", "relative_to", "offset_minutes"},
                  {"id", "kind": "event", "event_type", "where": [cond], "keywords", "during", "semantic"}],
     "window_start", "window_end", "repeat": "once|each", "cooldown_minutes",
     "on_match": [action], "on_deadline": [action],
     "evidence_event_types", "evidence_keywords", "evidence_question", "unsupported"}

Text matching ignores case, spaces and punctuation (:func:`norm`). A semantic
question costs a small-model call per candidate, so a trigger whose cheap
filters would pass more than :data:`SEMANTIC_BUDGET_PER_DAY` events a day in
the last two weeks is refused (:func:`load_errors`).
"""

import calendar
import copy
import datetime as dt
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from core import clock
from core.addons.hooks import EventType

logger = logging.getLogger(__name__)

OPS = ["eq", "ne", "in", "contains", "contains_any", "not_contains", "gt", "gte", "lt", "lte", "between", "approx",
       "exists"]
NUMERIC_OPS = ("gt", "gte", "lt", "lte", "between", "approx")
ACTIONS = ["notify", "create_task", "complete_task", "suggest_complete", "tool", "agent"]
EVERY = ["daily", "weekdays", "weekly", "monthly"]
URGENCY = ["now", "glance", "later"]
AUTONOMY = ["ask_first", "act_and_tell"]
KINDS = ["watch", "reminder"]
REPEAT = ["once", "each"]
SEMANTIC_BUDGET_PER_DAY = 20
DEFAULT_COOLDOWN = 10
DRY_RUN_DAYS = 14
# Events that need no condition: the event itself is the trigger.
BARE_EVENTS = ("core.user.woke",)
MAX_TRIGGERS = 12
MAX_ACTIONS = 8
TIME_FORMAT = "%Y-%m-%d %H:%M"


@dataclass
class Event:
    """One event as the engine sees it: type, data, when (aware, local) and an id."""
    type: str
    data: Dict[str, Any]
    ts: dt.datetime
    id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# --- Times ----------------------------------------------------------------------------


def parse_local(value: Any) -> Optional[dt.datetime]:
    """A local 'YYYY-MM-DD HH:MM' (or ISO) as an aware datetime in the server's zone, None when it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        d = dt.datetime.fromisoformat(value.strip().replace("T", " "))
    except ValueError:
        return None
    return d.astimezone(clock.get_timezone()) if d.tzinfo else d.replace(tzinfo=clock.get_timezone())


def fmt(when: Optional[dt.datetime]) -> Optional[str]:
    """A datetime as the spec writes times: local 'YYYY-MM-DD HH:MM'."""
    return when.astimezone(clock.get_timezone()).strftime(TIME_FORMAT) if when else None


def workweek() -> Set[int]:
    """The weekdays (Monday 0 … Sunday 6) that count as work days: the five
    days from the day weeks start on (``clock.week_start_day``: Sunday by
    default, so Sunday–Thursday; with weeks starting Monday, Monday–Friday)."""
    first = clock.week_start_day()
    return {(first + i) % 7 for i in range(5)}


def step(when: dt.datetime, every: str) -> Optional[dt.datetime]:
    """The occurrence after ``when`` for a repeat rule (None: no repeat)."""
    if every == "daily":
        return when + dt.timedelta(days=1)
    if every == "weekdays":
        days = workweek()
        nxt = when + dt.timedelta(days=1)
        while nxt.weekday() not in days:
            nxt += dt.timedelta(days=1)
        return nxt
    if every == "weekly":
        return when + dt.timedelta(days=7)
    if every == "monthly":
        year, month = (when.year + 1, 1) if when.month == 12 else (when.year, when.month + 1)
        return when.replace(year=year, month=month, day=min(when.day, calendar.monthrange(year, month)[1]))
    return None


def _trigger(spec: Mapping[str, Any], tid: str) -> Optional[Dict[str, Any]]:
    return next((t for t in spec.get("triggers") or [] if t.get("id") == tid), None)


def occurrences(spec: Mapping[str, Any], tid: str, until: dt.datetime, depth: int = 0) -> Iterator[dt.datetime]:
    """The times time-trigger ``tid`` fires, oldest first, up to ``until``
    (a relative trigger follows each occurrence of the one it names)."""
    t = _trigger(spec, tid)
    if t is None or t.get("kind") != "time" or depth > 4:
        return
    if t.get("relative_to"):
        offset = dt.timedelta(minutes=int(t.get("offset_minutes") or 0))
        for base in occurrences(spec, t["relative_to"], until - offset, depth + 1):
            yield base + offset
        return
    at = parse_local(t.get("at"))
    every = t.get("every") or ""
    count = 0
    while at is not None and at <= until and count < 5000:
        yield at
        count += 1
        at = step(at, every)


def first_time(spec: Mapping[str, Any], tid: str) -> Optional[dt.datetime]:
    """The first occurrence of a time trigger (None when it has none)."""
    far = dt.datetime(9999, 1, 1, tzinfo=clock.get_timezone())
    return next(occurrences(spec, tid, far), None)


def window(spec: Mapping[str, Any]) -> Tuple[Optional[dt.datetime], Optional[dt.datetime]]:
    """(window start, window end) of a validated spec."""
    return parse_local(spec.get("window_start")), parse_local(spec.get("window_end"))


def next_time(spec: Mapping[str, Any], after: dt.datetime, done: Set[Tuple[str, str]]) -> Optional[dt.datetime]:
    """When the watch next acts on its own after ``after``: its next time
    trigger occurrence not yet ``done`` (trigger id, 'YYYY-MM-DD HH:MM'), else
    the window's end when it has a deadline (None: only events)."""
    start, end = window(spec)
    best: Optional[dt.datetime] = None
    for t in spec.get("triggers") or []:
        if t.get("kind") != "time" or end is None:
            continue
        for at in occurrences(spec, t["id"], end):
            if at > after and (start is None or at >= start) and (t["id"], fmt(at)) not in done:
                best = at if best is None or at < best else best
                break
    if best is None and end is not None and end > after and spec.get("on_deadline"):
        return end
    return best


# --- Validation -----------------------------------------------------------------------


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value or [] if str(v).strip()] if isinstance(value, list) else []


def _clean_action(a: Any, where: str, tools: Optional[Set[str]], errs: List[str]) -> Dict[str, Any]:
    if not isinstance(a, dict):
        errs.append(f"{where}: an action must be an object")
        return {}
    out = {k: v for k, v in a.items() if k in ("type", "text", "urgency", "tool", "args_json", "instructions",
                                                "autonomy")}
    kind = out.get("type")
    if kind not in ACTIONS:
        errs.append(f"{where}.type: action type must be one of {ACTIONS}, not {kind!r}")
    if out.get("urgency") not in URGENCY:
        out["urgency"] = "now" if kind in ("notify", "suggest_complete") else "glance"
    if out.get("autonomy") not in AUTONOMY:
        out["autonomy"] = "ask_first"
    if kind == "tool":
        name = str(out.get("tool") or "")
        if not name:
            errs.append(f"{where}.tool: a tool action names its tool")
        elif tools is not None and name not in tools:
            errs.append(f"{where}.tool: {name!r} is not one of the user's tools; use an agent action with instructions")
        raw = out.get("args_json")
        if isinstance(raw, dict):
            out["args_json"] = json.dumps(raw, ensure_ascii=False)
        elif raw:
            try:
                if not isinstance(json.loads(str(raw)), dict):
                    raise ValueError
            except ValueError:
                errs.append(f"{where}.args_json: args_json of {name} is not a JSON object")
        else:
            out["args_json"] = "{}"
    if kind == "agent" and not (out.get("instructions") or out.get("text")):
        errs.append(f"{where}.instructions: an agent action needs instructions")
    if kind in ("notify", "create_task") and not str(out.get("text") or "").strip():
        errs.append(f"{where}.text: a {kind} action needs text")
    return out


Known = Mapping[str, Mapping[str, Sequence[str]]]


def value_of(entry: str) -> str:
    """The value of a known-value entry ("sensor.phone_battery (Phone battery)" → "sensor.phone_battery")."""
    return str(entry).split(" (", 1)[0].strip()


def known_values(types: Iterable[EventType], user: Any) -> Dict[str, Dict[str, List[str]]]:
    """Per event type, its ``values(user)`` (the known real values of some fields); a failing one gives none."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for et in types:
        if et.values is None:
            continue
        try:
            got = et.values(user) or {}
        except Exception:
            logger.warning(f"Known values of {et.name} failed; none shown", exc_info=True)
            continue
        clean = {k: [str(v) for v in vs if str(v).strip()] for k, vs in got.items() if isinstance(vs, (list, tuple))}
        clean = {k: v for k, v in clean.items() if v}
        if clean:
            out[et.name] = clean
    return out


def close_matches(given: str, entries: Sequence[str], n: int = 3) -> List[str]:
    """Known-value entries closest to ``given``: by the spelling of the value or its label, then sharing a word."""
    by_key: Dict[str, str] = {}
    for e in entries:
        by_key.setdefault(norm(value_of(e)), e)
        by_key.setdefault(norm(e), e)
    out: List[str] = []
    for key in difflib.get_close_matches(norm(given), list(by_key), n=n * 2, cutoff=0.5):
        if by_key[key] not in out:
            out.append(by_key[key])
    words = [w for w in re.split(r"[\W_]+", str(given).casefold()) if len(w) > 2 and w not in ("sensor", "binary")]
    for e in entries:
        if len(out) >= n:
            break
        if e not in out and any(w in e.casefold() for w in words):
            out.append(e)
    return out[:n]


def _known_errors(cond: Mapping[str, Any], known: Sequence[str], path: str) -> List[str]:
    """``eq`` / ``in`` values that are not among a field's known values (a closed set)."""
    if cond.get("op") not in ("eq", "in") or not known:
        return []
    have = {norm(value_of(k)) for k in known}
    given = [str(cond.get("value") or "")] if cond["op"] == "eq" else str(cond.get("value") or "").split("|")
    errs = []
    for v in given:
        if v.strip() and norm(v) not in have:
            close = close_matches(v, known)
            errs.append(f"{path}: {v.strip()!r} is not one of the user's real {cond.get('field')} values"
                        + (f"; close: {', '.join(close)}" if close else "; use one of the catalog's known_values"))
    return errs


def _clean_trigger(t: Any, catalog: Mapping[str, EventType], errs: List[str], path: str,
                   values: Optional[Known] = None) -> Dict[str, Any]:
    if not isinstance(t, dict):
        errs.append(f"{path}: a trigger must be an object")
        return {}
    tid = str(t.get("id") or "").strip()
    out: Dict[str, Any] = {"id": tid, "kind": t.get("kind")}
    if not tid:
        errs.append(f"{path}.id: every trigger needs an id")
    if t.get("skip_if_fired"):
        out["skip_if_fired"] = _strings(t.get("skip_if_fired"))
    if t.get("kind") == "time":
        if t.get("relative_to"):
            out["relative_to"] = str(t["relative_to"])
            offset = _num(t.get("offset_minutes"))
            if offset is None:
                errs.append(f"{path}.offset_minutes: a relative time needs offset_minutes (a number, e.g. -30)")
            out["offset_minutes"] = int(offset or 0)
        else:
            at = parse_local(t.get("at"))
            if at is None:
                errs.append(f"{path}.at: bad time {t.get('at')!r}, use 'YYYY-MM-DD HH:MM'")
            out["at"] = fmt(at) if at else t.get("at")
        every = t.get("every") or None
        if isinstance(every, str) and every.startswith("weekly"):
            every = "weekly"
        if every not in (None, *EVERY):
            errs.append(f"{path}.every: every must be one of {EVERY} or null")
        out["every"] = every
    elif t.get("kind") == "event":
        et = t.get("event_type")
        out["event_type"] = et
        if et not in catalog:
            errs.append(f"{path}.event_type: unknown event_type {et!r}; valid: {sorted(catalog)}")
            return out
        fields = catalog[et].fields
        conds = []
        for j, c in enumerate(t.get("where") or []):
            if not isinstance(c, dict):
                errs.append(f"{path}.where[{j}]: a condition must be an object")
                continue
            cond = {k: c[k] for k in ("field", "op", "value", "value2", "tolerance_pct") if c.get(k) is not None}
            for k in ("value", "value2"):
                if k in cond and not isinstance(cond[k], str):
                    cond[k] = "|".join(map(str, cond[k])) if isinstance(cond[k], list) else str(cond[k])
            if cond.get("field") not in fields:
                errs.append(f"{path}.where[{j}].field: {et} has no field {cond.get('field')!r}; fields: {list(fields)}")
            if cond.get("op") not in OPS:
                errs.append(f"{path}.where[{j}].op: bad op {cond.get('op')!r}; ops: {OPS}")
            if cond.get("op") in NUMERIC_OPS and (_num(cond.get("value")) is None or (
                    cond["op"] == "between" and _num(cond.get("value2")) is None)):
                errs.append(f"{path}.where[{j}].value: {cond['op']} on {cond.get('field')} needs numbers")
            if cond.get("op") not in ("exists",) and "value" not in cond:
                errs.append(f"{path}.where[{j}].value: {cond.get('op')} on {cond.get('field')} needs a value")
            errs.extend(_known_errors(cond, ((values or {}).get(et) or {}).get(str(cond.get("field") or "")) or [],
                                      f"{path}.where[{j}].value"))
            conds.append(cond)
        out["where"] = conds
        out["keywords"] = _strings(t.get("keywords"))
        during = str(t.get("during") or "").strip()
        if during:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$", during.replace(" ", "")):
                errs.append(f"{path}.during: during must be 'HH:MM-HH:MM', not {during!r}")
            out["during"] = during.replace(" ", "")
        if str(t.get("semantic") or "").strip():
            out["semantic"] = str(t["semantic"]).strip()
        if not (conds or out["keywords"] or out.get("semantic")) and et not in BARE_EVENTS:
            errs.append(f"{path}: an event trigger needs where, keywords or semantic")
    else:
        errs.append(f"{path}.kind: kind must be time or event")
    return out


def validate(spec: Any, now: dt.datetime, catalog: Mapping[str, EventType],
             tools: Optional[Set[str]] = None, values: Optional[Known] = None) -> Tuple[Dict[str, Any], List[str]]:
    """The spec in its stored form (defaults filled in, times resolved, the
    window set) and the errors the compiler or the page gets back (empty: valid).

    ``catalog``: the user's event types by name; ``tools``: the names of the
    user's tools (None: not checked); ``values``: per event type, the known
    real values of its closed fields (:func:`known_values`): an ``eq`` / ``in``
    value outside them is an error naming close matches (None: not checked)."""
    errs: List[str] = []
    if not isinstance(spec, dict):
        return {}, ["the spec must be a JSON object"]
    src = copy.deepcopy(spec)
    kind = src.get("kind") if src.get("kind") in KINDS else "watch"
    out: Dict[str, Any] = {
        "title": str(src.get("title") or "").strip()[:200],
        "kind": kind,
        "task_title": str(src.get("task_title") or "").strip()[:200],
        "task_id": src.get("task_id") or None,
    }
    if not out["title"]:
        errs.append("title: title is required")
    if kind == "reminder" and not out["task_title"]:
        out["task_title"] = out["title"]
    raw_triggers = src.get("triggers") or []
    if not isinstance(raw_triggers, list) or not raw_triggers:
        errs.append("triggers: no triggers")
        raw_triggers = []
    if len(raw_triggers) > MAX_TRIGGERS:
        errs.append(f"triggers: at most {MAX_TRIGGERS} triggers")
    triggers = [_clean_trigger(t, catalog, errs, f"triggers[{i}]", values)
                for i, t in enumerate(raw_triggers[:MAX_TRIGGERS])]
    ids = [t.get("id") for t in triggers]
    if len(set(ids)) != len(ids):
        errs.append("triggers: trigger ids must be unique")
    for i, t in enumerate(triggers):
        rel = t.get("relative_to")
        if rel and rel not in ids:
            errs.append(f"triggers[{i}].relative_to: relative_to {rel} is not a trigger id")
        elif rel and (_trigger({"triggers": triggers}, rel) or {}).get("kind") != "time":
            errs.append(f"triggers[{i}].relative_to: relative_to {rel} is not a time trigger")
        for other in t.get("skip_if_fired") or []:
            if other not in ids or other == t["id"]:
                errs.append(f"triggers[{i}].skip_if_fired: skip_if_fired names {other!r}, not another trigger id")
    out["triggers"] = triggers
    out["repeat"] = src.get("repeat") if src.get("repeat") in REPEAT else ("once" if kind == "watch" else "each")
    cooldown = _num(src.get("cooldown_minutes"))
    out["cooldown_minutes"] = int(cooldown) if cooldown is not None and cooldown >= 0 else DEFAULT_COOLDOWN
    for key in ("on_match", "on_deadline"):
        raw = src.get(key) or []
        if not isinstance(raw, list):
            errs.append(f"{key}: must be a list of actions")
            raw = []
        if len(raw) > MAX_ACTIONS:
            errs.append(f"{key}: at most {MAX_ACTIONS} actions")
        out[key] = [_clean_action(a, f"{key}[{i}]", tools, errs) for i, a in enumerate(raw[:MAX_ACTIONS])]
    if not out["on_match"]:
        errs.append("on_match: needs at least one action")
    out["evidence_event_types"] = [e for e in _strings(src.get("evidence_event_types"))]
    for k, e in enumerate(out["evidence_event_types"]):
        if e not in catalog:
            errs.append(f"evidence_event_types[{k}]: unknown event type {e!r}")
    out["evidence_keywords"] = _strings(src.get("evidence_keywords"))
    out["evidence_question"] = str(src.get("evidence_question") or "").strip()
    out["unsupported"] = str(src.get("unsupported") or "").strip()

    start = parse_local(src.get("window_start")) or now
    end = parse_local(src.get("window_end"))
    if src.get("window_start") and parse_local(src.get("window_start")) is None:
        errs.append(f"window_start: bad window_start {src.get('window_start')!r}, use 'YYYY-MM-DD HH:MM'")
    if src.get("window_end") and end is None:
        errs.append(f"window_end: bad window_end {src.get('window_end')!r}, use 'YYYY-MM-DD HH:MM'")
    if not errs and end is None:
        times = [first_time(out, t["id"]) for t in triggers if t.get("kind") == "time"]
        times = [x for x in times if x is not None]
        if times and not any(t.get("every") for t in triggers):
            end = max(times + [now]).replace(hour=23, minute=59, second=0, microsecond=0)
        elif times:
            end = max(times + [now]) + dt.timedelta(days=365)
        else:
            end = now + dt.timedelta(days=7)
    if end is not None and end <= start:
        errs.append("window_end: window_end is before window_start")
    out["window_start"] = fmt(start)
    out["window_end"] = fmt(end) if end else src.get("window_end")
    return out, errs


# --- Matching -------------------------------------------------------------------------

_STRIP = re.compile(r"[\s.\-_'\"״׳`’]+")


def norm(x: Any) -> str:
    """Text the way people read it: case, spaces and punctuation do not matter (K S P = KSP = k.s.p)."""
    return _STRIP.sub("", str(x).casefold())


def has(needle: Any, hay: Any) -> bool:
    """``needle`` appears in ``hay`` (both :func:`norm`-ed)."""
    n = norm(needle)
    return bool(n) and n in norm(hay)


def cond_ok(c: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    """One condition of ``where`` holds for an event's data."""
    v = data.get(c.get("field", ""))
    op, val = c.get("op"), c.get("value")
    sv = "" if v is None else str(v)
    if op == "exists":
        return sv != ""
    if op == "eq":
        return norm(sv) == norm(val) or (_num(v) is not None and _num(v) == _num(val))
    if op == "ne":
        return norm(sv) != norm(val) and not (_num(v) is not None and _num(v) == _num(val))
    if op == "in":
        return norm(sv) in [norm(x) for x in str(val).split("|")]
    if op == "contains":
        return has(val, sv)
    if op == "not_contains":
        return not has(val, sv)
    if op == "contains_any":
        return any(has(x, sv) for x in str(val).split("|"))
    n, target = _num(v), _num(val)
    if n is None or target is None:
        return False
    if op == "gt":
        return n > target
    if op == "gte":
        return n >= target
    if op == "lt":
        return n < target
    if op == "lte":
        return n <= target
    if op == "between":
        hi = _num(c.get("value2"))
        if hi is None:
            return False
        lo, hi = sorted([target, hi])
        return lo <= n <= hi
    if op == "approx":
        tol = (_num(c.get("tolerance_pct")) or 10) / 100
        return abs(n - target) <= abs(target) * tol
    return False


def text_blob(data: Mapping[str, Any]) -> str:
    """Every text value of an event's data, joined (what keywords look in)."""
    return " ".join(str(v) for v in data.values() if isinstance(v, str))


def in_during(during: Optional[str], ts: dt.datetime) -> bool:
    """``ts`` (local) falls in 'HH:MM-HH:MM' (which may cross midnight); no range: always."""
    if not during:
        return True
    try:
        a, b = [x.strip() for x in during.split("-")]
    except ValueError:
        return True
    hm = ts.astimezone(clock.get_timezone()).strftime("%H:%M")
    return a <= hm < b if a <= b else (hm >= a or hm < b)


def keywords_ok(keywords: Sequence[str], data: Mapping[str, Any]) -> bool:
    """At least one keyword appears in the event's text (none given: true)."""
    kws = [norm(k) for k in keywords if norm(k)]
    if not kws:
        return True
    blob = norm(text_blob(data))
    return any(k in blob for k in kws)


def structured_ok(t: Mapping[str, Any], e: Event) -> bool:
    """An event trigger's cheap filters pass for ``e``: its type, time of day, every condition and the keywords."""
    return (e.type == t.get("event_type") and in_during(t.get("during"), e.ts)
            and all(cond_ok(c, e.data) for c in t.get("where") or [])
            and keywords_ok(t.get("keywords") or [], e.data))


def _seen(v: Any) -> str:
    s = "" if v is None else str(v)
    return s if len(s) <= 60 else s[:59] + "…"


def cond_text(c: Mapping[str, Any], data: Mapping[str, Any]) -> str:
    """A condition and the value the event had (``from contains_any "x|y" ← "me"``), for the trace."""
    value = c.get("value", "")
    if c.get("op") == "between":
        value = f"{value}–{c.get('value2')}"
    head = f"{c.get('field')} {c.get('op')}" + ("" if c.get("op") == "exists" else f' "{value}"')
    return f'{head} ← "{_seen(data.get(c.get("field", "")))}"'


def explain(t: Mapping[str, Any], e: Event) -> Tuple[str, str, int]:
    """Why an event trigger's cheap filters fail for ``e`` (the trace): ``(kind, text, passed)`` with kind ``""``
    (they pass), ``"during"``, ``"where"`` or ``"keywords"``, the first failing check in words, and how many of
    its ``where`` conditions and keywords held (a near miss passed at least one)."""
    conds = list(t.get("where") or [])
    results = [cond_ok(c, e.data) for c in conds]
    kws = [k for k in t.get("keywords") or [] if norm(k)]
    kw_ok = keywords_ok(kws, e.data)
    passed = sum(results) + (1 if kws and kw_ok else 0)
    if not in_during(t.get("during"), e.ts):
        return "during", f"during {t.get('during')} ← {e.ts.astimezone(clock.get_timezone()).strftime('%H:%M')}", passed
    for c, ok in zip(conds, results):
        if not ok:
            return "where", cond_text(c, e.data), passed
    if not kw_ok:
        return "keywords", f'keywords "{"|".join(kws[:8])}" not in the text', passed
    return "", "", passed


def pinned_values(watches: Iterable[Mapping[str, Any]], event_type: str, field: str) -> Optional[List[str]]:
    """The values of ``field`` the given (active) watches can fire on for ``event_type``: the union over their
    event triggers on it of the values a ``where`` ``eq`` / ``in`` condition on ``field`` pins (``|``-joined for
    ``in``). None when any trigger on that type pins none, or any watch takes that type as reminder evidence
    (anything may matter); ``[]`` when none listens."""
    out: Set[str] = set()
    for w in watches:
        sp = w.get("spec") or {}
        if event_type in (sp.get("evidence_event_types") or []):
            return None
        for t in sp.get("triggers") or []:
            if t.get("kind") != "event" or t.get("event_type") != event_type:
                continue
            pins = [c for c in t.get("where") or [] if c.get("field") == field and c.get("op") in ("eq", "in")]
            if not pins:
                return None
            c = pins[0]  # conditions are ANDed: one pinning condition already bounds the values
            vals = str(c.get("value") or "").split("|") if c.get("op") == "in" else [str(c.get("value") or "")]
            out.update(v.strip() for v in vals if v.strip())
    return sorted(out)


def source_of(e: Event) -> str:
    """Who an event comes from, for the per-source cooldown: the counterparty, sender, address, person, app or
    entity."""
    d = e.data
    who = d.get("counterparty") or d.get("sender") or d.get("from") or d.get("person") or d.get("app") \
        or d.get("entity_id") or ""
    return f"{e.type}|{norm(who)}"


def summary(event_type: str, data: Mapping[str, Any]) -> str:
    """An event in a few words ("WhatsApp · Gal", "Transaction · Maya Cohen · 22,000")."""
    head = data.get("app") or data.get("account") or ""
    if not head:
        parts = event_type.split(".")
        head = " ".join(parts[1:]).replace("_", " ").capitalize() if len(parts) > 1 else event_type
    bits = [str(head)]
    who = data.get("sender") or data.get("from") or data.get("person") or data.get("counterparty") \
        or data.get("zone") or data.get("conversation") or data.get("title") or data.get("name") \
        or data.get("action") or ""
    if who:
        bits.append(str(who)[:40])
    amount = _num(data.get("amount"))
    if amount is not None:
        bits.append(f"{amount:,.0f}")
    if data.get("new_state") not in (None, ""):
        bits.append(f"{str(data.get('old_state') or '?')[:20]} → {str(data['new_state'])[:20]}")
    return " · ".join(bits)


# --- Dry runs -------------------------------------------------------------------------


History = Mapping[str, Sequence[Event]]


def dry_run(spec: Mapping[str, Any], history: History, days: int = DRY_RUN_DAYS) -> Dict[str, Dict[str, Any]]:
    """Each event trigger's cheap filters over the last ``days`` of the user's
    real events: how many would have passed, per day, whether its semantic
    check would go over budget, and the last 20 (``sample``)."""
    out: Dict[str, Dict[str, Any]] = {}
    for t in spec.get("triggers") or []:
        if t.get("kind") != "event":
            continue
        hits = [e for e in history.get(t.get("event_type") or "", ()) if structured_ok(t, e)]
        per_day = round(len(hits) / max(days, 1), 1)
        out[t["id"]] = {"matches": len(hits), "per_day": per_day,
                        "over_budget": bool(t.get("semantic")) and per_day > SEMANTIC_BUDGET_PER_DAY,
                        "sample": [{"at": e.ts.isoformat(timespec="seconds"), "event_type": e.type,
                                    "summary": summary(e.type, e.data), "data": e.data} for e in hits[-20:]]}
    return out


def estimate_load(spec: Mapping[str, Any], history: History, days: int = DRY_RUN_DAYS) -> Dict[str, Dict[str, Any]]:
    """A watch's ``load``: per event trigger ``{matches_14d, per_day, semantic, over_budget}``."""
    return {tid: {"matches_14d": r["matches"], "per_day": r["per_day"],
                  "semantic": bool((_trigger(spec, tid) or {}).get("semantic")), "over_budget": r["over_budget"]}
            for tid, r in dry_run(spec, history, days).items()}


def load_errors(load: Mapping[str, Mapping[str, Any]], spec: Mapping[str, Any]) -> List[str]:
    """The budget push-back for the compiler: one line per trigger whose semantic check would run too often."""
    index = {t.get("id"): i for i, t in enumerate(spec.get("triggers") or [])}
    return [f"triggers[{index.get(tid, tid)}].semantic: trigger {tid}'s where/keywords/during pass {v['per_day']} "
            "events a day in the last 2 weeks, so the "
            f"semantic check would run that often (budget {SEMANTIC_BUDGET_PER_DAY}/day). Narrow it with where "
            "(app, sender, conversation), during, or more specific keywords."
            for tid, v in load.items() if v.get("over_budget")]


# --- Words ----------------------------------------------------------------------------

_OP_WORDS = {"eq": "is", "ne": "is not", "in": "is one of", "contains": "contains", "contains_any": "contains any of",
             "not_contains": "does not contain", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "between",
             "approx": "≈", "exists": "is set"}


def describe_trigger(t: Mapping[str, Any]) -> str:
    """A trigger in plain words ("at 2026-11-26 20:30, daily", "money.transaction.added: amount ≈ 22000 (±10%)")."""
    if t.get("kind") == "time":
        if t.get("relative_to"):
            m = int(t.get("offset_minutes") or 0)
            words = f"{abs(m)} min {'before' if m < 0 else 'after'} {t['relative_to']}"
        else:
            words = f"at {t.get('at')}"
        if t.get("every"):
            words += f", {t['every']}"
    else:
        conds = []
        for c in t.get("where") or []:
            value = c.get("value", "")
            if c.get("op") == "between":
                value = f"{value}–{c.get('value2')}"
            elif c.get("op") == "approx":
                value = f"{value} (±{c.get('tolerance_pct') or 10}%)"
            conds.append(f"{c.get('field')} {_OP_WORDS.get(c.get('op', ''), c.get('op'))} {value}".strip())
        words = f"{t.get('event_type')}" + (f": {', '.join(conds)}" if conds else "")
        if t.get("keywords"):
            words += f"; words: {', '.join(t['keywords'][:6])}"
        if t.get("during"):
            words += f"; during {t['during']}"
        if t.get("semantic"):
            words += f"; asks: {t['semantic']}"
    if t.get("skip_if_fired"):
        words += f" (skipped if {', '.join(t['skip_if_fired'])} fired)"
    return f"{t.get('id')}: {words}"


def describe(spec: Mapping[str, Any]) -> List[str]:
    """Every trigger of a spec in words."""
    return [describe_trigger(t) for t in spec.get("triggers") or []]


def history_events(types: Iterable[EventType], user: Any, since: dt.datetime) -> Dict[str, List[Event]]:
    """The user's past events of each type that has a history (oldest first)."""
    out: Dict[str, List[Event]] = {}
    for et in types:
        if et.history is None:
            continue
        rows = []
        try:
            for i, (when, data) in enumerate(et.history(user, since)):
                if when.tzinfo is None:
                    when = when.replace(tzinfo=clock.get_timezone())
                rows.append(Event(et.name, dict(data), when.astimezone(clock.get_timezone()), f"h{i}"))
        except Exception:
            logger.warning(f"History of {et.name} failed; dry runs see none", exc_info=True)
            rows = []
        out[et.name] = rows
    return out
