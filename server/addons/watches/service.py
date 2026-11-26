"""Creating, changing and describing a user's watches — what the tools and
the HTTP routes share (docs/watches.md)."""

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core import clock, undo
from core.users import User
from addons.watches import actions, compiler, engine, store
from addons.watches import spec as S

logger = logging.getLogger(__name__)


class Invalid(ValueError):
    """A spec or change that is refused; ``errors`` says why."""

    def __init__(self, errors: List[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def watch_json(user: User, row: Mapping[str, Any], with_load: bool = True) -> Dict[str, Any]:
    """A watch as the API answers it."""
    sp = row["spec"]
    return {"id": row["id"], "title": row["title"], "kind": row["kind"], "status": row["status"],
            "request": row["request"], "spec": sp, "created_at": row["created_at"],
            "window": {"start": sp.get("window_start"), "end": sp.get("window_end")},
            "fires_count": row["fires_count"], "last_fired_at": row["last_fired_at"], "next_time": row["next_time"],
            "task_id": row["task_id"], "load": engine.load(user, row) if with_load else {}}


def fire_json(f: Mapping[str, Any]) -> Dict[str, Any]:
    """One fire as the API answers it."""
    return {"id": f["id"], "at": f["at"], "trigger_id": f["trigger_id"], "event_type": f["event_type"] or None,
            "event": f["event"], "actions": [{"type": a.get("type"), "status": a.get("status"),
                                              "detail": a.get("detail", ""), "run_id": a.get("run_id"),
                                              "session_id": a.get("session_id")}
                                             for a in f["actions"]]}


def _next(sp: Mapping[str, Any]) -> Optional[str]:
    return S.fmt(S.next_time(sp, clock.now(), set()))


async def _with_task(user: User, sp: Dict[str, Any], task_id: Optional[str]) -> Optional[str]:
    """A reminder's task: the one it has, else a new one through the tasks addon (None: no tasks addon)."""
    if sp.get("kind") != "reminder":
        return task_id
    if task_id:
        return task_id
    return await actions.create_task(user, sp.get("task_title") or sp["title"])


async def save_new(user: User, sp: Dict[str, Any], request: str) -> Dict[str, Any]:
    """Store a validated spec as a new active watch (a reminder gets its task now); returns the row."""
    task_id = await _with_task(user, sp, sp.get("task_id"))
    sp["task_id"] = task_id
    return store.create(user, title=sp["title"], kind=sp["kind"], request=request, spec=sp, task_id=task_id,
                        next_time=_next(sp))


async def create_from_text(user: User, text: str) -> Tuple[Dict[str, Any], compiler.Compiled]:
    """Compile ``text`` and store the watch.

    Raises:
        compiler.CompileError: no valid spec could be built.
    """
    compiled = await compiler.compile_request(user, text)
    row = await save_new(user, compiled.spec, text)
    return row, compiled


def validate(user: User, raw: Any) -> Dict[str, Any]:
    """A spec given as is (the page's editor), validated for ``user``.

    Raises:
        Invalid: what is wrong with it.
    """
    types = engine.catalog(user)
    sp, errors = S.validate(raw, clock.now(), types, compiler.available_tools(user),
                            S.known_values(types.values(), user))
    if errors:
        raise Invalid(errors)
    return sp


async def set_spec(user: User, row: Mapping[str, Any], sp: Dict[str, Any]) -> Dict[str, Any]:
    """Replace a watch's spec with a validated one (keeps its task, re-arms its times)."""
    task_id = await _with_task(user, sp, row.get("task_id"))
    sp["task_id"] = task_id
    status = row["status"] if row["status"] in (store.ACTIVE, store.PAUSED) else store.ACTIVE
    out = store.update(user, row["id"], spec=sp, title=sp["title"], kind=sp["kind"], task_id=task_id,
                       next_time=_next(sp), status=status, closed_at=None)
    return out or dict(row)


async def change(user: User, row: Mapping[str, Any], words: str) -> Dict[str, Any]:
    """Change a watch by plain words (compiled again from its spec).

    Raises:
        compiler.CompileError: no valid spec could be built.
    """
    compiled = await compiler.compile_request(user, words, old=row["spec"])
    out = await set_spec(user, row, compiled.spec)
    store.update(user, row["id"], request=f"{row['request']}\n+ {words}".strip())
    return store.get(user, row["id"]) or out


def set_status(user: User, row: Mapping[str, Any], status: str) -> Dict[str, Any]:
    """Pause or resume a watch (resuming a closed one makes it active again)."""
    if status not in (store.ACTIVE, store.PAUSED):
        raise Invalid(["status must be active or paused"])
    sp = row["spec"]
    out = store.update(user, row["id"], status=status, closed_at=None,
                       next_time=_next(sp) if status == store.ACTIVE else row["next_time"])
    return out or dict(row)


def delete(user: User, watch_id: str) -> Optional[str]:
    """Delete a watch; returns the undo id (None: not the user's)."""
    gone = store.delete(user, watch_id)
    if gone is None:
        return None
    return undo.record(f"Deleted watch “{gone['title']}”", lambda: store.restore(user, gone), source="delete_watch")


def describe(row: Mapping[str, Any]) -> str:
    """A watch in a few lines for the agent: title, status, window, triggers in words, what it cannot do."""
    sp = row["spec"]
    lines = [f"“{row['title']}” (ID: {row['id']}, {row['kind']}, {row['status']}, "
             f"{sp.get('window_start')} → {sp.get('window_end')}, {sp.get('repeat')}"
             + (f", fired {row['fires_count']}×" if row["fires_count"] else "") + ")"]
    lines += [f"  - {d}" for d in S.describe(sp)]
    acts = [a.get("type") for a in sp.get("on_match") or []]
    lines.append(f"  then: {', '.join(acts)}" + (f"; if nothing by the end: "
                                                    f"{', '.join(a.get('type') for a in sp['on_deadline'])}"
                                                    if sp.get("on_deadline") else ""))
    if row.get("task_id"):
        lines.append(f"  task: {row['task_id']}")
    if sp.get("unsupported"):
        lines.append(f"  cannot do: {sp['unsupported']}")
    return "\n".join(lines)
