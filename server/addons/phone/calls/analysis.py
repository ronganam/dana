"""The structured analysis of a call transcript: one model call (title,
summary, labels, importance, participants, key facts, action items with
triggers, project, security alert), written for the call's owner."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from core import budget, clock, llm, user_profile
from core.users import User
from addons.phone.calls.context import feedback_prompt, gather_context
from addons.phone.calls.labels import LABEL_IDS, catalog_for_prompt, clean_labels
from addons.phone.calls.values import MAX_TRANSCRIPT_CHARS, text


def _direction_text(user: Optional[User], direction: Optional[str]) -> str:
    me = user_profile.for_user(user).first_name
    return {
        "incoming": f"an incoming call (the contact called {me})",
        "outgoing": f"an outgoing call ({me} called the contact)",
        "missed": "a missed call",
    }.get((direction or "").lower(), "call direction unknown")


def analysis_schema() -> Dict[str, Any]:
    s = {"type": "string"}
    ns = llm.nullable(s)

    def enum(*values: str) -> Dict[str, Any]:
        return {"type": "string", "enum": list(values)}

    return {
        "type": "object",
        "properties": {
            "title": s,
            "summary": s,
            "labels": {"type": "array", "items": {"type": "string", "enum": LABEL_IDS}},
            "importance": enum("low", "normal", "high"),
            "is_automated": {"type": "boolean"},
            "participants": {"type": "array", "items": {"type": "object", "properties": {
                "name": s, "role": s}, "required": ["name"]}},
            "key_facts": {"type": "array", "items": {"type": "object", "properties": {
                "kind": enum("date", "money", "address", "phone", "person", "other"),
                "text": s}, "required": ["kind", "text"]}},
            "action_items": {"type": "array", "items": {"type": "object", "properties": {
                "text": s,
                "owner": enum("me", "them", "both"),
                "kind": enum("task", "event", "info"),
                "due_date": ns,
                "time_trigger": ns,
                "location_zone": ns,
                "location_event": llm.nullable(enum("arrive", "leave")),
                "car_trigger": llm.nullable(enum("enter", "leave")),
                "wake_trigger": llm.nullable({"type": "boolean"}),
                "event_start": ns,
                "event_end": ns,
                "event_location": ns,
                "priority": enum("low", "medium", "high"),
                "group": ns,
                "confidence": {"type": "number"},
                "evidence": s,
            }, "required": ["text", "owner", "kind", "priority", "confidence"]}},
            "project_id": ns,
            "security_alert": ns,
        },
        "required": ["title", "summary", "labels", "importance", "participants", "key_facts", "action_items"],
    }


def build_analysis_prompt(user: Optional[User], transcript: str, call: Dict[str, Any], ctx: Dict[str, Any],
                          custom_instructions: str = "") -> str:
    """The analysis prompt for ``user``'s call (for the first admin exactly as it always was)."""
    who = user_profile.for_user(user)
    me = who.first_name
    when = call.get("when")
    when_text = when.strftime("%A %Y-%m-%d %H:%M") if when else (call.get("call_timestamp") or "unknown")
    dur = int(call.get("duration_seconds") or 0)
    dur_text = f"{dur // 60}:{dur % 60:02d}" if dur else "unknown"
    phone = f" ({call['phone_number']})" if call.get("phone_number") else ""
    people = "\n".join(f"- {p}" for p in ctx.get("people") or []) or "- (none saved)"
    projects = "\n".join(f"- {p['id']}: {p['title']}" for p in ctx.get("projects") or []) or "- (none)"
    open_tasks = "\n".join(f"- {t['title']}" for t in ctx.get("open_tasks") or []) or "- (none)"
    zones = ", ".join(ctx.get("zones") or []) or "(unavailable — do not set location triggers)"
    groups = ", ".join(ctx.get("groups") or [])
    extra = (f"\nExtra instructions from {me} (follow them):\n{custom_instructions.strip()}\n"
             if custom_instructions.strip() else "")
    about = f"About {me}:\n{who.context}\n" if who.context else ""

    return f"""You analyse a phone call recorded on {who.name}'s phone. {who.called} owns the phone and is always one of the speakers.

{about}Saved contacts / people:
{people}

The call: with "{call.get('contact')}"{phone}, {_direction_text(user, call.get('direction'))}, on {when_text} ({clock.get_timezone().key} time), duration {dur_text}.

Return JSON matching the schema. Rules:
- Language: write title, summary, roles, key facts and action items in the transcript's main language (a Spanish call → Spanish). Keep names as spoken.
- Speakers: one side is {me}, the other is normally "{call.get('contact')}". Do NOT invent names for the speakers; if a name isn't clearly said, use "{me}" / the contact name / "the other side". A business contact name means a representative of that business.
- title: 3-7 words, specific (what was decided/asked), no quotes.
- summary: 2-6 short markdown bullet lines starting with "- ". Concrete: decisions, agreements, numbers, dates, addresses. No headings, no fluff.
- labels: 1-3 ids from this list, most relevant first:
{catalog_for_prompt(user)}
- importance: low for quick logistics, automated messages and trivial chit-chat; high for decisions, money, health, security, deadlines; else normal.
- is_automated: true for IVR menus, recorded messages, voicemail.
- participants: the people who speak or are central (name + short role, e.g. "אמא — {me}'s mother", "מאיה — sales rep at the venue").
- key_facts: up to 8 facts worth finding later (dates/times, prices/amounts, addresses, phone numbers, names).
- action_items: only real commitments and follow-ups that {me} would otherwise forget. Usually 0-2 per call.
  * Skip things already done during the call ("I'll check right now" and {who.pronoun} {who.agree('checks', 'check')}), vague intentions ("we should maybe…"), and several items for one topic — merge them into the single next step.
  * owner: "me" when {me} has to do it, "both" when shared, "them" when only the other side will act.
  * kind: "event" for an appointment/meeting {me} attends at a specific date AND time (fill event_start "YYYY-MM-DDTHH:MM", event_end — default 1 hour later — and event_location if known); "task" for to-dos; "info" for things only the other side will do.
  * Resolve relative dates ("Thursday", "tomorrow", "next week") against the call date above; always write absolute dates.
  * due_date "YYYY-MM-DD" when there is a deadline or a natural day for it.
  * Triggers (only when they truly fit the task):
    - time_trigger "YYYY-MM-DD HH:MM": a reminder at the right moment — the exact time for "call back at 17:00", the morning (09:00) of the due day, or a day before for things that need preparation.
    - location_zone + location_event: the task should pop up when {me} arrives at / leaves a place. Only zone names from this list: {zones}.
    - car_trigger: "enter" for things to do on the way when getting into the car (pick up / drop off), "leave" for things to do on arrival by car.
    - wake_trigger: true for things to do first thing in the morning (only when no other trigger fits).
    - Usually one trigger: the moment {me} can actually do it.
  * priority: high only for urgent items, security issues or deadlines within 2 days.
  * group: one of: {groups}.
  * confidence 0-1: how sure you are {me} wants this tracked as a task. Chit-chat ideas → low.
  * evidence: a short quote or paraphrase from the call that supports it.
  * Do not repeat things already on {me}'s open task list:
{open_tasks}
{feedback_prompt(user, ctx, "call")}- project_id: the id of one of {me}'s active projects if the call clearly belongs to it, else null:
{projects}
- security_alert: one sentence when the call shows fraud, scam, account takeover or social-engineering signs (fake support, requests for codes, unexpected account changes); else null.
{extra}
Transcript:
{transcript[:MAX_TRANSCRIPT_CHARS]}"""


