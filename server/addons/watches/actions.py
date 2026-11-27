"""What a watch does when it fires (docs/watches.md, "Actions").

Each action returns ``{type, status, detail, run_id}`` with status ``done``,
``held`` (waiting for the user: an approval, a Close on the Now card),
``failed`` or ``skipped``.

- ``notify``: ``core.notify.send``; a reminder goes as kind ``reminder`` like
  the tasks addon's, so phones show it full screen.
- ``create_task`` / ``complete_task``: through ``registry.execute(user,
  "add_task" / "update_task")``, so the tasks addon stays its own.
- ``tool``: run at once when it neither asks nor acts outside Dana and the
  action says ``act_and_tell``; otherwise through an agent run, so the
  normal Approve card appears.
- ``agent``: a run of Dana's agent for the user (``core.session_runner``) in
  the watch's own session ``watch-<watch id>`` titled "👀 <watch title>"
  (``watch-<id>-2``, ``-3`` while that one is busy), never the shared thread.
  Its message starts with who the user is and how to reach them
  (``identity.py``). The triggering event is text others wrote, framed with
  ``core.text.untrusted``, so that run asks before every tool that acts
  outside Dana. ``ask_first`` without an event frames the watch's request the
  same way.
- ``suggest_complete``: held; the Now card "Done?" closes the task and the watch.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Set

from core import approval_text, clock, live, notify, runs, session_runner
from core.addons import registry
from core.session import create_new_session_db, get_pending, set_session_title
from core.text import untrusted
from core.users import User
from addons.watches import identity
from addons.watches import spec as S
from addons.watches import store
from addons.watches.addon import addon

logger = logging.getLogger(__name__)

DONE, HELD, FAILED, SKIPPED = "done", "held", "failed", "skipped"
_TASK_ID = re.compile(r"\(ID: ([^)]+)\)")
# The agent runs started by actions, kept until they end (they update their fire).
_followers: Set["asyncio.Task[None]"] = set()


def result(kind: str, status: str, detail: str = "", run_id: Optional[str] = None,
           session_id: Optional[str] = None) -> Dict[str, Any]:
    """One action's outcome as a fire stores it (``run_id`` / ``session_id``: the agent run it started)."""
    return {"type": kind, "status": status, "detail": detail[:300], "run_id": run_id, "session_id": session_id}


def _event_lines(event: Optional[S.Event]) -> str:
    if event is None:
        return ""
    data = {k: (v[:600] if isinstance(v, str) else v) for k, v in event.data.items()}
    return f"{event.type} at {event.ts.strftime('%Y-%m-%d %H:%M')}: {json.dumps(data, ensure_ascii=False)}"


async def notify_action(user: User, watch: Dict[str, Any], action: Dict[str, Any],
                        event: Optional[S.Event], trigger_id: str) -> Dict[str, Any]:
    """Tell the user (a reminder full screen on the phone, anything else as a notification)."""
    text = str(action.get("text") or watch["title"]).strip()
    if watch["kind"] == "reminder":
        data = {"task_id": str(watch.get("task_id") or ""), "task_title": watch["spec"].get("task_title") or text,
                "priority": "high" if action.get("urgency") == "now" else "medium", "trigger_type": "watch",
                "watch_id": watch["id"], "text": text}
        message = notify.Message(f"🔔 {text}", kind="reminder", data=data)
    else:
        why = f"\n{S.summary(event.type, event.data)}" if event is not None else ""
        message = notify.Message(f"👀 {watch['title']}\n{text}{why}", kind="notify",
                                 data={"title": watch["title"][:200], "text": (text + why)[:4000],
                                       "tag": f"watch-{watch['id']}",
                                       "channel": "messages" if action.get("urgency") == "now" else "updates"})
    sent = await notify.send(user, message)
    if any(sent.values()):
        return result("notify", DONE, ", ".join(k for k, v in sent.items() if v))
    return result("notify", FAILED, "no channel delivered it (no Telegram chat or phone)")


async def create_task(user: User, title: str) -> Optional[str]:
    """Add a task through the tasks addon; returns its id (None: not added or no tasks addon)."""
    if not registry.has_tool(user, "add_task"):
        return None
    out = str(await registry.execute(user, "add_task", {"title": title}))
    m = _TASK_ID.search(out)
    return m.group(1) if m and not out.startswith("Error") else None


async def complete_task(user: User, task_id: str) -> str:
    """Complete a task through the tasks addon; returns the tool's answer."""
    if not registry.has_tool(user, "update_task"):
        return "Error: no tasks"
    return str(await registry.execute(user, "update_task", {"item": task_id, "status": "completed"}))


SESSION_TRIES = 3


def session_ids(watch: Mapping[str, Any]) -> List[str]:
    """The sessions a watch's agent runs use, in order: ``watch-<id>``, then ``watch-<id>-2``… while busy."""
    base = f"watch-{watch['id']}"
    return [base] + [f"{base}-{i}" for i in range(2, SESSION_TRIES + 1)]


