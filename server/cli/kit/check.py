"""``bin/dana addons check <path>``: what is wrong with an addon, each problem with its fix.

Checked, in order:

1. **The manifest** read without running it: ``addon.py`` declares ``addon = Addon("<name>", ...)``
   with a usable name, ``touches=`` written with plain values.
2. **Migrations**: ``migrations/mNNNN_<name>.py`` numbered 1..N, each with ``VERSION`` (its
   number), ``NAME``, ``up(conn)`` and a docstring saying what it changes.
3. **Loading** (:mod:`cli.kit.probe`, a clean process): it imports, loads next to the official
   addons and its migrations run; its manifest fits the JSON schema (:mod:`cli.kit.schema`);
   every tool has a docstring summary and every parameter is described under ``Args:`` (the
   schema the model reads); a tool that ``acts_outside`` matches ``touches.acts_outside``.
4. **Declared vs used** (a best-effort scan of the code with :mod:`ast`; tests are skipped):
   every host named in an ``http(s)://`` / ``ws(s)://`` string is in ``touches.hosts`` (a
   subdomain of one counts; this machine's addresses do not), every env value read
   (``os.getenv``, ``os.environ``) is a ``Need("env", ...)`` (or the worker's ``url_env``),
   every other addon it imports is in ``touches.reads`` or ``needs``. A declared host or env
   value the code never names is a warning (an address the user gives, a library's).

Errors make the check fail; warnings do not.
"""

import ast
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from core import addon_dirs
from core.addons import install
from core.addons.hooks import CORE_SCREENS, Touches
from cli.kit import probe as probe_module
from cli.kit import runner, schema

SERVER = Path(__file__).resolve().parents[2]
ERROR, WARNING = "error", "warning"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})
URL = re.compile(r"\b(?:https?|wss?)://([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9])")
MIGRATION_FILE = re.compile(r"^m(\d{4})_(\w+)$")
# Calls whose string arguments are words for people, not addresses the code calls.
WORDS_CALLS = frozenset({"Need", "PromptPart", "Field", "Approval", "Nav", "Card", "Check", "Section", "Hit", "DayItem"})
PROBE_TIMEOUT = 180


@dataclass
class Problem:
    """One thing ``check`` found: ``level`` error / warning, where (a file of the addon and a line, or
    ""), what is wrong and how to fix it."""
    level: str
    message: str
    fix: str
    file: str = ""
    line: int = 0

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.file and self.line else self.file


@dataclass
class Report:
    """What ``check`` found for the addon at ``path``."""
    path: Path
    name: str = ""
    version: str = ""
    touches: Dict[str, Any] = field(default_factory=dict)
    problems: List[Problem] = field(default_factory=list)
    # The loaded manifest as JSON (cli/kit/schema.py); {} when it did not load.
    manifest: Dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> List[Problem]:
        return [p for p in self.problems if p.level == ERROR]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, level: str, message: str, fix: str, file: str = "", line: int = 0) -> None:
        self.problems.append(Problem(level, message, fix, file, line))

    def to_json(self) -> Dict[str, Any]:
        return {"path": str(self.path), "name": self.name, "version": self.version, "ok": self.ok,
                "errors": len(self.errors), "warnings": len(self.problems) - len(self.errors),
                "touches": self.touches, "problems": [asdict(p) for p in self.problems]}

    def text(self) -> str:
        lines = [f"Checking {self.name or '?'} ({self.path})"]
        for p in self.problems:
            lines.append(f"{p.level:<8}{(p.where + '  ') if p.where else ''}{p.message}")
            lines.append(f"{'':<8}fix: {p.fix}")
        warnings = len(self.problems) - len(self.errors)
        lines.append(f"{'OK' if self.ok else 'FAILED'}: {len(self.errors)} error(s), {warnings} warning(s)")
        return "\n".join(lines) + "\n"


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def code_files(root: Path) -> List[Path]:
    """The addon's Python files, tests left out."""
    return sorted(p for p in root.rglob("*.py") if "tests" not in p.relative_to(root).parts
                  and "__pycache__" not in p.parts and not any(part.startswith(".") for part in p.relative_to(root).parts))


# --- 1. The manifest, read --------------------------------------------------------------------------

