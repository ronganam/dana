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
  their shared thread (another session when the thread is busy). The
  triggering event is text others wrote, framed with ``core.text.untrusted``,
  so that run asks before every tool that acts outside Dana. ``ask_first``
  without an event frames the watch's request the same way.
- ``suggest_complete``: held; the Now card "Done?" closes the task and the watch.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set

from core import notify, runs, session_runner
from core.addons import registry
from core.session import get_pending
from core.text import untrusted
from core.users import User
from addons.watches import spec as S
from addons.watches import store

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


def _agent_message(watch: Dict[str, Any], instructions: str, autonomy: str, event: Optional[S.Event]) -> str:
    lines = [f"[Watch “{watch['title']}”] Something you were asked to watch for happened; act on it now.",
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


async def _follow(user: User, run: runs.Run, fire_id: str, index: int, watch: Dict[str, Any]) -> None:
    """When the agent run ends, store how it went on its fire and tell the user when it waits for an approval."""
    try:
        if run.task is not None:
            await asyncio.wait([run.task])
    except Exception:
        pass
    waiting = get_pending(user.id, run.session_id) is not None
    status = HELD if waiting else (FAILED if run.status in ("error", "cancelled") else DONE)
    fire = store.get_fire(user, fire_id)
    if fire is not None and index < len(fire["actions"]):
        fire["actions"][index]["status"] = status
        if waiting:
            fire["actions"][index]["detail"] = "waiting for your approval in the chat"
        store.set_fire_actions(user, fire_id, fire["actions"])
    if waiting:
        await notify.send(user, notify.Message(
            f"👀 {watch['title']}: Dana prepared something and needs your OK in the chat.", kind="notify",
            data={"title": watch["title"][:200], "text": "Dana needs your OK in the chat",
                  "tag": f"watch-{watch['id']}", "channel": "messages"}))


def start_agent(user: User, watch: Dict[str, Any], instructions: str, autonomy: str, event: Optional[S.Event],
                fire_id: str, index: int, kind: str = "agent") -> Dict[str, Any]:
    """Start a background run of Dana's agent for the watch's user; the result holds its ``run_id``."""
    text = _agent_message(watch, instructions, autonomy, event)
    run = None
    for session_id in ("main", f"watch-{watch['id']}"):
        try:
            run = session_runner.start_agent_task(user, session_id, text, device="watches")
            break
        except runs.SessionBusyError:
            continue
    if run is None:
        return result(kind, FAILED, "Dana was busy in both the chat and this watch's session")
    task = asyncio.ensure_future(_follow(user, run, fire_id, index, watch))
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
