"""The user's coding-agent sessions (Claude Code, Codex), live on their phone.

The agents' hooks (``bin/dana-agent-hook``) post every hook event as it is to
``POST /api/agent-sessions/hook?agent=claude|codex&host=``; :func:`record`
turns it into one state per session (row in ``agent_sessions``):

    running   a prompt was submitted; ``activity`` says what it does now
              ("Editing push.py", "Running bin/test"), ``steps_done`` /
              ``steps_total`` come from its plan (Claude's TodoWrite, Codex's
              update_plan) when it has one
    waiting   it needs the user: a permission prompt, a question, a plan to review
    done      it stopped (``summary``: the start of its last message)
    failed    the turn ended on an API error

and pushes the whole list (:func:`snapshot`) to the phone as one
``agent_sessions`` event that replaces the earlier ones, so a lost ping or a
phone that was offline still ends up with the right state. A change of status
is pushed at once; changes of activity at most every ``PUSH_INTERVAL``
seconds per user (the last one always arrives, a moment later). ``rev`` rises
with every status change, so the phone alerts once per change.

Finished sessions stay in the list ``DONE_KEEP`` seconds (a phone that comes
online later still hears it finished); running ones that went silent for
``STALE_AFTER`` seconds (the laptop slept, the agent was killed) and ended
ones (``SessionEnd``) are left out. Everything is the user's own: a session
id is only looked up under the caller's user id.

Subagents are rows of their own with a ``parent`` and show inside their
parent's entry (``subagents``), never as sessions of their own: one of Claude
Code's subagents (its events carry ``agent_id``; row ``<parent id>/<agent
id>``) or a T3 Code delegated child (the hook sends its root thread as
``parent_thread``; the parent is the top-level session running in that
thread). The parent's ``status`` is the group's: waiting when any of them
waits, running while any runs, and running for ``GRACE`` seconds after a
subagent finished when the parent had already stopped (the agent reads the
result and goes on, so that is not "done" yet). A subagent turning to waiting
raises the parent's ``rev`` (the phone alerts); a finished group is kept and
pruned as one. A subagent whose parent isn't listed shows on its own.
"""

import asyncio
import datetime
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.db import get_db_connection
from core.users import User
from integrations.phone import push

logger = logging.getLogger(__name__)

AGENTS = ("claude", "codex")
STATUSES = ("running", "waiting", "done", "failed")
PUSH_INTERVAL = float(os.getenv("AGENT_SESSIONS_PUSH_INTERVAL", "15"))
DONE_KEEP = 15 * 60
STALE_AFTER = 60 * 60
GRACE = 90
TEXT_MAX = 120
SUMMARY_MAX = 300

# Tools that mean the agent is waiting for the user.
_ASKING = {"AskUserQuestion": "Asking you a question", "ExitPlanMode": "Plan ready for review",
           "request_user_input": "Asking you a question"}
_PATH_VERBS = {"Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing", "NotebookEdit": "Editing",
               "Read": "Reading"}

# Per user: when the last push went out and the trailing push waiting to go.
_last_push: Dict[str, float] = {}
_pending: Dict[str, asyncio.Task] = {}
# Per user: the push that goes out when a finished subagent's grace ends.
_grace: Dict[str, asyncio.Task] = {}
LIVE = ("running", "waiting")
_FIELDS = ("id", "agent", "host", "project", "title", "status", "activity", "summary", "steps_done", "steps_total",
           "tools", "rev", "started_at", "updated_at", "open_url")
_SUB_FIELDS = ("id", "title", "status", "activity", "summary", "steps_done", "steps_total", "started_at",
               "updated_at")