def check_manifest(report: Report) -> Optional[addon_dirs.Manifest]:
    root = report.path
    if not root.is_dir():
        report.add(ERROR, f"{root} is not a folder.", "Give the addon's folder (the one holding addon.py).")
        return None
    if not (root / "addon.py").is_file():
        report.add(ERROR, "There is no addon.py.", "Start from bin/dana addons new <name>, or add addon.py with "
                   "addon = Addon(\"<name>\", ...).")
        return None
    manifest = addon_dirs.read_manifest(root / "addon.py")
    if manifest is None:
        report.add(ERROR, "addon.py declares no addon = Addon(\"<name>\", ...) with the name as a plain string.",
                   "Write it as addon = Addon(\"<name>\", title=..., ...) at the top level of addon.py.", "addon.py")
        return None
    report.name = manifest.name
    name = manifest.name
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in install.RESERVED or name in CORE_SCREENS:
        report.add(ERROR, f"The name {name!r} cannot be used.", "Lower-case letters, digits and _, starting with a "
                   f"letter, and not one of {', '.join(sorted(install.RESERVED | set(CORE_SCREENS)))}.", "addon.py")
    elif (addon_dirs.OFFICIAL_DIR / name).is_dir():
        report.add(ERROR, f"{name!r} is an official addon's name.", "Pick another name (and rename the folder).",
                   "addon.py")
    if manifest.touches is None:
        report.add(ERROR, "touches= is not written with plain values, so it cannot be read before installing.",
                   "Write Touches(hosts=(\"api.example.com\",), stores_credentials=True) with strings, tuples and "
                   "True / False only.", "addon.py")
    else:
        try:
            install.manifest_touches(root / "addon.py")
        except install.InstallError as e:
            report.add(ERROR, str(e), "Correct touches= in addon.py (the message says what is wrong).", "addon.py")
    return manifest


# --- 2. Migrations ----------------------------------------------------------------------------------

def check_migrations(report: Report) -> None:
    folder = report.path / "migrations"
    if not folder.is_dir():
        return
    numbered: List[Tuple[int, Path]] = []
    for p in sorted(folder.glob("*.py")):
        if p.name == "__init__.py":
            continue
        m = MIGRATION_FILE.match(p.stem)
        if not m:
            report.add(ERROR, f"{p.name} is not named like a migration, so it never runs.",
                       "Name it mNNNN_<what>.py, numbered after the last one (m0002_add_notes.py).", _rel(p, report.path))
            continue
        numbered.append((int(m.group(1)), p))
    numbers = [n for n, _ in numbered]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        report.add(ERROR, f"Migration numbers must run 1..N without gaps or repeats; found {numbers}.",
                   "Renumber the new files to follow the last shipped one (never renumber a shipped one).",
                   "migrations")
    for number, p in numbered:
        rel = _rel(p, report.path)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            report.add(ERROR, f"It does not parse: {e.msg}.", "Fix the syntax error.", rel, e.lineno or 0)
            continue
        values = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
                  if isinstance(t, ast.Name)}
        version = values.get("VERSION")
        if not (isinstance(version, ast.Constant) and version.value == number):
            report.add(ERROR, f"VERSION must be {number}, its file number.", f"Write VERSION = {number}.", rel)
        name = values.get("NAME")
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            report.add(ERROR, "NAME is missing.", f"Write NAME = \"{p.stem.split('_', 1)[1]}\".", rel)
        elif name.value != p.stem.split("_", 1)[1]:
            report.add(WARNING, f"NAME = {name.value!r} differs from the file name.",
                       f"Write NAME = \"{p.stem.split('_', 1)[1]}\" (the part after mNNNN_).", rel)
        if not any(isinstance(n, ast.FunctionDef) and n.name == "up" and n.args.args for n in tree.body):
            report.add(ERROR, "It has no up(conn).", "Define def up(conn: sqlite3.Connection) -> None: with the "
                       "CREATE / ALTER statements (never commit inside it).", rel)
        if not ast.get_docstring(tree):
            report.add(WARNING, "It has no docstring.", "Start the file with a docstring saying what it changes.", rel)


# --- 3. Loading -------------------------------------------------------------------------------------

def run_probe(path: Path) -> Dict[str, Any]:
    """:mod:`cli.kit.probe` on ``path`` in a clean process: its JSON, or ``{"loaded": False, "error"}``."""
    done = subprocess.run([sys.executable, "-m", "cli.kit.probe", str(path)], cwd=SERVER, capture_output=True,
                          text=True, timeout=PROBE_TIMEOUT, env=runner.clean_env())
    line = next((ln for ln in reversed(done.stdout.splitlines()) if ln.startswith(probe_module.MARK)), None)
    if line is None:
        tail = (done.stderr or done.stdout).strip().splitlines()[-5:]
        return {"loaded": False, "error": "The check could not load it: " + (" / ".join(tail) or f"exit {done.returncode}")}
    return json.loads(line[len(probe_module.MARK):])


