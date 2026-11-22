"""``bin/dana addons dev <path>``: the addon running in the local test environment
(docs/local-testing.md), reloaded when a file of it changes.

1. ``bin/dana addons check`` (errors are shown; the environment starts anyway, so the log
   says what breaks).
2. ``bin/local-env.sh up`` with ``DANA_LOCAL_SOURCE=1`` (this checkout's code) and
   ``DANA_LOCAL_ADDONS=<name>=<path>``: the addon folder mounted read-only as a site addon
   (``/app/addons-site/<name>``). The first run builds the image (a few minutes); later
   ones reuse it.
3. The addon is connected for the test admin (``bin/dana addons connect Robin <name>``),
   then its log lines, its tools and the Now cards and day items it makes are printed
   (:mod:`cli.kit.devshow`, inside the app container).
4. Without ``--once``: the folder is watched (polled each second); on a change the check
   runs again, the app restarts (``bin/local-env.sh restart``, a few seconds) and step 3
   repeats. Ctrl-C stops watching; the environment keeps running (``bin/local-env.sh down``
   removes it).
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, TextIO

from cli.kit import check as check_kit

SERVER = Path(__file__).resolve().parents[2]
LOCAL_ENV = SERVER / "bin" / "local-env.sh"
POLL_SECONDS = 1.0
LOG_LINES = 20


class DevError(RuntimeError):
    """The local environment could not be started or reached."""


def snapshot(path: Path) -> Dict[str, float]:
    """Modification times of the addon's files (caches and git left out)."""
    return {str(p): p.stat().st_mtime for p in path.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and ".git" not in p.parts}


def local_env(args: list, name: str, path: Path, *, capture: bool = False) -> subprocess.CompletedProcess:
    """Run ``bin/local-env.sh <args>`` with this checkout's code and the addon mounted."""
    env = {**os.environ, "DANA_LOCAL_SOURCE": "1", "DANA_LOCAL_ADDONS": f"{name}={path}"}
    return subprocess.run([str(LOCAL_ENV), *args], cwd=SERVER, env=env, text=True,
                          capture_output=capture, timeout=1800)


def show(name: str, path: Path, out: TextIO) -> None:
    """Print the addon's log lines, tools, Now cards and day items in the running environment."""
    logs = local_env(["logs", "--tail", "400", "--no-log-prefix"], name, path, capture=True)
    mine = [ln for ln in logs.stdout.splitlines() if name in ln][-LOG_LINES:]
    print(f"--- log ({len(mine)} line(s) naming {name}) ---", file=out)
    print("\n".join(mine) or "(none)", file=out)
    done = local_env(["exec", "python", "-m", "cli.kit.devshow", name], name, path, capture=True)
    try:
        state = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        print(f"--- could not read its state: {(done.stderr or done.stdout).strip()[-500:]}", file=out)
        return
    if not state["loaded"]:
        print(f"--- NOT LOADED: {state['error']}", file=out)
        return
    print(f"--- tools: {', '.join(state['tools']) or '(none)'}", file=out)
    print(f"--- Now cards: {json.dumps(state['cards'], ensure_ascii=False) if state['cards'] else '(none)'}", file=out)
    print(f"--- day items: {json.dumps(state['day_items'], ensure_ascii=False) if state['day_items'] else '(none)'}",
          file=out)


def dev(path: Path, out: TextIO, *, once: bool = False) -> int:
    """Run the addon in the local test environment (see the module docstring); returns the exit status."""
    path = path.expanduser().resolve()
    report = check_kit.check(path)
    out.write(report.text())
    if not report.name:
        return 1
    name = report.name
    print("Starting the local test environment with it (bin/local-env.sh up)…", file=out, flush=True)
    if local_env(["up"], name, path).returncode != 0:
        print("The local test environment did not start (docs/local-testing.md).", file=out)
        return 1
    connected = local_env(["exec", "bin/dana", "addons", "connect", "Robin", name], name, path, capture=True)
    if connected.returncode != 0 and "already" not in (connected.stderr + connected.stdout):
        print(f"Could not connect it for Robin: {(connected.stderr or connected.stdout).strip()}", file=out)
    show(name, path, out)
    port = os.environ.get("DANA_LOCAL_PORT", "18080")
    print(f"Dana (local): http://127.0.0.1:{port} (the admin token: bin/local-env.sh prints it with up).", file=out)
    if once:
        return 0
    print(f"Watching {path} (Ctrl-C stops)…", file=out, flush=True)
    seen = snapshot(path)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            now = snapshot(path)
            if now == seen:
                continue
            changed = sorted(set(now.items()) ^ set(seen.items()))
            seen = now
            print(f"\nChanged: {', '.join(sorted({Path(p).name for p, _ in changed}))}; restarting…", file=out, flush=True)
            report = check_kit.check(path)
            if not report.ok:
                out.write(report.text())
            local_env(["restart"], name, path)
            show(name, path, out)
    except KeyboardInterrupt:
        print("\nStopped watching; the environment keeps running (bin/local-env.sh down removes it).", file=out)
    return 0