async def analyze_transcript(user: Optional[User], transcript: str, call: Dict[str, Any],
                             ctx: Optional[Dict[str, Any]] = None, custom_instructions: str = "") -> Dict[str, Any]:
    """One structured model call for ``user``'s call (run as them: the usage row is theirs). Raises on failure."""
    ctx = ctx if ctx is not None else await gather_context(user)
    prompt = build_analysis_prompt(user, transcript, call, ctx, custom_instructions)
    try:
        result = await llm.complete_json(prompt, analysis_schema(), job="calls", timeout=180.0,
                                         purpose="call_analysis", source="phone",
                                         ref=f"call:{call['id']}" if call.get("id") else "")
    except budget.Paused:
        raise
    except RuntimeError as e:
        raise RuntimeError(f"Call analysis failed: {e}") from e
    return normalize_analysis(result, ctx)


def _summary_text(value) -> str:
    if isinstance(value, list):
        lines = []
        for item in value:
            t = item.get("text") if isinstance(item, dict) else item
            t = str(t or "").strip()
            if t:
                lines.append(t if t.startswith(("-", "•", "*")) else f"- {t}")
        return "\n".join(lines)
    return str(value or "").strip()


def valid_date(v) -> Optional[str]:
    v = text(v)
    try:
        return datetime.date.fromisoformat(v[:10]).isoformat() if v else None
    except ValueError:
        return None


