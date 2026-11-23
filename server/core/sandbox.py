"""Python in a sandbox (docs/sandbox.md): CPython built for WASI, run in wasmtime.

The script sees only the standard library, its own scratch folder as ``/work``
and nothing else of the server: no network, no environment, no other files.
Time (``timeout``) and memory (``MEMORY_BYTES``) are capped by wasmtime itself
(epoch interruption, store limits), so a runaway script cannot hold the server.

The script reaches Dana's tools through one channel: a line it writes to stdout
starting with :data:`CALL_MARK` asks for a tool call, and the host's answer comes
back on stdin. The prelude wraps that as ``tools.NAME(**arguments)`` and
``call(NAME, **arguments)``. Whether a call may run is the caller's ``call_tool``
decision (``run_code`` in the essentials addon); this module only carries it.

Runs happen in a worker thread (:func:`run` is async); ``call_tool`` is a
coroutine run on the event loop, so tools behave as in any agent turn (same
user, undo collection and budget, through the context variables).
"""

import asyncio
import atexit
import concurrent.futures
import contextvars
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from core import paths

logger = logging.getLogger(__name__)

RUNTIME_DIR = Path(os.getenv("PYTHON_WASI_DIR") or Path(__file__).resolve().parent.parent / ".cache" / "python-wasi")
SCRATCH_DIR = paths.DATA_DIR / "sandbox"
SCRATCH_TTL_SECONDS = 24 * 3600
SCRATCH_MAX_BYTES = 50 * 1024 * 1024
MEMORY_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_CHARS = 10_000
MAX_TOOL_CALLS = 200
MAX_RUNNING = 2
TICK_SECONDS = 0.25
CALL_MARK = "\x00DANA "

ToolCaller = Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]

# Runs inside the sandbox before the script. The script's own frames are "<code>";
# a failure prints the traceback without this prelude's frame.
PRELUDE = r'''
import sys, json

class ToolError(Exception):
    """A tool call Dana refused or that failed before the tool ran."""

def _call(tool, arguments):
    sys.stdout.flush()
    sys.__stdout__.write("\x00DANA " + json.dumps({"tool": tool, "arguments": arguments}, default=str) + "\n")
    sys.__stdout__.flush()
    line = sys.stdin.readline()
    if not line:
        raise ToolError("Dana stopped answering tool calls (the time is up).")
    reply = json.loads(line)
    if "error" in reply:
        raise ToolError(reply["error"])
    return reply["result"]

class _Tools:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **arguments: _call(name, arguments)

def call(tool, **arguments):
    return _call(tool, arguments)

def _no_input(prompt=""):
    raise EOFError("run_code has no input: put the data in the code or in /work")

import builtins
builtins.input = _no_input

_ns = {"__name__": "__main__", "tools": _Tools(), "call": call, "ToolError": ToolError}
try:
    exec(compile(sys.argv[1], "<code>", "exec"), _ns)
except SystemExit:
    raise
except BaseException as e:
    import traceback
    traceback.print_exception(type(e), e, e.__traceback__.tb_next)
    sys.exit(1)
'''


@dataclass
class Result:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    tool_calls: int = 0
    files: List[Tuple[str, int]] = field(default_factory=list)


def available() -> bool:
    """The runtime is installed (``bin/fetch-python-wasi``) and wasmtime imports."""
    if not (RUNTIME_DIR / "python.wasm").is_file():
        return False
    try:
        import wasmtime  # noqa: F401
    except ImportError:
        return False
    return True


_engine_lock = threading.Lock()
_engine: Any = None
_module: Any = None
_slots: Optional[asyncio.Semaphore] = None


def _release() -> None:
    """Free the compiled module while wasmtime can still do it (not during interpreter teardown)."""
    global _engine, _module
    _module = _engine = None


