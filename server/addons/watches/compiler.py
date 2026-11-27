"""Plain words → a watch spec (docs/watches.md, "Compiler").

One call of the user's balanced model (purpose ``watch_compile``) with the
moment, who the user is and how a notify / their own chats reach them
(``identity.py``), the user's places, the names that appear in their events, their event
catalog (fields and a few common real values from the last two weeks) and
their tools. The model answers with the spec as one JSON object (no forced
tool use, no JSON mode: the reply's text is parsed). A reply with no spec or
with validation errors is sent back once with what to fix; on the first try a
semantic trigger whose cheap filters would pass more than the daily budget
in the last 14 days is pushed back too (``spec.load_errors``). ``change``
compiles again from the old spec plus the change in plain words.
"""

import collections
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Set

from core import clock, llm, zones
from core.addons import registry
from core.addons.hooks import EventType
from core.text import untrusted
from core.users import User
from addons.watches import identity
from addons.watches import spec as S

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
PEOPLE_FIELDS = ("sender", "person", "from", "counterparty", "conversation")
COMMON_VALUES = 6
# Fields an event carries only in its history, empty when it fires live (a notification is announced before its
# triage): shown to the model as unusable.
LIVE_EMPTY = {"phone.notification.received": ("category", "priority")}
MAX_VALUE_CHARS = 40
# Known real values shown per field (EventType.values: most relevant first); validation checks against all of them.
MAX_KNOWN_VALUES = 120

SYSTEM = """You are Dana's watch builder. The user speaks Hebrew or English. Turn their request into ONE watch spec: a single JSON object that follows the schema below. Always answer with that JSON object only, never with other text.

Now: {now} ({weekday}), timezone {tz}.
{identity}
The user's places (zones): {zones}
People Dana knows (names as they appear in events): {people}
Event catalog (the only events that exist; fields):
{catalog}
The user's Dana tools (for tool actions): {tools}

How to build it:
- Each moment the user names is its own trigger, including a second time tied to the same thing ("do X at 21:30, it should be done by 22:50" = triggers at 21:30 and 22:50). "remind me at 20:30 and 30 min before and when I get in the car" = 3 triggers; a relative one uses relative_to + offset_minutes.
- "if not X by T" / "unless" = a time trigger at T with skip_if_fired naming X's trigger ("remind me when I get home, or at 19:00 if I'm not home by then" -> the 19:00 trigger has skip_if_fired ["home"]).
- Put everything a field can check exactly into `where` (amount ranges, direction, app, sender, zone, action). "around X" = approx with tolerance_pct 10. Names: use contains with a distinctive part of the name as it appears in events (Hebrew names stay Hebrew).
- A field with known_values in the catalog takes only one of those values (the text before " (" is the value; the rest says what it is): pick the one the user means, never invent one. If none fits, say so in `unsupported`.
- Times of day ("tonight", "after midnight", "during work hours") go in `during`, never in the semantic question.
- Use `semantic` only for meaning a field cannot check ("about dinner", "is a P1 or P2 alert", "contains an event invitation"). Keep where/keywords as a cheap prefilter before semantic whenever possible.
- A semantic question is judged from that event's fields ONLY (exactly the catalog fields of its type, nothing else: no address book, no other events, not who the user is). Never ask about anything the event does not carry (who it was sent to when the type has no recipient field, whether an address is the user's): check it with `where` on a field that carries it (e.g. `from_email` eq one of the user's own addresses above), or leave it out and say so in `unsupported`. Ask a semantic question only about the meaning of the text the event carries.
- The same thing may be in the catalog from its own source and from the phone's notification of it (an email from the mail account vs the phone's email notification): prefer the most specific type, from the source itself, which carries more fields.
- The same thing can reach Dana as more than one event type (a charge from an app that is not the bank is a phone.notification with `amount`, not a money.transaction; a person may write by message or email). When unsure, add one trigger per likely type.
- repeat: "once" for a single expected thing (a person writes, an email arrives, a payment comes in); "each" when the user asks about a recurring kind ("any payment over", "every time", "whenever", "all").
- "until X" sets window_end. "make sure", "check that", "if not ... by" => also on_deadline (tell the user it did not happen). Default window: reminders until the end of the day of their last time; watches 7 days.
- kind=reminder when the user wants to be reminded of something to do (task_title = the thing to do). For reminders ALSO fill evidence_event_types/evidence_keywords (Hebrew and English forms)/evidence_question so Dana can notice the task got done by itself (an email with tickets, a payment, a message saying it is done). The evidence_question asks whether the thing WAS DONE, never whether it was talked about.
- Actions: notify (default for watches; a reminder's own trigger fire IS the reminder, so on_match for a reminder is notify with the reminder text). create_task, tool (a direct Dana tool call, args_json), agent (instructions for an AI agent that gets the triggering event and Dana's tools, for multi-step work like "summarize and send to Gil", "put it in my calendar and invite Gil"). Outbound messages default to autonomy ask_first unless the user said to just do it. A tool action needs real ids (a device entity, a chat): if you do not know them, use an agent action that looks them up instead of guessing.
- If something cannot be expressed (no such event source, a condition across two events), set `unsupported` honestly and build the closest watch. Never claim the user cannot be reached on a channel or tool listed above, nor call a notify a poor substitute: it reaches them where it says.
- An agent action already knows who the user is and how to reach them; write its instructions with "me" kept as the user.
- Titles in the user's language, short.

The JSON schema of the spec:
{schema}
"""


