"""Agent runs and their live event logs.

A *run* is one stretch of agent work for a session: answering a message, or
resuming after an approval / selection / step-limit decision. Every run owns
an append-only event log. Each event carries ``run_id`` and a ``seq`` (1, 2, …)
so a client can attach at any time, replay what it missed and keep following:

    run_start          {kind, model}
    transcription      {text}                         (voice only)
    step_start         {iteration, max_iterations}    one per LLM call
    reasoning_delta    {iteration, delta}             model's thinking, streamed
    text_delta         {iteration, delta}             reply text, streamed
    tool_call          {id, name, iteration}          model decided to call a tool
    tool_start         {id, name, args, iteration}    tool is executing
    tool_end           {id, name, ok, result, duration_ms, undos}
                       undos: [{id, label}] — what this call changed and can be
                       reversed with POST /api/undo/{id} (empty list if none).
                       After an approval it also has approved: bool.
    retry              {iteration, attempt, message}  discard this step's partial text
    final              {content, shortcut?}           the complete reply; shortcut: true
                                                      when a learned phrase answered it
                                                      without the model (core/shortcuts.py)
    error              {message}
    approval_required  {id, name, args, reason?}       reason: "device_screen" when the call asks
                                                      only because the turn carries the shared
                                                      phone screen (core/device_screen.py),
                                                      "untrusted_result" when only because a
                                                      tool of the turn read text other people
                                                      wrote (an email)
    selection_required {prompt, options, multiple}
    iterations_exceeded{iteration, max_iterations, summary}
    cancelled          {}
    run_end            {status}                       always last; session state is final

The log lives in memory and is kept for ``RUN_RETENTION_SECONDS`` after the run
ends, so a client that reconnects late still gets the tail.

The closing ``state`` frame every stream ends with (built in core/sessions_api.py)
carries ``undos`` — every still-valid undo entry of the session's latest run —
and ``shortcut`` (bool), so a client can put one Undo button under the reply.
See docs/agent-actions.md.

The run is bound to a :class:`~contextvars.ContextVar` when its task is
created. ``asyncio.create_task`` copies the context, so :func:`emit` anywhere
inside the run (agent loop, tool code) publishes to it without threading a
handle through every signature. Outside a run :func:`emit` is a no-op.

Every run is started for a user (:class:`core.users.User`): runs are keyed by
``(user id, session id)`` — one run per session, sessions are per user — and
the run's task also has that user bound as the current user
(:func:`core.users.current`), so undo entries, usage records and tool code
inside it know whose work it is.
"""

import asyncio
import contextvars
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from core import undo, users
from core.db import get_db_connection
from core.users import User

logger = logging.getLogger(__name__)

# Finished runs stay attachable this long.
RUN_RETENTION_SECONDS = 300.0

# Hard cap on a single run's log. Only streaming deltas are dropped past it;
# structural events (tools, prompts, run_end) are always kept.
MAX_EVENTS_PER_RUN = 20_000

TERMINAL_EVENT_TYPES = frozenset({
    "final", "error", "approval_required", "selection_required",
    "iterations_exceeded", "cancelled",
})

_current_run: contextvars.ContextVar[Optional["Run"]] = contextvars.ContextVar(
    "ganam_current_run", default=None
)


class SessionBusyError(Exception):
    """A run is already in progress for this session."""

    def __init__(self, run: "Run"):
        super().__init__(f"Session {run.session_id} already has run {run.run_id} in progress")
        self.run = run


