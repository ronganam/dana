"""Dana's .env (core/env_file.py): only its own file is loaded (DANA_ENV_FILE or server/.env, set variables win), LITELLM_MODE=PRODUCTION is set so LiteLLM never loads a .env of its own, and main.py / bin/dana do that before importing anything else.

Run: bin/test test_env_file

The LiteLLM check runs in a fresh interpreter with ``dotenv.load_dotenv``
replaced by a spy (so nothing is ever read): after ``env_file.load()``,
importing LiteLLM must not call it; without it (the control), it does.
"""

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("env_file_test")

ROOT = testenv.SERVER
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


ENV = Path(testenv.scratch_path("dana.env"))
ENV.write_text("DANA_TEST_FROM_FILE=from-file\nDANA_TEST_ALREADY=from-file\nLITELLM_MODE=DEV\n")


def run(script: str, **env_changes: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("LITELLM_MODE", None)
    for k, v in env_changes.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=180)


out = run("""
    import os
    from core import env_file
    env_file.load()
    print(os.environ.get("DANA_TEST_FROM_FILE"), os.environ.get("DANA_TEST_ALREADY"), os.environ.get("LITELLM_MODE"))
""", DANA_ENV_FILE=str(ENV), DANA_TEST_ALREADY="from-env")
check("load: the named file's values are loaded, a set variable wins, LITELLM_MODE is PRODUCTION even if the file "
      "says otherwise", out.stdout.split() == ["from-file", "from-env", "PRODUCTION"], out.stdout + out.stderr[-800:])

out = run("""
    import os
    from core import env_file
    env_file.load()
    print(os.environ.get("DANA_TEST_FROM_FILE"), os.environ.get("LITELLM_MODE"), env_file.path())
""", DANA_ENV_FILE="")
check("load: DANA_ENV_FILE='' loads nothing (still PRODUCTION)", out.stdout.split() == ["None", "PRODUCTION", "None"],
      out.stdout + out.stderr[-800:])

out = run("""
    from core import env_file
    print(env_file.path() == env_file.ENV_FILE, env_file.ENV_FILE.parent.name == "server" or env_file.ENV_FILE.parent.name)
""", DANA_ENV_FILE=None)
check("load: by default it is .env in the server folder", out.stdout.split()[:1] == ["True"], out.stdout + out.stderr[-800:])

SPY = """
    import dotenv
    CALLS = []
    def spy(*args, **kwargs):
        CALLS.append(kwargs)
        return False
    dotenv.load_dotenv = spy
"""
out = run(SPY + """
    from core import env_file
    env_file.load()          # DANA_ENV_FILE="": reads no file (the spy counts only LiteLLM)
    import litellm
    print("CALLS", len(CALLS))
""", DANA_ENV_FILE="")
check("LiteLLM: after env_file.load() importing it loads no .env", "CALLS 0" in out.stdout, out.stdout + out.stderr[-800:])
out = run(SPY + """
    import litellm
    print("CALLS", len(CALLS))
""", DANA_ENV_FILE="")
check("LiteLLM: the control (no env_file.load(), LITELLM_MODE unset) would load one", "CALLS 1" in out.stdout,
      out.stdout + out.stderr[-800:])


def leading(path: Path) -> list:
    """The first statements of a script, as source, after its docstring and plain stdlib setup."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [ast.unparse(n) for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]


def env_first(path: Path) -> bool:
    """``env_file.load()`` runs before any import of Dana's modules or of LiteLLM."""
    statements = leading(path)
    try:
        at = statements.index("env_file.load()")
    except ValueError:
        return False
    before = statements[:at]
    app = [s for s in before if s.startswith(("from core", "import core", "from addons", "import addons", "from cli",
                                              "import litellm", "from litellm"))]
    return app == ["from core import env_file"]


check("order: main.py loads the env file before importing anything of Dana or LiteLLM", env_first(ROOT / "main.py"),
      leading(ROOT / "main.py")[:6])
check("order: bin/dana does too", env_first(ROOT / "bin" / "dana"), leading(ROOT / "bin" / "dana")[:6])

passed = sum(results)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
