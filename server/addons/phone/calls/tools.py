"""The agent's call and suggestion tools (the ``phone`` addon), each on the
connection's user's own calls and suggestions."""
import sqlite3
import logging
import re
import json
from typing import List, Optional, Dict, Any
from core import undo as undo_registry, undoable
from core.db import get_db_connection
from core.text import escape_markdown
from core.undoable import safe_record
from core.addons import Connection
from addons.phone import suggestions as phone_suggestions
from addons.phone.calls import actions as suggestion_actions
from addons.phone.calls.chat import suggestion_line
from addons.phone.calls.labels import label_text
from addons.phone.addon import addon
from core.users import User
from addons.tasks import service as tasks_service
from addons.tasks.service import TASK_COLUMNS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Word-boundary helper (kept for the multi-term LIKE fallback)
# ---------------------------------------------------------------------------

def _term_matches(text: str, term: str) -> bool:
    """Check if *term* appears as a whole word inside *text* (Hebrew- and English-aware)."""
    if not text or not term:
        return False
    pattern = rf"(?<![a-zA-Z0-9\u0590-\u05ff]){re.escape(term)}(?![a-zA-Z0-9\u0590-\u05ff])"
    return bool(re.search(pattern, text, re.IGNORECASE))


def _format_call_row(user: User, row) -> str:
    """Format a single phone_calls row for display."""
    c_id = row['id']
    caller = escape_markdown(row['caller'])
    receiver = escape_markdown(row['receiver'])
    timestamp = escape_markdown(row['call_timestamp'])
    title = escape_markdown(row['title'] or "Call Recording")
    summary = escape_markdown(row['summary'] or "No summary available.")
    dur = row['duration_seconds'] if 'duration_seconds' in row.keys() else 0
    duration_str = ""
    if dur:
        minutes, seconds = divmod(int(dur), 60)
        duration_str = f" ({minutes:02d}:{seconds:02d})"
    labels = _labels_line(user, row)

    return (
        f"- **Call ID: {c_id}**\n"
        f"  - **Title**: {title}\n"
        f"  - **From**: {caller} | **To**: {receiver}\n"
        f"  - **Date/Time**: {timestamp}{duration_str}\n"
        + (f"  - **Labels**: {labels}\n" if labels else "")
        + f"  - **Summary**: {summary}\n"
    )


def _labels_line(user: User, row) -> str:
    try:
        ids = json.loads(row['labels']) if 'labels' in row.keys() and row['labels'] else []
    except Exception:
        ids = []
    return ", ".join(label_text(user, i) for i in ids)


# ---------------------------------------------------------------------------
# Registered tools
# ---------------------------------------------------------------------------

@addon.tool(untrusted_result=True)
async def list_recent_calls(conn: Connection, limit: int = 5, label: Optional[str] = None) -> str:
    """List the most recent call recordings and voice memos with titles, labels, summaries, and durations.

    Use this when the user asks about recent calls or voice memos in general,
    or when you need to find calls from a specific person before searching deeper.

    Args:
        limit: The max number of calls to retrieve. Defaults to 5.
        label: Optional label id to filter by: family, partner, friends, work, wedding, home, purchases, travel, car, health, pets, admin, security, tech_help, logistics, automated.
    """
    try:
        with get_db_connection() as db:
            if label:
                cursor = db.execute(
                    """
                    SELECT id, caller, receiver, call_timestamp, title, summary, duration_seconds, labels
                    FROM phone_calls
                    WHERE user_id = ? AND status != 'duplicate' AND labels LIKE ?
                    ORDER BY call_timestamp DESC
                    LIMIT ?
                    """,
                    (conn.user.id, f'%"{label.strip().lower()}"%', limit)
                )
            else:
                cursor = db.execute(
                    """
                    SELECT id, caller, receiver, call_timestamp, title, summary, duration_seconds, labels
                    FROM phone_calls
                    WHERE user_id = ? AND status != 'duplicate'
                    ORDER BY call_timestamp DESC
                    LIMIT ?
                    """,
                    (conn.user.id, limit)
                )
            rows = cursor.fetchall()

            if not rows:
                return "No call recordings or voice memos found."

            res = f"Found {len(rows)} recent calls/voice memos:\n\n"
            for row in rows:
                res += _format_call_row(conn.user, row) + "\n"
            return res.strip()
    except Exception as e:
        logger.error(f"Error listing recent calls: {e}", exc_info=True)
        return f"Error listing recent calls: {e}"


