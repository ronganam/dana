"""Import smoke test — mirrors the container's boot path.

`main.py` imports modules lazily inside `main()`, so a bad name in a type
annotation (e.g. `List` missing from a `typing` import) does not surface until
the pod starts, where it kills the process with a NameError at import time.

This walks the same import sequence in a throwaway subprocess so any module that
cannot even be imported is caught here instead of in production.

Each check still starts from an interpreter where none of the app's modules is
loaded: a few fork servers (subprocesses) import the heavy third-party
libraries once (LiteLLM, aiogram, Telethon… several seconds each) and fork a
child per check. A preloaded library binds no name in an app module, so a
missing import fails exactly as before. Without ``os.fork`` every check is its
own subprocess (slower).
"""

import json
import os
import pathlib
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import testenv  # clean env (no .env, no addon values) + a temp dir removed at exit

testenv.isolate("imports_test")

def _modules() -> list[str]:
    """Every module under core/, addons/ and cli/ (packages included), found on disk."""
    root = testenv.SERVER
    names = []
    for top in ("core", "addons", "cli"):
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).parts
            if "tests" in rel or "template" in rel:  # test scripts (addons/<name>/tests/), the scaffold's template
                continue
            if top == "addons" and len(rel) > 2 and rel[2] == "worker":  # an addon's sidecar (not Python modules)
                continue
            parts = path.relative_to(root).with_suffix("").parts
            names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


CHECKS = _modules()

failures = []

# Force every annotation to be resolved after import.
#
# Python 3.14 (PEP 649) evaluates annotations only when something asks for them,
# so a name missing from a `typing` import imports cleanly and then fails the
# first time a tool schema, pydantic or get_type_hints() reads that annotation —
# in the pod, at an unlucky moment. Calling get_type_hints() on everything here
# makes that failure happen in the test instead.
RESOLVE_FN = """
import importlib, inspect, typing
def resolve(module):
  mod = importlib.import_module(module)
  bad = []
  for name, obj in list(vars(mod).items()):
      targets = []
      if inspect.isfunction(obj) and obj.__module__ == mod.__name__:
          targets.append(obj)
      elif inspect.isclass(obj) and obj.__module__ == mod.__name__:
          targets.append(obj)
          targets.extend(m for m in vars(obj).values()
                         if inspect.isfunction(m))
      for t in targets:
          try:
              typing.get_type_hints(t)
          except Exception as e:
              bad.append('%s:%s -> %s: %s' % (mod.__name__,
                                             getattr(t, '__qualname__', t),
                                             type(e).__name__, e))
  return bad
"""

RESOLVE = RESOLVE_FN + """
import sys
bad = resolve(sys.argv[1])
if bad:
    print('ANNOTATION_ERRORS')
    print(chr(10).join(bad))
    sys.exit(2)
print('OK')
"""

# The fork server: preloads the third-party libraries, then for each check (a JSON
# line on stdin: [kind, name, db path]) forks a child that runs it and prints one
# JSON line [name, error or null]; the server waits for each child in turn.
PRELOAD = ("litellm", "aiogram", "telethon", "aiohttp", "httpx", "pydantic", "googleapiclient.discovery",
           "google.oauth2.credentials", "cryptography.fernet")
FORK_SERVER = RESOLVE_FN + """
import importlib, json, os, sys
for lib in %r:
    try:
        importlib.import_module(lib)
    except Exception:
        pass  # not installed: the check that needs it fails as it would have
for line in sys.stdin:
    kind, name, db = json.loads(line)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        os.environ['DATABASE_PATH'] = db
        try:
            if kind == 'module':
                bad = resolve(name)
                error = bad[-1] if bad else None
            else:
                for m in name.split(' '):
                    importlib.import_module(m)
                error = None
        except BaseException as e:
            error = '%%s: %%s' %% (type(e).__name__, e)
        os.write(w, json.dumps(error).encode())
        os._exit(0)
    os.close(w)
    chunks = []
    while True:
        chunk = os.read(r, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(r)
    _, status = os.waitpid(pid, 0)
    raw = b''.join(chunks)
    error = json.loads(raw) if raw else 'child died (status %%d)' %% status
    print(json.dumps([name, error]), flush=True)
""" % (PRELOAD,)