def check_loaded(report: Report, found: Dict[str, Any], migrations_ok: bool = True) -> Optional[Dict[str, Any]]:
    if not found.get("loaded"):
        report.add(ERROR, f"It does not load: {found.get('error')}",
                   "Fix what the error names (bin/dana addons test shows the whole traceback).", "addon.py")
        return None
    if found.get("migration_error") and migrations_ok:
        report.add(ERROR, f"Its migrations fail on a new database: {found['migration_error']}",
                   "Fix the migration the error names (up(conn) runs inside a transaction: never commit).",
                   "migrations")
    manifest = found["manifest"]
    report.version = manifest.get("version") or ""
    if not report.version:
        report.add(WARNING, "The manifest has no version.", "Add version=\"0.1.0\" (publish tags releases with it).",
                   "addon.py")
    for err in schema.validate(manifest, schema.manifest_schema()):
        report.add(ERROR, f"The manifest does not fit its schema: {err}.",
                   "Correct that field (REFERENCE.md, \"The manifest as JSON\").", "addon.py")
    touches = manifest.get("touches") or {}
    for t in found["tools"]:
        where = _rel(Path(t["file"]), report.path), t["line"]
        if not t["summary"]:
            report.add(ERROR, f"Tool {t['name']} has no docstring: the model would not know what it does.",
                       f"Start {t['name']}'s docstring with one line saying what it does.", *where)
        for param in t["params"]:
            if param not in t["documented"]:
                report.add(ERROR, f"Tool {t['name']}: parameter {param!r} is not described, so the model guesses it.",
                           f"Add \"{param}: <what it is>\" under Args: in {t['name']}'s docstring.", *where)
        for extra in sorted(set(t["documented"]) - set(t["params"])):
            report.add(WARNING, f"Tool {t['name']}: Args: describes {extra!r}, which it does not take.",
                       f"Remove {extra!r} from its Args: (or add the parameter).", *where)
        if t["acts_outside"] and not touches.get("acts_outside"):
            report.add(ERROR, f"Tool {t['name']} is acts_outside=True but touches= does not say acts_outside=True.",
                       "Add acts_outside=True to touches=Touches(...) in addon.py.", *where)
    return manifest


# --- 4. Declared vs used ----------------------------------------------------------------------------

def _parents(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _call_name(node: ast.AST) -> str:
    func = getattr(node, "func", None)
    return getattr(func, "id", "") or getattr(func, "attr", "")


def _is_docstring(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> bool:
    expr = parents.get(node)
    return isinstance(expr, ast.Expr) and isinstance(parents.get(expr), (ast.Module, ast.FunctionDef,
                                                                            ast.AsyncFunctionDef, ast.ClassDef))


def _words(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> bool:
    """``node`` sits inside a call whose strings are words for people (``Field(help=, url=)``, ``Need``…)."""
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.Call) and _call_name(node) in WORDS_CALLS:
            return True
    return False


def hosts_used(tree: ast.AST) -> Iterator[Tuple[str, int]]:
    """(host, line) for each address in a string of the code (not docstrings, not words for people)."""
    parents = _parents(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and isinstance(parents.get(node), ast.JoinedStr):
            continue  # judged with its f-string
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
        else:
            continue
        if _is_docstring(node, parents) or _words(node, parents):
            continue
        for m in URL.finditer(text):
            yield m.group(1).lower(), getattr(node, "lineno", 0)


def _os_names(tree: ast.AST) -> Tuple[Set[str], Set[str], Set[str]]:
    """The names ``os``, ``os.environ`` and ``os.getenv`` go by in this module."""
    oses, environs, getenvs = {"os"}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            oses |= {a.asname or a.name for a in node.names if a.name == "os"}
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            environs |= {a.asname or a.name for a in node.names if a.name == "environ"}
            getenvs |= {a.asname or a.name for a in node.names if a.name == "getenv"}
    return oses, environs, getenvs


def env_used(tree: ast.AST) -> Iterator[Tuple[str, int]]:
    """(name, line) for each env value read with a literal name: ``os.getenv("X")``, ``os.environ.get("X")``,
    ``os.environ["X"]``, ``"X" in os.environ`` (and ``from os import environ, getenv``)."""
    oses, environs, getenvs = _os_names(tree)

    def is_environ(node: ast.AST) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name)
                and node.value.id in oses) or (isinstance(node, ast.Name) and node.id in environs)

    def literal(node: Optional[ast.AST]) -> Optional[str]:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    for node in ast.walk(tree):
        key: Optional[str] = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == "getenv" and isinstance(f.value, ast.Name)
                    and f.value.id in oses) or (isinstance(f, ast.Name) and f.id in getenvs):
                key = literal(node.args[0])
            elif isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault") and is_environ(f.value):
                key = literal(node.args[0])
        elif isinstance(node, ast.Subscript) and is_environ(node.value) and isinstance(node.ctx, ast.Load):
            key = literal(node.slice)
        elif isinstance(node, ast.Compare) and len(node.comparators) == 1 and is_environ(node.comparators[0]) \
                and isinstance(node.ops[0], (ast.In, ast.NotIn)):
            key = literal(node.left)
        if key:
            yield key, node.lineno