def _compiled() -> Tuple[Any, Any]:
    """The shared engine and compiled interpreter (compiled once, ~1 s), with the epoch ticker."""
    global _engine, _module
    with _engine_lock:
        if _module is None:
            import wasmtime
            config = wasmtime.Config()
            config.epoch_interruption = True
            engine = wasmtime.Engine(config)
            _module = wasmtime.Module.from_file(engine, str(RUNTIME_DIR / "python.wasm"))
            _engine = engine

            def tick() -> None:
                while True:
                    time.sleep(TICK_SECONDS)
                    engine.increment_epoch()

            threading.Thread(target=tick, name="sandbox-epoch", daemon=True).start()
            atexit.register(_release)
        return _engine, _module


def scratch_dir(user_id: str) -> Path:
    """``user_id``'s ``/work``: files older than a day are removed first."""
    if not user_id or not str(user_id).replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"not a user id: {user_id!r}")
    folder = SCRATCH_DIR / str(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - SCRATCH_TTL_SECONDS
    for path in sorted(folder.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
    return folder


def _files(folder: Path) -> List[Tuple[str, int]]:
    out = [(str(p.relative_to(folder)), p.stat().st_size) for p in folder.rglob("*") if p.is_file()]
    return sorted(out)


def _trim_scratch(folder: Path) -> None:
    """Keep the folder under :data:`SCRATCH_MAX_BYTES`, dropping the largest files first."""
    files = sorted(((p.stat().st_size, p) for p in folder.rglob("*") if p.is_file()), reverse=True)
    total = sum(size for size, _ in files)
    for size, path in files:
        if total <= SCRATCH_MAX_BYTES:
            break
        path.unlink(missing_ok=True)
        total -= size


def _cut(text: str, keep_end: bool = False) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    note = f"[cut: {len(text):,} characters, showing the {'last' if keep_end else 'first'} {MAX_OUTPUT_CHARS:,}]"
    return f"{note}\n{text[-MAX_OUTPUT_CHARS:]}" if keep_end else f"{text[:MAX_OUTPUT_CHARS]}\n{note}"


def _run_sync(code: str, workdir: Path, timeout: float, call_tool: Callable[[str, Dict[str, Any], float], Dict[str, Any]]) -> Result:
    import wasmtime
    engine, module = _compiled()
    result = Result()
    deadline = time.monotonic() + timeout
    out_lines: List[str] = []
    err_parts: List[bytes] = []
    pending = bytearray()
    read_fd, write_fd = os.pipe()
    closed = threading.Event()

    def close_input() -> None:
        if not closed.is_set():
            closed.set()
            try:
                os.close(write_fd)
            except OSError:
                pass

    def reply(message: Dict[str, Any]) -> None:
        # From a thread: the script reads its answer only after this callback returns,
        # so a large answer must not block on a full pipe.
        data = (json.dumps(message) + "\n").encode()

        def send() -> None:
            view = memoryview(data)
            while view and not closed.is_set():
                try:
                    view = view[os.write(write_fd, view):]
                except OSError:
                    return

        threading.Thread(target=send, daemon=True).start()

    def handle(line: str) -> None:
        if not line.startswith(CALL_MARK):
            out_lines.append(line)
            return
        result.tool_calls += 1
        try:
            request = json.loads(line[len(CALL_MARK):])
            name, arguments = str(request["tool"]), request.get("arguments") or {}
        except (ValueError, KeyError, TypeError):
            reply({"error": "malformed tool call"})
            return
        if result.tool_calls > MAX_TOOL_CALLS:
            reply({"error": f"too many tool calls in one run (at most {MAX_TOOL_CALLS})"})
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            reply({"error": "the time is up"})
            return
        reply(call_tool(name, arguments if isinstance(arguments, dict) else {}, remaining))

    def on_stdout(chunk: bytes) -> None:
        pending.extend(chunk)
        while True:
            at = pending.find(b"\n")
            if at < 0:
                break
            line = pending[:at + 1].decode("utf-8", "replace")
            del pending[:at + 1]
            handle(line)

    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-I", "-S", "-c", PRELUDE, code]
    wasi.env = [("PYTHONHOME", "/"), ("PYTHONPATH", "/lib/python3.15"), ("HOME", "/work"), ("PYTHONIOENCODING", "utf-8")]
    wasi.preopen_dir(str(RUNTIME_DIR / "lib"), "/lib", fs_mutable=False)
    wasi.preopen_dir(str(workdir), "/work")
    wasi.stdin_file = f"/proc/self/fd/{read_fd}"
    wasi.stdout_custom = on_stdout
    wasi.stderr_custom = err_parts.append
    store = wasmtime.Store(engine)
    store.set_wasi(wasi)
    store.set_limits(memory_size=MEMORY_BYTES)
    store.set_epoch_deadline(max(1, int(timeout / TICK_SECONDS) + 1))
    timer = threading.Timer(timeout, close_input)  # a script blocked reading stdin gets EOF
    timer.start()
    try:
        instance = wasmtime.Linker(engine)
        instance.define_wasi()
        instance.instantiate(store, module).exports(store)["_start"](store)
    except wasmtime.ExitTrap as e:
        result.exit_code = e.code
    except wasmtime.Trap as e:
        result.exit_code = 1
        if time.monotonic() >= deadline - TICK_SECONDS:
            result.timed_out = True
        else:
            err_parts.append(f"\n{str(e).splitlines()[0]}".encode())
    finally:
        timer.cancel()
        close_input()
        os.close(read_fd)
    if pending:
        handle(pending.decode("utf-8", "replace"))
    result.stdout = _cut("".join(out_lines))
    result.stderr = _cut(b"".join(err_parts).decode("utf-8", "replace"), keep_end=True)
    _trim_scratch(workdir)
    result.files = _files(workdir)
    return result


async def run(code: str, *, user_id: str, call_tool: ToolCaller, timeout: float = 30.0) -> Result:
    """Run ``code`` in the sandbox with ``user_id``'s scratch folder as ``/work``.

    ``call_tool(name, arguments)`` answers each tool call from the script, on the
    event loop: ``{"result": text}`` or ``{"error": why}`` (raised in the script as
    ``ToolError``). At most :data:`MAX_RUNNING` runs at once; the rest wait.

    Raises:
        RuntimeError: the runtime is not installed (:func:`available`).
    """
    global _slots
    if not available():
        raise RuntimeError("the Python sandbox is not installed on this server (bin/fetch-python-wasi; docs/sandbox.md)")
    loop = asyncio.get_running_loop()
    if _slots is None:
        _slots = asyncio.Semaphore(MAX_RUNNING)

    # The caller's context (user, undo collection, budget): wasmtime's callback does not carry it.
    context = contextvars.copy_context()

    def call_from_thread(name: str, arguments: Dict[str, Any], remaining: float) -> Dict[str, Any]:
        answer: "concurrent.futures.Future[Dict[str, Any]]" = concurrent.futures.Future()
        tasks: List["asyncio.Task[Dict[str, Any]]"] = []

        def done(task: "asyncio.Task[Dict[str, Any]]") -> None:
            if task.cancelled():
                answer.cancel()
            elif task.exception() is not None:
                answer.set_exception(task.exception())
            else:
                answer.set_result(task.result())

        def start() -> None:
            task = loop.create_task(call_tool(name, arguments), context=context)
            task.add_done_callback(done)
            tasks.append(task)

        loop.call_soon_threadsafe(start)
        try:
            return answer.result(timeout=remaining)
        except concurrent.futures.TimeoutError:
            loop.call_soon_threadsafe(lambda: [t.cancel() for t in tasks])
            return {"error": f"{name} did not answer before the time was up"}
        except BaseException as e:  # call_tool should answer every call itself
            logger.warning(f"sandbox tool call {name} failed: {e!r}", exc_info=True)
            return {"error": f"{name} failed: {e}"}

    async with _slots:
        workdir = scratch_dir(user_id)
        return await asyncio.to_thread(_run_sync, code, workdir, timeout, call_from_thread)