def session_title(watch: Mapping[str, Any]) -> str:
    """The title of a watch's agent session ("👀 <watch title>")."""
    return f"👀 {watch['title']}"[:120]


def _ensure_session(user: User, session_id: str, title: str) -> None:
    """Create the watch's session with its title (an existing one gets the watch's current title)."""
    state = create_new_session_db(user.id, session_id, title=title)
    if state.get("title") != title:
        set_session_title(user.id, session_id, title)


def _agent_message(user: User, watch: Dict[str, Any], instructions: str, autonomy: str,
                   event: Optional[S.Event]) -> str:
    tools = [t.name for t in registry.tools_for(user)]
    lines = [f"[Watch “{watch['title']}”] Something you were asked to watch for happened; act on it now. "
             "This is the watch's own session, not the user's chat: the user is not here to answer now.",
             identity.about_user(user, tools) + ("\n" + identity.home_line(user) if identity.home_line(user) else ""),
             f"What to do: {instructions}",
             "Reply with one short line saying what you did or prepared."]
    if autonomy == "act_and_tell":
        lines.append("The user said to just do it and tell them after.")
    else:
        lines.append("Ask before sending messages or changing anything outside Dana (the Approve card does it).")
    if event is not None:
        lines.append(untrusted(f"the event that fired this watch ({event.type})", _event_lines(event)))
    elif autonomy != "act_and_tell":
        lines.append(untrusted("the watch's request as it was saved", watch.get("request") or watch["title"]))
    else:
        lines.append(f"The user's original request: {watch.get('request') or watch['title']}")
    return "\n\n".join(lines)


def _live_key(run: runs.Run) -> str:
    """The live activity of one agent run: ``run:<session id>:<run id>`` (the Approve action finds the session in it)."""
    return f"run:{run.session_id}:{run.run_id}"[:100]


def open_path(session_id: str) -> str:
    """Where tapping a watch run's live activity leads: its session in the chat (``/chat/<session id>``)."""
    return f"/chat/{session_id}"


def _working(title: str, step: str, since: Any, opens: str = "/watches") -> live.Activity:
    return live.Activity(title=title[:120], text=step[:240], context="Watch · Dana is working", icon="bot",
                         since=since, progress=live.Progress(indeterminate=True), open=opens)


async def _live(op: Any, *args: Any) -> None:
    """A live-activity call that never breaks the action (a client-only feature)."""
    try:
        await op(*args)
    except Exception as e:
        logger.warning(f"Watch live activity: {e}")


async def follow(user: User, run: runs.Run, title: str, fire_id: str = "", index: int = 0,
                 watch: Optional[Dict[str, Any]] = None) -> None:
    """Show the agent run as a live activity (phone Live Update, the web Now strip, the Omarchy bar) while it
    works; when it ends store how it went on its fire, and when it waits for an approval turn the activity into
    Approve / Deny and tell the user (Telegram with the same buttons)."""
    key, since, seq, final = _live_key(run), clock.now(), 0, ""
    opens = open_path(run.session_id)
    await _live(live.start, user, addon, key, _working(title, "Starting…", since, opens))
    while True:
        evs = await run.wait_for_events(seq, 30.0)
        for ev in evs:
            seq = ev["seq"]
            if ev.get("type") == "tool_start" and ev.get("name"):
                await _live(live.update, user, addon, key, _working(title, f"Using {ev['name']}", since, opens))
            elif ev.get("type") == "final" and ev.get("content"):
                final = str(ev["content"])
        if run.finished and seq >= run.last_seq:
            break
    if run.task is not None:
        try:
            await asyncio.wait([run.task])
        except Exception:
            pass
    pending = get_pending(user.id, run.session_id) or {}
    waiting = bool(pending)
    status = HELD if waiting else (FAILED if run.status in ("error", "cancelled") else DONE)
    fire = store.get_fire(user, fire_id) if fire_id else None
    if fire is not None and index < len(fire["actions"]):
        fire["actions"][index]["status"] = status
        if waiting:
            fire["actions"][index]["detail"] = "waiting for your approval"
        store.set_fire_actions(user, fire_id, fire["actions"])
    asks = pending.get("status") == "pending_approval" and bool(pending.get("tool_name"))
    if asks:
        what = approval_text.plain(approval_text.describe(pending["tool_name"], pending.get("tool_args")))
        await _live(live.update, user, addon, key, live.Activity(
            title=title[:120], text=what[:240], context="Watch · needs your OK", icon="bot", tone="warn",
            alert=f"ok:{run.run_id}", open=opens,
            actions=(live.Action("approve", "Approve"), live.Action("deny", "Deny"))))
    elif waiting:
        await _live(live.end, user, addon, key, live.Final("Dana is waiting for you in the chat", tone="warn",
                                                            open=opens))
    else:
        ok = status == DONE
        await _live(live.end, user, addon, key, live.Final(
            "Done" if ok else "It did not work", text=final[:240], tone="good" if ok else "bad", open=opens))