def addons_imported(tree: ast.AST, own: str) -> Iterator[Tuple[str, int]]:
    """(addon, line) for each other addon this module imports (``addons.<x>``)."""
    for node in ast.walk(tree):
        names: List[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] if node.module != "addons" else [f"addons.{a.name}" for a in node.names]
        for n in names:
            parts = n.split(".")
            if len(parts) > 1 and parts[0] == "addons" and parts[1] != own:
                yield parts[1], node.lineno


def _covered(host: str, declared: List[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in declared)


def check_declared(report: Report, manifest: Optional[Dict[str, Any]], read: addon_dirs.Manifest) -> None:
    """Compare what the code does with what the manifest declares (see the module docstring)."""
    root = report.path
    raw = manifest.get("touches") if manifest else (read.touches or {})
    try:
        touches = Touches.from_json({k: list(v) if isinstance(v, tuple) else v for k, v in raw.items()})
    except (AttributeError, ValueError):  # reported by check_manifest
        touches = Touches()
    declared_hosts = [h.lower() for h in touches.hosts]
    needs = (manifest or {}).get("needs") or []
    env_needs = {n["what"] for n in needs if n.get("kind") == "env"}
    addon_needs = {n["what"] for n in needs if n.get("kind") == "addon"} | set(read.depends)
    worker = (manifest or {}).get("worker") or {}
    seen_hosts: Set[str] = set()
    seen_env: Set[str] = set()
    for path in code_files(root):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            report.add(ERROR, f"It does not parse: {e.msg}.", "Fix the syntax error.", rel, e.lineno or 0)
            continue
        in_worker = rel.split("/")[0] == "worker"
        reported: Set[str] = set()
        for host, line in hosts_used(tree):
            if host in LOCAL_HOSTS:
                continue
            seen_hosts.add(host)
            if not _covered(host, declared_hosts) and host not in reported:
                reported.add(host)
                report.add(ERROR, f"The code calls {host}, which touches= does not list.",
                           f"Add \"{host}\" to touches=Touches(hosts=(...)) in addon.py (or a parent domain of it).",
                           rel, line)
        if in_worker:  # a separate process with its own environment
            continue
        for key, line in env_used(tree):
            seen_env.add(key)
            if key not in env_needs and key != worker.get("url_env") and key not in reported:
                reported.add(key)
                report.add(ERROR, f"The code reads the env value {key}, which needs= does not declare.",
                           f"Add Need(\"env\", \"{key}\", \"<what it is for>\", optional=True) to needs= in addon.py "
                           "(optional=False when it cannot work without it). A per-user value belongs in "
                           "settings= or credentials= instead.", rel, line)
        for other, line in addons_imported(tree, report.name):
            if other not in touches.reads and other not in addon_needs and other not in reported:
                reported.add(other)
                report.add(ERROR, f"The code imports the {other} addon, which neither touches.reads nor needs= names.",
                           f"Add \"{other}\" to touches=Touches(reads=(...)) when it reads its data, or "
                           f"Need(\"addon\", \"{other}\", \"<why>\") when it cannot work without it.", rel, line)
    for host in declared_hosts:
        if "." in host and " " not in host and not any(_covered(h, [host]) for h in seen_hosts):
            report.add(WARNING, f"touches= lists {host}, but the code never names it.",
                       "Fine when the address comes from the user or a library; otherwise remove it.", "addon.py")
    for key in sorted(env_needs - seen_env):
        report.add(WARNING, f"needs= declares the env value {key}, but the code never reads it.",
                   "Remove the Need, or read it where it is used.", "addon.py")


# --- Everything -------------------------------------------------------------------------------------

def check(path: Path) -> Report:
    """Every check above on the addon in ``path``."""
    report = Report(path=Path(path).expanduser().resolve())
    read = check_manifest(report)
    if read is None:
        return report
    report.touches = {k: list(v) if isinstance(v, tuple) else v for k, v in (read.touches or {}).items()}
    readable = report.ok
    before = len(report.problems)
    check_migrations(report)
    migrations_ok = not any(p.level == ERROR for p in report.problems[before:])
    manifest = check_loaded(report, run_probe(report.path), migrations_ok) if readable else None
    if manifest is not None:
        report.manifest = manifest
        report.touches = manifest.get("touches") or report.touches
    check_declared(report, manifest, read)
    if not (report.path / "README.md").is_file():
        report.add(WARNING, "There is no README.md.", "Write one: what it does, its settings, the events it emits "
                   "(the catalog shows it).")
    if not list((report.path / "tests").glob("test_*.py")):
        report.add(WARNING, "There are no tests (tests/test_*.py).", "Add one: bin/dana addons new writes a "
                   "starting point with the users and fakes it needs.")
    return report