class CompileError(ValueError):
    """The model gave no valid spec; ``errors`` says what was wrong with its last try."""

    def __init__(self, errors: List[str]) -> None:
        super().__init__("; ".join(errors) or "no watch could be built")
        self.errors = errors


@dataclass
class Compiled:
    """A compiled, validated spec, its load (dry run) and how many tries it took."""
    spec: Dict[str, Any]
    load: Dict[str, Any]
    attempts: int
    notes: List[str] = field(default_factory=list)


def schema(types: Mapping[str, EventType]) -> Dict[str, Any]:
    """The spec's JSON schema for this user's event types (shown to the model)."""
    cond = {"type": "object", "required": ["field", "op"], "properties": {
        "field": {"type": "string"}, "op": {"type": "string", "enum": S.OPS},
        "value": {"type": "string", "description": "for in / contains_any: values separated by |"},
        "value2": {"type": "string", "description": "upper bound for between"},
        "tolerance_pct": {"type": "number", "description": "for approx, default 10"}}}
    action = {"type": "object", "required": ["type"], "properties": {
        "type": {"type": "string", "enum": S.ACTIONS},
        "text": {"type": "string", "description": "notify: the message; create_task: the task title"},
        "urgency": {"type": "string", "enum": S.URGENCY},
        "tool": {"type": "string", "description": "tool: one of the user's Dana tool names"},
        "args_json": {"type": "string", "description": "tool: JSON object of arguments"},
        "instructions": {"type": "string", "description": "agent: what the agent should do with the triggering event"},
        "autonomy": {"type": "string", "enum": S.AUTONOMY,
                     "description": "agent/tool: outbound messages, deletes, money default to ask_first"}}}
    names = sorted(types)
    trigger = {"type": "object", "required": ["id", "kind"], "properties": {
        "id": {"type": "string", "description": "short and meaningful, e.g. 'home', 't2030'"},
        "kind": {"type": "string", "enum": ["time", "event"]},
        "at": {"type": "string", "description": "time: local 'YYYY-MM-DD HH:MM'"},
        "relative_to": {"type": "string", "description": "time: another trigger id; at is then ignored"},
        "offset_minutes": {"type": "integer", "description": "time with relative_to: e.g. -30"},
        "every": {"type": ["string", "null"], "enum": S.EVERY + [None], "description": "time: repeat rule"},
        "skip_if_fired": {"type": "array", "items": {"type": "string"},
                          "description": "this trigger does not fire if any of these trigger ids already fired"},
        "event_type": {"type": "string", "enum": names},
        "where": {"type": "array", "items": cond, "description": "all must hold (AND)"},
        "keywords": {"type": "array", "items": {"type": "string"},
                     "description": "optional cheap prefilter: at least one must appear in the event text "
                                    "(give Hebrew and English forms)"},
        "semantic": {"type": "string", "description": "optional yes/no question about the event, judged by a small "
                                                      "model only after where/keywords pass"},
        "during": {"type": "string", "description": "event: only at these local times of day, 'HH:MM-HH:MM' (may "
                                                    "cross midnight, e.g. '00:00-06:00', '18:00-02:00')"}}}
    return {"type": "object", "required": ["title", "kind", "triggers", "on_match"], "properties": {
        "title": {"type": "string", "description": "one readable line, in the user's language"},
        "kind": {"type": "string", "enum": S.KINDS},
        "task_title": {"type": "string", "description": "reminder: the task to create"},
        "triggers": {"type": "array", "items": trigger},
        "window_start": {"type": "string", "description": "local 'YYYY-MM-DD HH:MM', default now"},
        "window_end": {"type": "string", "description": "local 'YYYY-MM-DD HH:MM'"},
        "cooldown_minutes": {"type": "integer", "description": "repeat=each: quiet minutes after a fire (default 10)"},
        "repeat": {"type": "string", "enum": S.REPEAT},
        "on_match": {"type": "array", "items": action},
        "on_deadline": {"type": "array", "items": action,
                        "description": "runs if the window ends with no event match ('make sure', 'if not')"},
        "evidence_event_types": {"type": "array", "items": {"type": "string", "enum": names}},
        "evidence_keywords": {"type": "array", "items": {"type": "string"}},
        "evidence_question": {"type": "string"},
        "unsupported": {"type": "string", "description": "if part of the request cannot be expressed, say what"}}}