def _utc(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")


def _line(text: Any, limit: int = TEXT_MAX) -> str:
    """The first non-empty line of ``text``, cut to ``limit`` characters."""
    for line in str(text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"
    return ""


def _base(path: Any) -> str:
    return os.path.basename(str(path or "").rstrip("/")) or ""


def _plan(tool: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Steps done / total and the current step of a plan tool's input, or None."""
    if tool == "TodoWrite":
        items = [(t.get("status"), t.get("activeForm") or t.get("content")) for t in args.get("todos") or []
                 if isinstance(t, dict)]
    elif tool == "update_plan":
        items = [(t.get("status"), t.get("step")) for t in args.get("plan") or [] if isinstance(t, dict)]
    else:
        return None
    current = next((label for status, label in items if status == "in_progress"), "")
    return {"steps_done": sum(1 for status, _ in items if status == "completed"), "steps_total": len(items),
            "activity": _line(current)}


def activity(tool: str, args: Dict[str, Any]) -> str:
    """What a tool call does, in a few words for the notification."""
    if tool in _ASKING:
        return _ASKING[tool]
    if tool in _PATH_VERBS:
        name = _base(args.get("file_path") or args.get("notebook_path") or args.get("path"))
        return f"{_PATH_VERBS[tool]} {name}".strip()
    if tool in ("Bash", "shell", "exec_command", "local_shell"):
        command = args.get("command")
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        return _line(args.get("description") or f"Running {_line(command, 80)}")
    if tool == "apply_patch":
        return "Editing files"
    if tool in ("Grep", "Glob", "LS"):
        return "Searching the code"
    if tool in ("WebSearch", "WebFetch", "web_search"):
        return "Searching the web"
    if tool in ("Agent", "Task"):
        return _line(f"Subagent: {args.get('description') or args.get('subagent_type') or 'working'}")
    if tool.startswith("mcp__"):
        return _line(f"Using {tool.split('__')[-1]}")
    return _line(f"Using {tool}") if tool else ""


def _get(conn, user: User, sid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM agent_sessions WHERE user_id = ? AND id = ?", (user.id, sid)).fetchone()
    return dict(row) if row else None


def _open_url(url: str) -> str:
    """``url`` when it is an https link of sane length, else empty."""
    url = (url or "").strip()
    return url if url.startswith("https://") and len(url) <= 300 and not any(c.isspace() for c in url) else ""


def _ident(value: Any, limit: int = 300) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit and value.isprintable() and " " not in value else ""


def record(user: User, agent: str, host: str, event: Dict[str, Any], open_url: str = "", thread: str = "",
           parent_thread: str = "", title: str = "") -> Optional[str]:
    """Apply one hook event to the session's row. ``open_url``: where tapping
    its notification goes (the T3 Code thread; https only, kept when a later
    event has none). ``thread``: the T3 Code thread it runs in;
    ``parent_thread``: its root thread when it is a delegated child;
    ``title``: the thread's title (used over the prompt). Returns ``"status"``
    when the status changed (push now), ``"activity"`` when only what it shows
    changed (push soon), None when nothing the phone shows changed."""
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    session_id = str(event.get("session_id") or "").strip()[:100]
    name = str(event.get("hook_event_name") or "")
    if not session_id or not name:
        raise ValueError("session_id and hook_event_name are required")
    sid = f"{agent}:{session_id}"
    agent_id = _ident(event.get("agent_id"), 100)
    thread, parent_thread = _ident(thread), _ident(parent_thread)
    if agent_id:
        parent, sid = sid, f"{sid}/{agent_id}"
    elif parent_thread and parent_thread != thread:
        parent = f"t3:{parent_thread}"
    else:
        parent = ""
    now = _utc()
    tool = str(event.get("tool_name") or "")
    args = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}

    with get_db_connection() as conn:
        row = _get(conn, user, sid)
        if name == "SessionEnd":
            gone = conn.execute("DELETE FROM agent_sessions WHERE user_id = ? AND (id = ? OR id LIKE ?)",
                                (user.id, sid, sid + "/%")).rowcount
            conn.commit()
            return "status" if gone else None
        change: Dict[str, Any] = {}
        if name == "SubagentStart" and agent_id:
            change = {"status": "running", "title": _line(event.get("agent_type")) or "Subagent",
                      "activity": "Starting", "summary": "", "steps_done": 0, "steps_total": 0, "tools": 0,
                      "started_at": now}
        elif name == "SubagentStop" and agent_id:
            change = {"status": "done", "summary": _line(event.get("last_assistant_message"), SUMMARY_MAX),
                      "activity": "Done"}
        elif name == "UserPromptSubmit":
            change = {"status": "running", "title": _line(event.get("prompt")), "activity": "Thinking",
                      "summary": "", "steps_done": 0, "steps_total": 0, "tools": 0, "started_at": now}
        elif name in ("PreToolUse", "PostToolUse"):
            plan = _plan(tool, args)
            if plan:
                change = {k: v for k, v in plan.items() if v or k != "activity"}
            elif name == "PreToolUse":
                change = {"activity": activity(tool, args)}
            asking = name == "PreToolUse" and tool in _ASKING
            change["status"] = "waiting" if asking else "running"
            if name == "PreToolUse":
                change["tools"] = (row["tools"] if row else 0) + 1
        elif name == "PermissionRequest":
            change = {"status": "waiting", "activity": _line(f"Needs permission: {activity(tool, args) or tool}")}
        elif name == "Notification":
            if event.get("notification_type") in ("permission_prompt", "elicitation_dialog") \
                    or "permission" in str(event.get("message") or "").lower():
                change = {"status": "waiting", "activity": _line(event.get("message") or "Needs you")}
        elif name in ("Stop", "StopFailure"):
            failed = name == "StopFailure"
            text = event.get("error") or event.get("error_message") if failed else event.get("last_assistant_message")
            change = {"status": "failed" if failed else "done", "summary": _line(text, SUMMARY_MAX),
                      "activity": "Failed" if failed else "Done"}
        if not change:
            return None
        if row is None:
            if change.get("status") not in ("running", "waiting", "done", "failed"):
                return None
            row = {"user_id": user.id, "id": sid, "agent": agent, "host": "", "project": "", "open_url": "",
                   "title": _line(event.get("agent_type")) if agent_id else "", "thread": "", "parent": "",
                   "status": change["status"], "activity": "", "summary": "", "steps_done": 0, "steps_total": 0,
                   "tools": 0, "rev": 0, "started_at": now, "updated_at": now}
            new = True
        else:
            new = False
        before = {k: row[k] for k in ("status", "activity", "title", "steps_done", "steps_total", "summary", "open_url")}
        status_changed = new or change.get("status", row["status"]) != row["status"]
        row.update(change)
        row["updated_at"] = now
        row["host"] = _line(host, 60) or row["host"]
        row["project"] = _base(event.get("cwd")) or row["project"]
        row["open_url"] = _open_url(open_url) or row["open_url"]
        row["thread"] = thread or row["thread"]
        row["parent"] = parent or row["parent"]
        if title and not agent_id:
            row["title"] = _line(title)
        if status_changed:
            row["rev"] = int(row["rev"]) + 1
        conn.execute(
            "INSERT INTO agent_sessions (user_id, id, agent, host, project, title, status, activity, summary, "
            "steps_done, steps_total, tools, rev, started_at, updated_at, open_url, thread, parent) "
            "VALUES (:user_id, :id, :agent, :host, :project, :title, :status, :activity, :summary, :steps_done, "
            ":steps_total, :tools, :rev, :started_at, :updated_at, :open_url, :thread, :parent) "
            "ON CONFLICT(user_id, id) DO UPDATE SET "
            "host = excluded.host, project = excluded.project, title = excluded.title, status = excluded.status, "
            "activity = excluded.activity, summary = excluded.summary, steps_done = excluded.steps_done, "
            "steps_total = excluded.steps_total, tools = excluded.tools, rev = excluded.rev, "
            "started_at = excluded.started_at, updated_at = excluded.updated_at, open_url = excluded.open_url, "
            "thread = excluded.thread, parent = excluded.parent", row)
        if status_changed and row["parent"] and row["status"] == "waiting":
            _bump(conn, user, row["parent"])
        conn.commit()
    if status_changed and row["parent"] and row["status"] in ("done", "failed"):
        _grace_push(user)
    if status_changed:
        return "status"
    after = {k: row[k] for k in before}
    return "activity" if after != before else None


def _bump(conn, user: User, parent: str) -> None:
    """Raise the parent's ``rev`` (one of its subagents needs the user)."""
    if parent.startswith("t3:"):
        conn.execute("UPDATE agent_sessions SET rev = rev + 1 WHERE user_id = ? AND parent = '' AND thread = ?",
                     (user.id, parent[3:]))
    else:
        conn.execute("UPDATE agent_sessions SET rev = rev + 1 WHERE user_id = ? AND id = ?", (user.id, parent))


def _groups(rows: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """``rows`` (newest first) as (top-level session, its subagents, oldest first)."""
    by_id = {r["id"]: r for r in rows}
    by_thread: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not r["parent"] and r["thread"]:
            by_thread.setdefault(r["thread"], r)  # the newest session in that thread

    def owner(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        p = r["parent"]
        return by_thread.get(p[3:]) if p.startswith("t3:") else by_id.get(p) if p else None

    def top(r: Dict[str, Any]) -> Dict[str, Any]:
        for _ in range(5):
            o = owner(r)
            if o is None or o is r:
                break
            r = o
        return r

    kids: Dict[str, List[Dict[str, Any]]] = {}
    tops = []
    for r in rows:
        t = top(r)
        if t is r:
            tops.append(r)
        else:
            kids.setdefault(t["id"], []).append(r)
    return [(t, kids.get(t["id"], [])[::-1]) for t in tops]


def _group_state(top: Dict[str, Any], subs: List[Dict[str, Any]], now: datetime.datetime) -> Tuple[str, str]:
    """The group's status and activity (see the module docstring)."""
    if top["status"] == "waiting":
        return "waiting", top["activity"]
    waiting = next((s for s in subs if s["status"] == "waiting"), None)
    if waiting:
        return "waiting", _line(f"{waiting['title']}: {waiting['activity']}")
    if top["status"] == "running":
        return "running", top["activity"]
    running = [s for s in subs if s["status"] == "running"]
    if running:
        names = ", ".join(s["title"] for s in running)
        return "running", _line(f"Waiting for {names}" if len(running) <= 2 else f"Waiting for {len(running)} subagents")
    grace = _utc(now - datetime.timedelta(seconds=GRACE))
    if any(s["updated_at"] > top["updated_at"] and s["updated_at"] >= grace for s in subs):
        return "running", "Reading the subagents' results"
    return top["status"], top["activity"]


def snapshot(user: User) -> List[Dict[str, Any]]:
    """The sessions the phone shows, newest first, each with its
    ``subagents``: groups with someone running / waiting heard from in the last
    ``STALE_AFTER`` seconds and finished groups of the last ``DONE_KEEP``
    seconds. Older rows are deleted."""
    now = datetime.datetime.now(datetime.timezone.utc)
    stale, done = _utc(now - datetime.timedelta(seconds=STALE_AFTER)), _utc(now - datetime.timedelta(seconds=DONE_KEEP))
    with get_db_connection() as conn:
        conn.execute("DELETE FROM agent_sessions WHERE user_id = ? AND (updated_at < ? AND status IN ('running', "
                     "'waiting') OR updated_at < ?)", (user.id, stale, _utc(now - datetime.timedelta(days=1))))
        rows = [dict(r) for r in conn.execute("SELECT * FROM agent_sessions WHERE user_id = ? "
                                              "ORDER BY started_at DESC, rowid DESC", (user.id,)).fetchall()]
        out, old = [], []
        for top, subs in _groups(rows):
            status, act = _group_state(top, subs, now)
            if status not in LIVE and max(r["updated_at"] for r in [top, *subs]) < done:
                old += [top["id"], *(s["id"] for s in subs)]
                continue
            entry = {k: top[k] for k in _FIELDS}
            entry.update(status=status, activity=act, subagents=[{k: s[k] for k in _SUB_FIELDS} for s in subs])
            out.append(entry)
        if old:
            conn.execute(f"DELETE FROM agent_sessions WHERE user_id = ? AND id IN ({','.join('?' * len(old))})",
                         (user.id, *old))
        conn.commit()
    return out


def remove(user: User, sid: str) -> bool:
    """Forget one of the user's sessions and its subagents (the phone's Hide).
    False: no such session of theirs."""
    with get_db_connection() as conn:
        row = _get(conn, user, sid)
        if row is None:
            return False
        conn.execute("DELETE FROM agent_sessions WHERE user_id = ? AND (id = ? OR id LIKE ?)",
                     (user.id, sid, sid + "/%"))
        if row["thread"] and not row["parent"]:
            conn.execute("DELETE FROM agent_sessions WHERE user_id = ? AND parent = ?", (user.id, f"t3:{row['thread']}"))
        conn.commit()
    return True


async def push_now(user: User) -> bool:
    """Send the whole list to the user's phone (replacing the earlier list)."""
    _last_push[user.id] = time.monotonic()
    return await push.send(user, "agent_sessions", {"sessions": snapshot(user)}, replace=True)


async def _push_later(user: User, delay: float, tasks: Optional[Dict[str, asyncio.Task]] = None) -> None:
    try:
        await asyncio.sleep(delay)
        (_pending if tasks is None else tasks).pop(user.id, None)
        await push_now(user)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(f"Agent sessions: push for user {user.id} failed")


def _grace_push(user: User) -> None:
    """Push again when a finished subagent's grace ends (the group may be done by then)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = _grace.pop(user.id, None)
    if task:
        task.cancel()
    _grace[user.id] = loop.create_task(_push_later(user, GRACE + 1, _grace))


async def schedule(user: User, change: Optional[str]) -> None:
    """Push after a :func:`record`: at once for a status change, otherwise at
    most every ``PUSH_INTERVAL`` seconds (a trailing push carries the latest)."""
    if not change:
        return
    waited = time.monotonic() - _last_push.get(user.id, 0.0)
    if change == "status" or waited >= PUSH_INTERVAL:
        task = _pending.pop(user.id, None)
        if task:
            task.cancel()
        await push_now(user)
    elif user.id not in _pending:
        _pending[user.id] = asyncio.create_task(_push_later(user, PUSH_INTERVAL - waited))
