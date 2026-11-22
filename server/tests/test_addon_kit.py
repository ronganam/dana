"""The addon kit: bin/dana addons new → check → test in a temp folder (and alone with DANA_HOME), check catching each declared-vs-used mismatch with a fix, the generated reference not drifting, the showcase example's tests, publish --dry-run.

Run: bin/test test_addon_kit

The command line runs in this process (``cli.main.main``); what it starts on its own (the
check's probe, the addons' tests) are subprocesses, run side by side. Addons are written to
a temporary folder; nothing is pushed anywhere (publish only with ``--dry-run``, with a
made-up GitHub login). The showcase example lives at the repository root, outside the
image's ``server/``: its checks are skipped where it is missing (the container's test stage).
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("addon_kit_test")
os.environ.update(GIT_AUTHOR_NAME="Robin Example", GIT_AUTHOR_EMAIL="robin@example.com",  # made up: the scaffold's commit
                  GIT_COMMITTER_NAME="Robin Example", GIT_COMMITTER_EMAIL="robin@example.com")

from cli import main as cli  # noqa: E402
from cli.kit import check as check_kit  # noqa: E402
from cli.kit import publish, reference  # noqa: E402

SERVER = testenv.SERVER
SCRATCH = Path(testenv.scratch_dir())
SHOWCASE = SERVER.parent / "examples" / "addons" / "showcase"
ENV = dict(os.environ)
failures = 0


def check(name: str, cond: object, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {str(detail)[-2500:]}" if detail and not cond else ""))
    failures += 0 if cond else 1


def dana(*args: str) -> Tuple[int, str, str]:
    """``bin/dana <args>`` in this process: (exit status, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(list(args), stdout=out, stderr=err, stdin=io.StringIO(""))
    return rc, out.getvalue(), err.getvalue()


def found(report: Dict[str, Any], level: str, *words: str) -> Dict[str, Any]:
    """The first problem of ``level`` whose message holds every word (or {})."""
    return next((p for p in report["problems"] if p["level"] == level and all(w in p["message"] for w in words)), {})


def alone(test: Path, home: Path) -> subprocess.CompletedProcess:
    """An addon's test run by itself, Dana found only through ``DANA_HOME``."""
    env = {k: v for k, v in ENV.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(test)], cwd=str(SCRATCH), capture_output=True, text=True, timeout=300,
                          env={**env, "DANA_HOME": str(home)})


# --- Writing the addons (fast, in this process) -------------------------------------------------------
rc, out, err = dana("addons", "new", "tides", "--dir", str(SCRATCH))
TIDES = SCRATCH / "tides"
made = sorted(str(p.relative_to(TIDES)) for p in TIDES.rglob("*") if p.is_file() and ".git" not in p.parts)
check("new: the addon with its kit (AGENTS.md, REFERENCE.md, harness, fakes, a Now card)", rc == 0 and made == [
    ".gitignore", "AGENTS.md", "README.md", "REFERENCE.md", "addon.py", "hooks.py", "tests/dana_harness.py",
    "tests/fakes.py", "tests/test_tides.py", "tools.py"], out + err + str(made))
COMMIT = publish.git(["rev-parse", "HEAD"], TIDES)
log = publish.git(["log", "--format=%s"], TIDES)
check("new: its own git repository, first commit made", log == "Start the tides addon (bin/dana addons new)", log)
check("new: REFERENCE.md is the generated reference", (TIDES / "REFERENCE.md").read_text() == reference.render())
agents = (TIDES / "AGENTS.md").read_text()
check("new: AGENTS.md has the loop and the rules", all(w in agents for w in (
    "bin/dana addons check", "bin/dana addons test", "bin/dana addons dev", "bin/dana addons publish",
    "Declare what it needs and what it touches", "Secrets only in credentials", "Made-up test data", "REFERENCE.md")))
manifest = (TIDES / "addon.py").read_text()
check("new: the manifest declares needs and touches", 'Need("env", "TIDES_API_URL"' in manifest
      and 'Touches(hosts=("api.example.com",))' in manifest and "{{" not in manifest, manifest)

CLONE = SCRATCH / "elsewhere" / "dana-addon-tides"  # a clone whose folder is not the addon's name
shutil.copytree(TIDES, CLONE)
DIRTY = SCRATCH / "dirty" / "tides"  # a repository with a change not committed
shutil.copytree(TIDES, DIRTY)
(DIRTY / "tools.py").write_text((DIRTY / "tools.py").read_text() + "\n# not committed\n")

