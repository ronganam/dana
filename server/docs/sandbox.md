# The Python sandbox (`run_code`)

`run_code` (the essentials addon, so every user has it) runs a short Python 3 script and returns what it prints. It covers what no single tool covers: arithmetic and dates, parsing or reshaping text and data, and many similar tool calls in a loop ("add these twelve items", "compare every option's price per square metre").

## What the script can reach

- **The standard library of CPython 3.15**, built for WASI (`bin/fetch-python-wasi`: the `brettcannon/cpython-wasi-build` release, pinned by version and SHA-256). It has no third-party packages, no network, no subprocesses and no threads.
- **`/work`**: the user's own scratch folder (`<data dir>/sandbox/<user id>/`). It is kept between runs, and files older than a day are removed before each run. After each run the folder is trimmed to 50 MB, largest files first. The result lists what is in it.
- **`/lib`**: the interpreter's library, read-only. Nothing else of the server is visible: no other files and no environment variables (the script gets only `HOME=/work` and Python's own variables).
- **The user's tools**: `tools.NAME(arg=...)` or `call("NAME", arg=...)` runs a tool as the user who ran the script and returns its text.
  - **Allowed**: reads, and changes Dana can undo. Each change gets its Undo as usual, because the undo collection, the current user and the budget follow the calls.
  - **Refused**: a tool that asks first or acts outside Dana (`confirm`, `acts_outside`: sending, paying, deleting) raises `ToolError` inside the script, so the model calls it itself, where the user approves it. The script also cannot call `run_code` or `request_user_selection`, a tool the user does not have, or more than 200 tools in one run.
- **Text written by others**: if the script called an `untrusted_result` tool (mail, messages, the web), the whole output comes back framed by `core.text.untrusted`, and the rest of the turn treats it as such (`docs/agent-actions.md`, "Text written by others").

## Limits

| Limit | Value | Enforced by |
|---|---|---|
| Time | 30 s by default, up to 120 s (`timeout`) | wasmtime epoch interruption (a ticker every 0.25 s); a script waiting on stdin gets EOF |
| Memory | 256 MB | `Store.set_limits` (`MemoryError` in the script) |
| Output | 10,000 characters each for stdout and the traceback (stdout keeps its start, the traceback its end) | `core/sandbox.py` |
| Runs at once | 2 per server; the rest wait | a semaphore |

## How it works (`core/sandbox.py`)

1. One wasmtime engine compiles `python.wasm` once per process (about 1 s) and is shared.
2. Each run gets its own store with the limits above, and runs in a worker thread.
3. A prelude runs before the script. It defines `tools`, `call` and `ToolError`, and prints the script's own traceback without the prelude's frame.
4. A tool call is a stdout line starting with `\x00DANA ` followed by JSON. The host runs the tool on the event loop, in the caller's context, and writes the JSON answer to the script's stdin (a pipe).

The image fetches the runtime at build time into `/opt/python-wasi` (`PYTHON_WASI_DIR`). In a checkout, run `bin/fetch-python-wasi` once; it writes to `.cache/python-wasi`, which is gitignored. Without the runtime, `run_code` answers with an error saying so, and `tests/test_run_code.py` skips. CI fetches the runtime before the tests.

This sandbox is not the ops addon's `run_python`. That one runs CPython on the server itself, for admins only, and asks first.