def _valid_datetime(v, fmt_out: str) -> Optional[str]:
    v = text(v).replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.datetime.strptime(v[:n], fmt).strftime(fmt_out)
        except ValueError:
            continue
    return None


def normalize_analysis(raw: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Coerce the model output into the stored shape (drops invalid bits)."""
    ctx = ctx or {}
    zones = {z.lower(): z for z in ctx.get("zones") or []}
    groups = {g.lower(): g for g in ctx.get("groups") or []}
    project_ids = {p["id"] for p in ctx.get("projects") or []}

    items = []
    for it in raw.get("action_items") or []:
        if not isinstance(it, dict) or not text(it.get("text")):
            continue
        zone = text(it.get("location_zone")).lower()
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        item = {
            "text": text(it.get("text")),
            "owner": it.get("owner") if it.get("owner") in ("me", "them", "both") else "me",
            "kind": it.get("kind") if it.get("kind") in ("task", "event", "info") else "task",
            "due_date": valid_date(it.get("due_date")),
            "time_trigger": _valid_datetime(it.get("time_trigger"), "%Y-%m-%d %H:%M"),
            "location_zone": zones.get(zone) if zone else None,
            "location_event": it.get("location_event") if it.get("location_event") in ("arrive", "leave") else None,
            "car_trigger": it.get("car_trigger") if it.get("car_trigger") in ("enter", "leave") else None,
            "wake_trigger": bool(it.get("wake_trigger")) or None,
            "event_start": _valid_datetime(it.get("event_start"), "%Y-%m-%dT%H:%M:00"),
            "event_end": _valid_datetime(it.get("event_end"), "%Y-%m-%dT%H:%M:00"),
            "event_location": text(it.get("event_location")) or None,
            "priority": it.get("priority") if it.get("priority") in ("low", "medium", "high") else "medium",
            "group": groups.get(text(it.get("group")).lower()),
            "confidence": round(conf, 2),
            "evidence": text(it.get("evidence")),
        }
        if item["location_zone"] and not item["location_event"]:
            item["location_event"] = "arrive"
        items.append(item)

    facts = [{"kind": f.get("kind") or "other", "text": text(f.get("text"))}
             for f in raw.get("key_facts") or [] if isinstance(f, dict) and text(f.get("text"))][:8]
    people = [{"name": text(p.get("name")), "role": text(p.get("role"))}
              for p in raw.get("participants") or [] if isinstance(p, dict) and text(p.get("name"))][:8]
    labels = clean_labels(raw.get("labels"))
    if raw.get("is_automated") and "automated" not in labels:
        labels = (labels + ["automated"])[:3] if labels else ["automated"]
    project = text(raw.get("project_id"))
    return {
        "title": text(raw.get("title"))[:120] or "Call",
        "summary": _summary_text(raw.get("summary")),
        "labels": labels,
        "importance": raw.get("importance") if raw.get("importance") in ("low", "normal", "high") else "normal",
        "is_automated": bool(raw.get("is_automated")),
        "participants": people,
        "key_facts": facts,
        "action_items": items,
        "project_id": project if project in project_ids else None,
        "security_alert": text(raw.get("security_alert")) or None,
        "model": llm.model("calls"),
        "analyzed_at": clock.now().isoformat(timespec="seconds"),
    }