BAD = SCRATCH / "bad" / "tides"  # does more than it declares
shutil.copytree(TIDES, BAD, ignore=shutil.ignore_patterns(".git"))
with (BAD / "tools.py").open("a", encoding="utf-8") as f:
    f.write("""

from addons.tasks import service as tasks_service

WEATHER = f"https://api.weather.example/v1/{BASE_URL}"


@addon.tool(acts_outside=True)
def tides_ring(conn: Connection, room: str, loud: bool = False) -> str:
    \"\"\"Ring the bell in a room.

    Args:
        room: Where.
    \"\"\"
    return os.environ.get("TIDES_BELL_TOKEN", "") + os.environ["TIDES_MODE"] + room
""")
(BAD / "addon.py").write_text((BAD / "addon.py").read_text().replace(
    'Touches(hosts=("api.example.com",))', 'Touches(hosts=("api.example.com", "unused.example.org"))'))
(BAD / "migrations").mkdir()
(BAD / "migrations" / "m0001_baseline.py").write_text('VERSION = 2\nNAME = "baseline"\n\ndef up(conn):\n    pass\n')
(BAD / "migrations" / "m0003_more.py").write_text('"""More."""\nVERSION = 3\nNAME = "more"\n\ndef up(conn):\n    pass\n')
(BAD / "migrations" / "add_notes.py").write_text('"""Notes."""\n')

VAGUE = SCRATCH / "vague" / "tides"  # touches= not written with plain values
shutil.copytree(TIDES, VAGUE, ignore=shutil.ignore_patterns(".git"))
(VAGUE / "addon.py").write_text((VAGUE / "addon.py").read_text().replace(
    'touches=Touches(hosts=("api.example.com",))', 'touches=TOUCHES'))
BROKEN = SCRATCH / "broken" / "tides"  # fails to import
shutil.copytree(TIDES, BROKEN, ignore=shutil.ignore_patterns(".git"))
(BROKEN / "hooks.py").write_text((BROKEN / "hooks.py").read_text() + "\nimport not_a_module_anywhere\n")

# --- Running them, side by side (each starts its own Python processes) --------------------------------
jobs = {
    "check": lambda: dana("addons", "check", str(TIDES), "--json"),
    "test": lambda: dana("addons", "test", str(TIDES)),
    "alone": lambda: alone(CLONE / "tests" / "test_tides.py", SERVER.parent),
    "nowhere": lambda: alone(CLONE / "tests" / "test_tides.py", SCRATCH),
    "bad": lambda: check_kit.check(BAD),
    "vague": lambda: check_kit.check(VAGUE),
    "broken": lambda: check_kit.check(BROKEN),
    "publish": lambda: dana("addons", "publish", str(TIDES), "--owner", "robin-example", "--dry-run"),
    "publish_own": lambda: dana("addons", "publish", str(TIDES), "--owner", "robin-example", "--catalog-repo",
                                "robin-example/dana-catalog", "--repo", "robin-example/tides", "--dry-run"),
    "dirty": lambda: dana("addons", "publish", str(DIRTY), "--owner", "robin-example", "--dry-run"),
    # The server's image has no tests/: bin/dana there must import without testenv.
    "no_tests": lambda: subprocess.run([sys.executable, "-c", "import cli.main"], cwd=str(SERVER), capture_output=True,
                                       text=True, timeout=120, env={**{k: v for k, v in os.environ.items()
                                                                       if k != "PYTHONPATH"}, "DANA_ENV_FILE": ""}),
}
if SHOWCASE.is_dir():
    jobs["showcase_check"] = lambda: dana("addons", "check", str(SHOWCASE), "--json")
    jobs["showcase_test"] = lambda: dana("addons", "test", str(SHOWCASE))
with ThreadPoolExecutor(max_workers=6) as pool:
    futures = {name: pool.submit(job) for name, job in jobs.items()}
R = {name: f.result() for name, f in futures.items()}

# --- 1. new → check → test --------------------------------------------------------------------------
rc, out, err = R["check"]
report = json.loads(out) if out.startswith("{") else {}
check("check --json: the new addon passes (no errors, no warnings)", rc == 0 and report.get("ok")
      and report["problems"] == [] and report["name"] == "tides" and report["version"] == "0.1.0"
      and report["touches"]["hosts"] == ["api.example.com"], out + err)