@addon.tool(untrusted_result=True)
async def search_calls(conn: Connection, query: str) -> str:
    """Search call recordings and voice memos by person name, topic, or both.

    You can search for:
    - A person's name (searches caller/receiver fields)
    - A topic or keyword (searches transcripts, summaries, titles)
    - A combination of person AND topic (e.g. "אמא דגים" finds calls
      with 'אמא' in the contact name and 'דגים' in the transcript)

    If the query contains multiple words each will be required — only calls
    matching ALL terms (across any field) will be returned.

    For broad searches like "recent calls with אמא", consider listing recent
    calls first with list_recent_calls(limit=10) to get an overview, then
    use get_call_transcript on the relevant call ID.

    Args:
        query: The keyword(s) to find. Multiple words act as AND — all must match.
    """
    if not query.strip():
        return "Error: Search query cannot be empty."

    # Common Hebrew stopwords to filter out — these add noise and rarely help match calls
    _HE_STOPWORDS = frozenset({
        "שלי", "שלך", "שלו", "שלה", "שלנו", "שלכם", "שלהן",
        "אני", "אתה", "את", "אנחנו", "אתם", "אתן", "הוא", "היא", "הם", "הן",
        "זה", "זאת", "זו", "אלה", "כך",
        "על", "אל", "את", "אבל", "או", "אם", "כי", "של", "לו", "לה", "להם", "להן",
        "כל", "יש", "היה", "היתה", "היו", "כן", "לא", "מה", "מי", "איפה", "איך", "למה",
        "עוד", "רק", "גם", "אז", "הרבה", "מאוד",
    })

    query_raw = query.strip()
    # Split into individual non-empty tokens, ignore single-character noise and Hebrew stopwords
    terms = [t for t in query_raw.split() if len(t) > 1 and t not in _HE_STOPWORDS]

    try:
        with get_db_connection() as db:
            rows: List[sqlite3.Row] = []

            # ---- TIER 1: FTS5 full-text search ----
            if terms:
                fts_query = " AND ".join(f'"{t}"' for t in terms)
                try:
                    cursor = db.execute(
                        """
                        SELECT pc.id, pc.caller, pc.receiver, pc.call_timestamp,
                               pc.title, pc.summary, pc.transcript, pc.duration_seconds, pc.labels
                        FROM phone_calls_fts fts
                        JOIN phone_calls pc ON pc.id = fts.id
                        WHERE phone_calls_fts MATCH ? AND pc.user_id = ?
                        ORDER BY rank
                        LIMIT 30
                        """,
                        (fts_query, conn.user.id)
                    )
                    rows = cursor.fetchall()
                except Exception as fts_err:
                    logger.warning(f"FTS5 query failed ({fts_err}), falling back.")

            # ---- TIER 2: multi-term LIKE fallback ----
            if not rows:
                if terms:
                    conditions = []
                    params: List[str] = []
                    for term in terms:
                        like = f"%{term}%"
                        clause = "(caller LIKE ? OR receiver LIKE ? OR transcript LIKE ? OR summary LIKE ? OR title LIKE ?)"
                        conditions.append(clause)
                        params.extend([like] * 5)
                    sql = f"""
                        SELECT id, caller, receiver, call_timestamp, title, summary, transcript, duration_seconds, labels
                        FROM phone_calls
                        WHERE user_id = ? AND {' AND '.join(conditions)}
                        ORDER BY call_timestamp DESC
                        LIMIT 30
                    """
                    cursor = db.execute(sql, [conn.user.id, *params])
                    rows = cursor.fetchall()
                else:
                    # Single-character query — fall back to old-style catch-all
                    like = f"%{query_raw}%"
                    cursor = db.execute(
                        """
                        SELECT id, caller, receiver, call_timestamp, title, summary, transcript, duration_seconds, labels
                        FROM phone_calls
                        WHERE user_id = ? AND (caller LIKE ? OR receiver LIKE ? OR transcript LIKE ? OR summary LIKE ?
                                               OR title LIKE ?)
                        ORDER BY call_timestamp DESC
                        LIMIT 30
                        """,
                        (conn.user.id, like, like, like, like, like)
                    )
                    rows = cursor.fetchall()

            # ---- Post-filter with word-boundary matching ----
            if terms:
                filtered = []
                for row in rows:
                    text = " ".join(str(row[c] or "") for c in
                                    ("caller", "receiver", "title", "summary", "transcript"))
                    if all(_term_matches(text, t) for t in terms):
                        filtered.append(row)
                if filtered:
                    rows = filtered
                # If filtering emptied the set, keep the original (loose) results
            else:
                # For single-char queries, use the raw results
                pass

            if not rows:
                return f"No call recordings or voice memos match the query '{query_raw}'."

            res = f"Found {len(rows)} matching calls/voice memos for '{query_raw}':\n\n"
            for row in rows:
                res += _format_call_row(conn.user, row) + "\n"
            return res.strip()
    except Exception as e:
        logger.error(f"Error searching calls: {e}", exc_info=True)
        return f"Error searching calls: {e}"