async def answer(user: User, current: live.Current, action: str) -> Optional[str]:
    """Approve / Deny pressed on a run's live activity: resume that session's run and follow it in a new
    activity. Returns an error word, or None."""
    parts = current.key.split(":", 2)
    if len(parts) != 3 or parts[0] != "run":
        return "unknown"
    session_id = parts[1]
    pending = get_pending(user.id, session_id)
    if pending is None or pending.get("status") != "pending_approval":
        return "gone"
    try:
        run = session_runner.start_resume_task(user, session_id, "approve" if action == "approve" else "deny",
                                               device="live")
    except runs.SessionBusyError:
        return "busy"
    task = asyncio.ensure_future(follow(user, run, current.activity.title))
    _followers.add(task)
    task.add_done_callback(_followers.discard)
    return None


def start_agent(user: User, watch: Dict[str, Any], instructions: str, autonomy: str, event: Optional[S.Event],
                fire_id: str, index: int, kind: str = "agent") -> Dict[str, Any]:
    """Start a background run of Dana's agent for the watch's user in the watch's own session (``session_ids``);
    the result holds its ``run_id`` and ``session_id``."""
    text = _agent_message(user, watch, instructions, autonomy, event)
    run = None
    title = session_title(watch)
    for session_id in session_ids(watch):
        if runs.runs.active(user.id, session_id) is not None:
            continue
        _ensure_session(user, session_id, title)
        try:
            run = session_runner.start_agent_task(user, session_id, text, device="watches")
            break
        except runs.SessionBusyError:
            continue
    if run is None:
        return result(kind, FAILED, f"Dana was busy in all {SESSION_TRIES} of this watch's sessions")
    task = asyncio.ensure_future(follow(user, run, watch["title"], fire_id, index, watch))
    _followers.add(task)
    task.add_done_callback(_followers.discard)
    return result(kind, DONE, f"Dana is on it (session {run.session_id})", run_id=run.run_id,
                  session_id=run.session_id)


async def run_one(user: User, watch: Dict[str, Any], action: Dict[str, Any], event: Optional[S.Event],
                  trigger_id: str, fire_id: str, index: int) -> Dict[str, Any]:
    """Run one action of a fire; never raises."""
    kind = str(action.get("type") or "")
    try:
        if kind == "notify":
            return await notify_action(user, watch, action, event, trigger_id)
        if kind == "create_task":
            tid = await create_task(user, str(action.get("text") or watch["title"]))
            return result(kind, DONE, f"task {tid}") if tid else result(kind, SKIPPED, "no tasks addon")
        if kind == "complete_task":
            if not watch.get("task_id"):
                return result(kind, SKIPPED, "this watch has no task")
            out = await complete_task(user, watch["task_id"])
            return result(kind, FAILED if out.startswith("Error") else DONE, out)
        if kind == "suggest_complete":
            return result(kind, HELD, "asks on the Now screen whether it is done")
        if kind == "tool":
            name = str(action.get("tool") or "")
            args = json.loads(action.get("args_json") or "{}")
            if not registry.has_tool(user, name):
                return result(kind, FAILED, f"{name} is not one of your tools now")
            asks = (registry.needs_confirmation(name, args) or registry.acts_outside(name)
                    or action.get("autonomy") != "act_and_tell")
            if asks:
                return start_agent(user, watch, f"Call the tool {name} with these arguments: "
                                   f"{json.dumps(args, ensure_ascii=False)}", str(action.get("autonomy")),
                                   event, fire_id, index, kind="tool")
            out = str(await registry.execute(user, name, args))
            return result(kind, FAILED if out.startswith("Error") else DONE, out)
        if kind == "agent":
            return start_agent(user, watch, str(action.get("instructions") or action.get("text")),
                               str(action.get("autonomy") or "ask_first"), event, fire_id, index)
        return result(kind, SKIPPED, "unknown action")
    except Exception as e:
        logger.exception(f"Watch {watch['id']}: action {kind} failed for user {user.id}")
        return result(kind, FAILED, str(e) or type(e).__name__)


async def run_all(user: User, watch: Dict[str, Any], actions: List[Dict[str, Any]], event: Optional[S.Event],
                  trigger_id: str, fire_id: str) -> List[Dict[str, Any]]:
    """Run a fire's actions in order; stores and returns their outcomes."""
    out: List[Dict[str, Any]] = []
    for i, action in enumerate(actions):
        out.append(await run_one(user, watch, action, event, trigger_id, fire_id, i))
        store.set_fire_actions(user, fire_id, out)
    return out


async def drain() -> None:
    """Wait for the agent runs actions started (tests)."""
    while _followers:
        await asyncio.gather(*list(_followers), return_exceptions=True)