rc, out, err = R["test"]
check("test: its tests pass against Dana's harness", rc == 0 and "PASS  tests/test_tides.py" in out
      and "1 passed, 0 failed" in out, out + err)
done = R["alone"]
check("test alone: DANA_HOME finds Dana, and a clone named dana-addon-tides loads as tides", done.returncode == 0
      and "All tides checks passed" in done.stdout, done.stdout[-1500:] + done.stderr[-1500:])
done = R["nowhere"]
check("test alone: a DANA_HOME that is not Dana says so", done.returncode != 0
      and "is not a Dana checkout" in done.stderr, done.stderr)

done = R["no_tests"]
check("bin/dana imports without the tests folder (as in the server's image)", done.returncode == 0, done.stderr[-1500:])

# --- 2. check catches what the code does but the manifest does not declare ----------------------------
bad = R["bad"]
report = bad.to_json()
check("mismatch: the check fails with errors", not bad.ok and report["errors"] > 0, report)
host = found(report, "error", "api.weather.example")
check("mismatch: a host the code calls but touches= does not list, with the line and the fix",
      host.get("file") == "tools.py" and host.get("line", 0) > 0
      and 'Add "api.weather.example" to touches=Touches(hosts=(...))' in host.get("fix", ""), host)
env1, env2 = found(report, "error", "TIDES_BELL_TOKEN"), found(report, "error", "TIDES_MODE")
check("mismatch: env values read but not declared (os.environ.get and os.environ[...]), fix: a Need",
      'Need("env", "TIDES_BELL_TOKEN"' in env1.get("fix", "") and 'Need("env", "TIDES_MODE"' in env2.get("fix", "")
      and "credentials=" in env1.get("fix", ""), (env1, env2))
reads = found(report, "error", "imports the tasks addon")
check("mismatch: another addon imported without touches.reads or a need, fix: reads=",
      'Touches(reads=(...))' in reads.get("fix", "") and 'Need("addon", "tasks"' in reads.get("fix", ""), reads)
param = found(report, "error", "tides_ring", "'loud'")
check("mismatch: a tool parameter the docstring does not describe, fix: Args:",
      'Add "loud: <what it is>" under Args:' in param.get("fix", ""), report)
outside = found(report, "error", "tides_ring", "acts_outside=True")
check("mismatch: an acts_outside tool while touches= does not say so",
      "Add acts_outside=True to touches=Touches(...)" in outside.get("fix", ""), outside)
unused = found(report, "warning", "unused.example.org")
check("mismatch: a declared host the code never names is a warning, not an error", unused.get("fix", "").startswith(
    "Fine when the address comes from the user"), unused)
numbers = found(report, "error", "Migration numbers must run 1..N")
version = found(report, "error", "VERSION must be 1")
named = found(report, "error", "add_notes.py is not named like a migration")
nodoc = found(report, "warning", "no docstring")
check("migrations: misnamed file, gap, VERSION against the file name, a missing docstring (each with its fix)",
      numbers and version.get("fix") == "Write VERSION = 1." and "mNNNN_" in named.get("fix", "")
      and nodoc.get("file") == "migrations/m0001_baseline.py", (numbers, version, named, nodoc))
text = bad.text()
check("check (plain text): each problem with where and its fix, then the count",
      re.search(r"error\s+tools\.py:\d+\s+The code calls api\.weather\.example", text)
      and 'fix: Add "api.weather.example"' in text and re.search(r"FAILED: \d+ error\(s\), \d+ warning\(s\)", text), text)
vague = R["vague"].to_json()
check("manifest: touches= that is not plain values is refused before anything runs",
      found(vague, "error", "touches= is not written with plain values")
      and not found(vague, "error", "It does not load"), vague)
check("imports: an addon that fails to import says why",
      found(R["broken"].to_json(), "error", "It does not load", "not_a_module_anywhere"), R["broken"].to_json())

# --- 3. The reference --------------------------------------------------------------------------------
check("reference: docs/addon-reference.md is what the code generates (bin/dana addons reference)",
      reference.OUTPUT.read_text(encoding="utf-8") == reference.render())