@addon.tool(untrusted_result=True)
async def get_call_transcript(conn: Connection, call_id: str) -> str:
    """Retrieve the full transcript and summary of a specific call or voice memo by ID.

    Use this after you have identified the relevant call via list_recent_calls or
    search_calls. The output includes the full dialogue so you can answer specific
    questions about what was said.

    Args:
        call_id: The unique ID of the call (e.g. UUID).
    """
    if not call_id.strip():
        return "Error: Call ID cannot be empty."

    try:
        with get_db_connection() as db:
            cursor = db.execute(
                """
                SELECT id, caller, receiver, call_timestamp, title, transcript, summary, duration_seconds,
                       labels, analysis, direction
                FROM phone_calls
                WHERE id = ? AND user_id = ?
                """,
                (call_id.strip(), conn.user.id)
            )
            row = cursor.fetchone()

            if not row:
                return f"No call or voice memo found with ID '{call_id}'."

            caller = escape_markdown(row['caller'])
            receiver = escape_markdown(row['receiver'])
            timestamp = escape_markdown(row['call_timestamp'])
            title = escape_markdown(row['title'] or "Call Recording")
            transcript = escape_markdown(row['transcript'] or "No transcript available.")
            summary = escape_markdown(row['summary'] or "No summary available.")
            dur = row['duration_seconds'] if 'duration_seconds' in row.keys() else 0
            duration_str = ""
            if dur:
                minutes, seconds = divmod(int(dur), 60)
                duration_str = f" ({minutes:02d}:{seconds:02d})"

            res = (
                f"### Call/Voice Memo Details (ID: {call_id})\n"
                f"- **Title**: {title}\n"
                f"- **From**: {caller}\n"
                f"- **To**: {receiver}\n"
                f"- **Date/Time**: {timestamp}{duration_str}\n"
                + (f"- **Labels**: {_labels_line(conn.user, row)}\n" if _labels_line(conn.user, row) else "")
                + (f"- **Direction**: {row['direction']}\n" if row['direction'] else "")
                + f"\n#### Summary:\n{summary}\n\n"
                + _call_extras(conn.user, row['id'], row['analysis'])
                + f"#### Full Transcript:\n{transcript}"
            )
            return res
    except Exception as e:
        logger.error(f"Error fetching call transcript: {e}", exc_info=True)
        return f"Error fetching call transcript: {e}"


def _call_extras(user: User, call_id: str, analysis_raw) -> str:
    """Key facts, pending suggestions and linked tasks for get_call_transcript."""
    out = ""
    try:
        analysis = json.loads(analysis_raw) if analysis_raw else {}
    except Exception:
        analysis = {}
    facts = analysis.get("key_facts") or []
    if facts:
        out += "#### Key facts:\n" + "\n".join(f"- {f.get('text')}" for f in facts) + "\n\n"
    pending = phone_suggestions.list_suggestions(user, "pending", call_id=call_id)
    if pending:
        out += "#### Pending suggestions (use accept_call_suggestion / dismiss_call_suggestion):\n"
        out += "\n".join(f"- {s['id']}: {suggestion_line(s)}" for s in pending) + "\n\n"
    tasks = tasks_service.tasks_for_call(user, call_id)
    if tasks:
        out += "#### Tasks from this call:\n" + "\n".join(f"- {t['title']} ({t['status']}, ID: {t['id']})" for t in tasks) + "\n\n"
    return out