def _run(argv: list[str], db_name: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_PATH"] = testenv.scratch_path(db_name)
    return subprocess.run([sys.executable, "-B", *argv], capture_output=True, text=True, env=env)


def _check_module(module: str) -> tuple[str, str | None]:
    proc = _run(["-c", RESOLVE, module], f"smoke_{module}.sqlite3")
    if proc.returncode == 0 and "OK" in proc.stdout:
        return module, None
    detail = proc.stdout.strip() or (proc.stderr or "").strip()
    lines = [ln for ln in detail.splitlines() if ln not in ("ANNOTATION_ERRORS",) and ln.strip()]
    return module, lines[-1] if lines else "no output"


# Import order must not matter: each pair is imported in that order in a fresh
# interpreter (the sharing helper used to raise a circular import when it was
# imported before the addons package; it now lives in the framework).
ORDER_PAIRS = [
    ("core.addons", "core.users"),
    ("core.users", "core.addons"),
    ("core.addons.registry", "core.server"),
    ("core.addons.sharing", "addons"),
    ("addons", "core.addons.sharing"),
    ("core.addons.store", "core.addons.sharing"),
    ("core.addons.addresses", "addons"),
    ("core.sharing_api", "core.server"),
    ("core.debug_env", "core.server"),
    ("core.outbound", "addons.knowledge.media"),
    ("addons.whatsapp.accounts", "addons"),
    ("addons.shopping.lists", "addons.shopping.tools"),
    ("addons.shopping.journal", "addons.shopping.lists"),
    ("addons.shopping.api", "core.server"),
    ("addons.tasks.api", "core.server"),
    ("addons.phone.calls.api", "addons.phone.api"),
    ("addons.phone.notifications.api", "core.server"),
    ("core.assistant_api", "core.sessions_api"),
]
def _check_order(pair: tuple[str, str]) -> tuple[str, str | None]:
    first, second = pair
    proc = _run(["-c", f"import {first}; import {second}; print('OK')"], f"smoke_order_{first}_{second}.sqlite3")
    name = f"order: {first} then {second}"
    if proc.returncode == 0 and "OK" in proc.stdout:
        return name, None
    lines = [ln for ln in (proc.stderr or proc.stdout).splitlines() if ln.strip()]
    return name, lines[-1] if lines else "no output"


def _workers() -> int:
    """Half the CPUs (bin/test runs other scripts beside this one), fewer when memory is short."""
    try:
        available_mb = int(re.search(r"MemAvailable:\s+(\d+)", pathlib.Path("/proc/meminfo").read_text()).group(1)) // 1024
    except (OSError, AttributeError):
        available_mb = 8192
    return max(2, min((os.cpu_count() or 4) // 2, available_mb // 800))


def _fork_server(jobs: list[tuple[str, str, str]]) -> dict[str, str | None]:
    """Run ``jobs`` ([kind, name, db path]) in one fork server; name → error (None: passed)."""
    proc = subprocess.run([sys.executable, "-B", "-c", FORK_SERVER], capture_output=True, text=True,
                          input="".join(json.dumps(j) + "\n" for j in jobs), env=dict(os.environ))
    out: dict[str, str | None] = {}
    for line in proc.stdout.splitlines():
        try:
            name, error = json.loads(line)
        except ValueError:
            continue
        out[name] = error
    for _, name, _ in jobs:
        if name not in out:
            out[name] = "fork server: " + ((proc.stderr or "").strip().splitlines() or ["no output"])[-1]
    return out


def _forked() -> list[tuple[str, str | None]]:
    jobs = [("module", m, testenv.scratch_path(f"smoke_{m}.sqlite3")) for m in CHECKS]
    jobs += [("order", f"{a} {b}", testenv.scratch_path(f"smoke_order_{a}_{b}.sqlite3")) for a, b in ORDER_PAIRS]
    n = _workers()
    with ThreadPoolExecutor(max_workers=n) as pool:
        found: dict[str, str | None] = {}
        for part in pool.map(_fork_server, [jobs[i::n] for i in range(n)]):
            found.update(part)
    return ([(m, found[m]) for m in CHECKS]
            + [(f"order: {a} then {b}", found[f"{a} {b}"]) for a, b in ORDER_PAIRS])


if hasattr(os, "fork"):
    results = _forked()
else:  # each check its own interpreter, side by side
    with ThreadPoolExecutor(max_workers=_workers()) as pool:
        results = list(pool.map(_check_module, CHECKS)) + list(pool.map(_check_order, ORDER_PAIRS))
for name, error in results:
    if error is None:
        print(f"[PASS] {name}")
    else:
        failures.append((name, error))
        print(f"[FAIL] {name}: {error}")

print()
if failures:
    print(f"{len(failures)}/{len(CHECKS) + len(ORDER_PAIRS)} checks failed")
    for module, err in failures:
        print(f"  - {module}: {err}")
    sys.exit(1)

print(f"All {len(CHECKS)} modules import cleanly; {len(ORDER_PAIRS)} import orders work")