ref = reference.render()
check("reference: every hook, the manifest fields, the schema; examples quoted from the showcase", all(
    f"`@addon.{h}" in ref for h, _, _ in reference.HOOKS) and "| `touches` |" in ref and '"$defs"' in ref
    and "examples/addons/showcase/tools.py" in ref and "def showcase_add_plant" in ref)
stale = SCRATCH / "stale.md"
stale.write_text("old\n")
(r1, o1, _), (r2, o2, e2) = dana("addons", "reference", "--check", "--out", str(stale)), dana("addons", "reference", "--check")
check("reference --check: 1 for a stale file, 0 for the real one", r1 == 1 and "out of date" in o1 and r2 == 0, o1 + o2 + e2)

# --- 4. The showcase example -------------------------------------------------------------------------
if SHOWCASE.is_dir():
    rc, out, err = R["showcase_check"]
    check("showcase: check passes with nothing to say", rc == 0 and json.loads(out)["problems"] == [], out + err)
    rc, out, err = R["showcase_test"]
    check("showcase: its tests pass (every capability once)", rc == 0 and "1 passed, 0 failed" in out, out + err)
    template = SERVER / "cli" / "template" / "tests"
    check("showcase: its harness and fakes are the template's", all(
        (SHOWCASE / "tests" / name).read_text() == (template / f"{name}.tmpl").read_text()
        for name in ("dana_harness.py", "fakes.py")))
else:
    print("[SKIP] showcase: no examples/ beside server/")

# --- 5. publish --dry-run ----------------------------------------------------------------------------
rc, out, err = R["publish"]
check("publish --dry-run: checks and tests first", rc == 0 and "OK: 0 error(s)" in out and "1 passed, 0 failed" in out,
      out + err)
expected = ["gh repo view robin-example/dana-addon-tides --json name",
            "gh repo create robin-example/dana-addon-tides --public",
            f"git tag -a v0.1.0 -m 'Tides 0.1.0' {COMMIT}", "git push origin v0.1.0",
            "gh release create v0.1.0 --repo robin-example/dana-addon-tides",
            "gh repo clone ronganam/dana-catalog", "gh repo fork ronganam/dana-catalog --clone=false",
            "git remote add fork https://github.com/robin-example/dana-catalog.git",
            "git checkout -b addon-tides-0.1.0", "git commit -am 'Add tides 0.1.0'", "git push fork addon-tides-0.1.0",
            "gh pr create --repo ronganam/dana-catalog --head robin-example:addon-tides-0.1.0"]
missing = [c for c in expected if c not in out]
check("publish --dry-run: prints every gh / git command (repo, tag, release, catalog fork, branch, pull request)",
      not missing and out.count("would run:") >= len(expected), missing)
entry = json.loads(out.split("catalog.json with this entry:\n", 1)[1].split("\nwould run:", 1)[0]) \
    if "with this entry:" in out else {}
check("publish --dry-run: the catalog entry is pinned to the full commit, with its touches",
      entry.get("source") == {"repo": "https://github.com/robin-example/dana-addon-tides", "path": "", "ref": COMMIT}
      and entry.get("touches", {}).get("hosts") == ["api.example.com"] and entry.get("name") == "tides"
      and entry.get("author") == "robin-example", entry)
check("publish --dry-run: nothing was done (no tag)", publish.git(["tag"], TIDES) == ""
      and "Dry run: nothing was published" in out)
rc, mine, err = R["publish_own"]
check("publish --dry-run: your own catalog repository needs no fork; --repo names the addon's", rc == 0
      and "gh repo fork" not in mine and "git push origin addon-tides-0.1.0" in mine
      and "gh repo create robin-example/tides" in mine, mine + err)
rc, out, err = R["dirty"]
check("publish: refused with uncommitted changes, before anything else", rc == 1 and "uncommitted changes" in err
      and "would run" not in out, out + err)
catalog = {"about": "x", "addons": [{"name": "zeta"}, {"name": "tides", "tags": ["old"]}]}
bumped = publish.with_entry(catalog, {"name": "tides", "v": 2})
added = publish.with_entry({"addons": [{"name": "zeta"}]}, {"name": "alpha"})
check("publish: the catalog entry replaces the one of that name, or is added; entries by name",
      bumped == {"about": "x", "addons": [{"name": "tides", "v": 2}, {"name": "zeta"}]}
      and [e["name"] for e in added["addons"]] == ["alpha", "zeta"])

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