# Suggestions are written by the model from calls and notifications (others' words).
@addon.tool(untrusted_result=True)
async def list_call_suggestions(conn: Connection, status: str = "pending") -> str:
    """List tasks, calendar events and security alerts suggested from the user's phone calls and phone notifications.

    After each recorded call (and for notifications that ask the user to do something) the assistant
    extracts action items as suggestions; nothing is created until the user (or you, on their explicit
    request) accepts one.

    Args:
        status: 'pending' (default), 'accepted', 'dismissed' or 'all'.
    """
    items = phone_suggestions.list_suggestions(conn.user, None if status == "all" else status, limit=50)
    if not items:
        return f"No {status} suggestions."
    lines = []
    for s in items:
        if s.get("source_type") == "notification":
            n = s.get("notification") or {}
            src = (f"from a {n.get('app_name')} notification ({str(n.get('posted_at') or '')[:16]}, "
                   f"notification ID: {s['source_id']})")
        else:
            call = s.get("call") or {}
            src = (f"from call with {call.get('contact')} ({str(call.get('call_timestamp') or '')[:16]}, "
                   f"call ID: {s['call_id']})")
        lines.append(f"- {s['id']} [{s['status']}] {suggestion_line(s)} — {src}")
    return f"{len(items)} suggestion(s):\n" + "\n".join(lines)