def common_values(hist: Mapping[str, List[S.Event]]) -> Dict[str, Dict[str, List[str]]]:
    """Per event type and text field, the values seen most often in the user's recent events (at least twice)."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for name, evs in hist.items():
        counts: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for e in evs:
            for k, v in e.data.items():
                if isinstance(v, str) and v.strip() and len(v) <= MAX_VALUE_CHARS:
                    counts[k][v.strip()] += 1
        out[name] = {k: [v for v, n in c.most_common(COMMON_VALUES) if n >= 2] for k, c in counts.items()}
        out[name] = {k: v for k, v in out[name].items() if v}
    return out


def people(hist: Mapping[str, List[S.Event]], limit: int = 60) -> List[str]:
    """The names that appear most in the user's recent events (senders, callers, counterparties)."""
    c: collections.Counter = collections.Counter()
    for evs in hist.values():
        for e in evs:
            for k in PEOPLE_FIELDS:
                v = e.data.get(k)
                if isinstance(v, str) and v.strip() and len(v) <= MAX_VALUE_CHARS:
                    c[v.strip()] += 1
    return [v for v, _ in c.most_common(limit)]


def known_catalog(known: Mapping[str, Mapping[str, List[str]]]) -> Dict[str, Dict[str, List[str]]]:
    """The known values as the model sees them: at most :data:`MAX_KNOWN_VALUES` per field, most relevant first."""
    return {n: {k: list(v[:MAX_KNOWN_VALUES]) for k, v in fields.items()} for n, fields in known.items()}


def system_prompt(user: User, types: Mapping[str, EventType], hist: Mapping[str, List[S.Event]],
                  tools: List[str], now: dt.datetime,
                  known: Optional[Mapping[str, Mapping[str, List[str]]]] = None) -> str:
    """The compiler's instructions for ``user`` now (``known``: :func:`spec.known_values`)."""
    shown = known_catalog(known or {})
    cat: List[Dict[str, Any]] = []
    for n, e in types.items():
        entry: Dict[str, Any] = {"event_type": n, "about": e.about, "fields": {
            k: ("always empty when the event fires: never use it" if k in LIVE_EMPTY.get(n, ()) else v)
            for k, v in e.fields.items()}}
        if shown.get(n):
            entry["known_values"] = shown[n]
        cat.append(entry)
    values = {n: {k: v for k, v in fields.items() if k not in LIVE_EMPTY.get(n, ())}
              for n, fields in common_values(hist).items()}
    names = people(hist)
    seen = ""
    if any(values.values()) or names:
        seen = "\n" + untrusted("the user's recent events (common values and names, written by others)",
                                json.dumps({"common_values": values, "people": names}, ensure_ascii=False))
    return SYSTEM.format(now=now.strftime(S.TIME_FORMAT), weekday=now.strftime("%A"), tz=str(clock.get_timezone()),
                         identity=identity.about_user(user, tools) + identity.remembered(user),
                         zones=", ".join(z.name for z in zones.list_zones(user)) or "none yet",
                         people="see the frame below" if names else "none known",
                         catalog=json.dumps(cat, ensure_ascii=False) + seen,
                         tools=", ".join(tools) or "none",
                         schema=json.dumps(schema(types), ensure_ascii=False))


def _parse(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if "{" not in t:
        return None
    try:
        data = json.loads(t[t.index("{"):t.rindex("}") + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def compile_request(user: User, text: str, *, old: Optional[Mapping[str, Any]] = None,
                          now: Optional[dt.datetime] = None) -> Compiled:
    """Build and validate a spec from ``text`` (with ``old``: the change ``text`` asks of that spec).

    Raises:
        CompileError: no valid spec after the retries.
    """
    now = now or clock.now()
    types = {e.name: e for e in registry.event_types(user)}
    hist = S.history_events(types.values(), user, now - dt.timedelta(days=S.DRY_RUN_DAYS))
    tools = [t.name for t in registry.tools_for(user)]
    known = S.known_values(types.values(), user)
    await identity.learn(user)
    if old is not None:
        clean = {k: v for k, v in old.items() if k != "task_id"}
        ask = (f"The current watch spec:\n{json.dumps(clean, ensure_ascii=False)}\n\nChange it: {text}\n"
               "Answer with the whole updated spec.")
    else:
        ask = text
    msgs: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt(user, types, hist, tools, now, known)},
                                  {"role": "user", "content": ask}]
    errors: List[str] = ["no answer"]
    for attempt in range(MAX_ATTEMPTS):
        reply = await llm.complete(msgs, purpose="watch_compile", max_tokens=12000)
        raw = _parse(reply)
        if raw is None:
            errors = ["the answer was not a JSON spec"]
            msgs += [{"role": "assistant", "content": reply or "(empty)"},
                     {"role": "user", "content": "Answer with the watch spec as one JSON object, nothing else."}]
            continue
        spec, errors = S.validate(raw, now, types, set(tools), known)
        load: Dict[str, Any] = {}
        if not errors:
            load = S.estimate_load(spec, hist)
            if attempt == 0:
                errors = S.load_errors(load, spec)
        if not errors:
            if old is not None and old.get("task_id"):
                spec["task_id"] = old["task_id"]
            return Compiled(spec, load, attempt + 1)
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": "Invalid: " + "; ".join(errors) + ". Answer again with the whole spec, fixed."}]
    raise CompileError(errors)


def available_tools(user: User) -> Set[str]:
    """The names of the tools ``user`` has (what a tool action may name)."""
    return {t.name for t in registry.tools_for(user)}