class Run:
    def __init__(self, user: User, session_id: str, kind: str, model: Optional[Dict[str, Any]] = None):
        self.run_id = uuid.uuid4().hex[:16]
        self.user = user
        self.session_id = session_id
        self.kind = kind
        self.model = model
        self.started_at = time.time()
        self.events: List[Dict[str, Any]] = []
        self.finished = False
        self.status = "running"
        self.task: Optional[asyncio.Task] = None
        self._changed = asyncio.Event()
        self._saw_terminal = False
        # Undo entries ({id, label}) of everything this run changed.
        self.undos: List[Dict[str, str]] = []
        # True when a learned phrase answered without the model.
        self.shortcut = False

    @property
    def last_seq(self) -> int:
        return len(self.events)

    def emit(self, event: Dict[str, Any]) -> None:
        """Append an event and wake followers. Never raises."""
        if self.finished:
            return
        etype = event.get("type", "")
        if len(self.events) >= MAX_EVENTS_PER_RUN and etype.endswith("_delta"):
            return
        if etype in TERMINAL_EVENT_TYPES:
            self._saw_terminal = True
        stamped = {**event, "run_id": self.run_id, "seq": len(self.events) + 1, "ts": round(time.time(), 3)}
        self.events.append(stamped)
        self._wake()

    def _wake(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def wait_for_events(self, after_seq: int, timeout: float) -> List[Dict[str, Any]]:
        """Events with ``seq > after_seq``, waiting up to ``timeout`` for new ones.

        Returns an empty list on timeout. Once the run has finished and the
        caller has everything, returns an empty list immediately.
        """
        if after_seq < len(self.events) or self.finished:
            return self.events[after_seq:]
        changed = self._changed
        try:
            await asyncio.wait_for(changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.events[after_seq:]

    def info(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "last_seq": self.last_seq,
            "model": self.model,
            "started_at": self.started_at,
        }


_Key = Tuple[str, str]  # (user id, session id)


class RunManager:
    def __init__(self) -> None:
        self._active: Dict[_Key, Run] = {}
        self._recent: Dict[_Key, Run] = {}  # last finished run per session

    def active(self, user_id: str, session_id: str) -> Optional[Run]:
        run = self._active.get((user_id, session_id))
        return run if run and not run.finished else None

    def latest(self, user_id: str, session_id: str) -> Optional[Run]:
        """The active run, else the most recently finished one still retained."""
        return self.active(user_id, session_id) or self._recent.get((user_id, session_id))

    def is_running(self, user_id: str, session_id: str) -> bool:
        return self.active(user_id, session_id) is not None

    def owner_of(self, run_id: str) -> Optional[str]:
        """The user id of the run ``run_id`` while it is kept, else None."""
        for run in (*self._active.values(), *self._recent.values()):
            if run.run_id == run_id:
                return run.user.id
        return None

    def start(
        self,
        user: User,
        session_id: str,
        kind: str,
        body: Callable[[], Awaitable[Any]],
        *,
        model: Optional[Dict[str, Any]] = None,
        pre_events: Iterable[Dict[str, Any]] = (),
    ) -> Run:
        """Create a run for ``user`` and start ``body()`` as its task (with the
        run and the user bound in its context).

        Raises :class:`SessionBusyError` if the session already has a run.
        """
        existing = self.active(user.id, session_id)
        if existing is not None:
            raise SessionBusyError(existing)

        run = Run(user, session_id, kind, model)
        run.emit({"type": "run_start", "kind": kind, "session_id": session_id, "model": model})
        for event in pre_events:
            run.emit(event)

        token = _current_run.set(run)
        try:
            with users.acting_as(user):
                run.task = asyncio.create_task(body())
        finally:
            _current_run.reset(token)

        self._active[(user.id, session_id)] = run
        run.task.add_done_callback(lambda t, r=run: self._finish(r, t))
        return run

    def cancel(self, user_id: str, session_id: str) -> bool:
        run = self.active(user_id, session_id)
        if run and run.task and not run.task.done():
            run.task.cancel()
            return True
        return False

    def _finish(self, run: Run, task: asyncio.Task) -> None:
        if task.cancelled():
            run.emit({"type": "cancelled"})
            status = "cancelled"
        else:
            exc = task.exception()
            if exc is not None:
                if not run._saw_terminal:
                    run.emit({"type": "error", "message": f"The assistant run failed: {exc}"})
                status = "error"
            else:
                status = _session_status(run.user.id, run.session_id)

        run.status = status
        run.emit({"type": "run_end", "status": status})
        run.finished = True
        run._wake()

        key = (run.user.id, run.session_id)
        if self._active.get(key) is run:
            del self._active[key]
        self._recent[key] = run
        try:
            asyncio.get_running_loop().call_later(RUN_RETENTION_SECONDS, self._forget, run)
        except RuntimeError:
            pass

    def _forget(self, run: Run) -> None:
        key = (run.user.id, run.session_id)
        if self._recent.get(key) is run:
            del self._recent[key]


def _session_status(user_id: str, session_id: str) -> str:
    # Read directly (not through core.session, which sits above runs: it
    # imports the integrations framework, whose kinds use core.llm → usage → runs).
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE user_id = ? AND session_id = ?",
                               (user_id, session_id)).fetchone()
        return (row["status"] if row else None) or "idle"
    except Exception:
        logger.warning("Could not read session status for %s", session_id, exc_info=True)
        return "idle"


runs = RunManager()


def current_run() -> Optional[Run]:
    return _current_run.get()


def emit(event: Dict[str, Any]) -> None:
    """Publish an event to the run bound to this context, if any."""
    run = _current_run.get()
    if run is not None:
        run.emit(event)


def add_undos(entries: Iterable[Dict[str, str]]) -> None:
    """Remember undo entries on the run bound to this context, if any."""
    run = _current_run.get()
    if run is not None:
        for e in list(entries):
            if e not in run.undos:
                run.undos.append(e)


def live_undos(run: Optional["Run"]) -> List[Dict[str, str]]:
    """The run's undo entries that can still be used (not expired or used)."""
    if run is None or not run.undos:
        return []
    out = []
    for entry in run.undos:
        d = undo.describe(entry["id"], run.user.id)
        if d:
            out.append(d)
    return out


def sse_frame(payload: Dict[str, Any]) -> bytes:
    """Encode one Server-Sent Events frame. ``id:`` carries the seq when present."""
    data = json.dumps(payload, ensure_ascii=False, default=str)
    seq = payload.get("seq")
    prefix = f"id: {seq}\n" if seq is not None else ""
    return f"{prefix}data: {data}\n\n".encode("utf-8")