@addon.tool(is_write=True, needs=("tasks",))
async def accept_call_suggestion(
    conn: Connection,
    suggestion_id: str,
    title: Optional[str] = None,
    due_date: Optional[str] = None,
    priority: Optional[str] = None,
    group: Optional[str] = None,
    time_trigger: Optional[str] = None,
    location: Optional[str] = None,
    location_event: Optional[str] = None,
    car_trigger: Optional[str] = None,
    wake_trigger: Optional[bool] = None,
    clear_triggers: bool = False,
    description: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> str:
    """Create the task (or calendar event) a call or notification suggestion describes, optionally with changes. The task is linked to its source.

    Only call this when the user asked to create/accept it or approved your proposed version.
    Pass only the fields that change; everything else comes from the suggestion.

    Args:
        suggestion_id: The suggestion ID (from list_call_suggestions or the chat context).
        title: New task/event title.
        due_date: New due date YYYY-MM-DD (tasks).
        priority: 'low', 'medium' or 'high' (tasks).
        group: Task group name (e.g. 'Home', 'Work', 'Health').
        time_trigger: Reminder time 'YYYY-MM-DD HH:MM' (tasks). Compute absolute times with get_current_time.
        location: One of the user's zones for a location trigger (call get_zones first if unsure).
        location_event: 'arrive' or 'leave' for the location trigger.
        car_trigger: 'enter' or 'leave' car trigger.
        wake_trigger: True to remind when the user wakes up.
        clear_triggers: True to drop the suggested triggers before applying the ones given here.
        description: New description/details.
        start_time: Event start 'YYYY-MM-DDTHH:MM:SS' (events).
        end_time: Event end 'YYYY-MM-DDTHH:MM:SS' (events).
    """
    sid = suggestion_actions.resolve_suggestion_id(conn.user, suggestion_id)
    if not sid:
        return f"Error: no suggestion with ID '{suggestion_id}'. Use list_call_suggestions."
    s = phone_suggestions.get_suggestion(conn.user, sid)
    overrides: Dict[str, Any] = {}
    for key, val in (("title", title), ("due_date", due_date), ("priority", priority), ("group", group),
                     ("description", description), ("start", start_time), ("end", end_time)):
        if val is not None and str(val).strip():
            overrides[key] = str(val).strip()
    if s and s["kind"] == "task":
        triggers = {} if clear_triggers else dict(s["payload"].get("triggers") or {})
        if time_trigger:
            triggers["time"] = time_trigger.strip()
        if location:
            triggers["location"] = {"location": location.strip(), "event": (location_event or "arrive").strip().lower()}
        if car_trigger:
            triggers["car"] = car_trigger.strip().lower()
        if wake_trigger is not None:
            triggers["wake"] = bool(wake_trigger)
        overrides["triggers"] = triggers
    try:
        # Undos recorded inside (the calendar event) are folded into one
        # undo for the whole accept, so they're collected apart here.
        with undo_registry.collect() as inner:
            result = await suggestion_actions.accept_suggestion(conn.user, sid, overrides)
    except Exception as e:
        return f"Error: {e}"
    if s and not result.get("already") and result.get("status") in ("accepted", "dismissed"):
        _record_accept_undo(conn.user, s, result, [u["id"] for u in inner])
    if result.get("already"):
        return f"That suggestion was already {result['status']} ({result.get('result_ref') or ''})."
    if result.get("kind") == "event":
        return f"Added to the calendar. {result.get('message', '')}"
    if result.get("kind") == "task":
        src = (f"notification {s['source_id']}" if s and s.get("source_type") == "notification"
               else f"call {s['call_id'] if s else ''}")
        return f"Created task (ID: {result['task_id']}) from the suggestion, linked to {src}."
    return f"Suggestion {result.get('status')}."


@addon.tool(is_write=True)
async def dismiss_call_suggestion(conn: Connection, suggestion_id: str) -> str:
    """Dismiss a call or notification suggestion the user doesn't want.

    Args:
        suggestion_id: The suggestion ID.
    """
    sid = suggestion_actions.resolve_suggestion_id(conn.user, suggestion_id)
    before = phone_suggestions.get_suggestion(conn.user, sid) if sid else None
    if not sid or not before or not suggestion_actions.dismiss_suggestion(conn.user, sid):
        return f"Error: no suggestion with ID '{suggestion_id}'."
    if before and before["status"] == "pending":
        def _undo():
            current = phone_suggestions.get_suggestion(conn.user, sid)
            if current and current["status"] == "dismissed":
                suggestion_actions.reopen_suggestion(conn.user, sid, before["payload"])

        safe_record(f"Dismissed suggestion “{before['payload'].get('title') or ''}”", _undo, "dismiss_call_suggestion")
    return "Dismissed."


def _record_accept_undo(user: User, before: Dict[str, Any], result: Dict[str, Any], inner_undo_ids: list) -> None:
    """One undo for an accepted suggestion: remove what it created (task or
    calendar event) and put the suggestion back to pending as it was."""
    sid = before["id"]
    after = phone_suggestions.get_suggestion(user, sid) or {}
    task_id = result.get("task_id")
    created_task = undoable.row("tasks", "id", task_id, TASK_COLUMNS) if task_id else None
    if result.get("kind") == "event" and not inner_undo_ids:
        return  # no way to take the event back (e.g. it invited people)

    async def _undo():
        current = phone_suggestions.get_suggestion(user, sid)
        if not current or current["status"] != after.get("status") or current.get("result_ref") != after.get("result_ref"):
            raise undoable.UndoConflict("The suggestion changed since, so it was left as it is.")
        if task_id:
            tasks_service.check_still_writable(user, task_id)
            now = undoable.row("tasks", "id", task_id, TASK_COLUMNS)
            if now is not None and not undoable.same(now, created_task or {}, TASK_COLUMNS):
                raise undoable.UndoConflict("The task was changed since it was created, so it was kept.")
            tasks_service.remove_task_rows(task_id)
        for uid in inner_undo_ids:
            try:
                await undo_registry.run(uid)
            except KeyError:
                pass
        suggestion_actions.reopen_suggestion(user, sid, before["payload"])

    title = before["payload"].get("title") or "the suggestion"
    label = {"event": f"Added “{title}” to the calendar", "task": f"Created task “{title}”"}.get(
        result.get("kind"), f"Handled “{title}”")
    undoable.safe_record(label, _undo, "accept_call_suggestion")
