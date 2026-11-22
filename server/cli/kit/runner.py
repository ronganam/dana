"""``bin/dana addons test <path>``: an addon's tests against Dana's test harness.

Each ``tests/test_*.py`` runs in its own process from ``server/``, with ``server/`` and
``server/tests/`` on the import path and ``DANA_HOME`` set to this checkout (what the
addon's ``tests/dana_harness.py`` looks for); ``tests/test_*.mjs`` run with Node. The
values of Dana's own ``.env`` are left out of their environment, so a test never reaches
a real service with a real key.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, TextIO, Tuple

from dotenv import dotenv_values

from core import env_file

SERVER = Path(__file__).resolve().parents[2]
TIMEOUT = 600


def clean_env() -> Dict[str, str]:
    """This process's environment without what Dana's ``.env`` put in it, with Dana on the import path."""
    loaded = env_file.path()
    secret = set(dotenv_values(loaded)) if loaded is not None and loaded.is_file() else set()
    env = {k: v for k, v in os.environ.items() if k not in secret}
    paths = [str(SERVER), str(SERVER / "tests")] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env.update(PYTHONPATH=os.pathsep.join(paths), DANA_HOME=str(SERVER.parent), DANA_ENV_FILE="")
    return env


def scripts(path: Path) -> List[Path]:
    """The addon's test scripts: ``tests/test_*.py`` then ``tests/test_*.mjs``."""
    folder = path / "tests"
    return sorted(folder.glob("test_*.py")) + sorted(folder.glob("test_*.mjs"))


def run_one(script: Path) -> Tuple[int, str]:
    """Run one script; (exit status, its output)."""
    cmd = ["node", str(script)] if script.suffix == ".mjs" else [sys.executable, str(script)]
    try:
        done = subprocess.run(cmd, cwd=SERVER, env=clean_env(), capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return 1, f"timed out after {TIMEOUT} s"
    except FileNotFoundError as e:
        return 1, f"cannot run it: {e}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def run(path: Path, out: TextIO, verbose: bool = False) -> int:
    """Run every test of the addon at ``path``, printing a line each (and the output of failures);
    returns the number that failed (no test at all counts as one)."""
    path = path.expanduser().resolve()
    found = scripts(path)
    if not found:
        print(f"No tests in {path / 'tests'} (test_*.py): add one (bin/dana addons new writes a starting point).",
              file=out)
        return 1
    failed = 0
    for script in found:
        rc, output = run_one(script)
        label = script.relative_to(path)
        print(f"{'PASS' if rc == 0 else 'FAIL'}  {label}", file=out)
        if rc != 0 or verbose:
            lines = output.rstrip().splitlines()
            shown = lines if verbose or len(lines) <= 40 else ["…"] + lines[-40:]
            print("\n".join(f"      {ln}" for ln in shown), file=out)
        failed += rc != 0
    print(f"{len(found) - failed} passed, {failed} failed", file=out)
    return failed
