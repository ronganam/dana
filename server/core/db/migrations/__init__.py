"""Schema migrations: core's (this package) and each addon's
(``addons/<name>/migrations/``), applied by :func:`core.db.migrate`.

A migration is a module ``mNNNN_<name>.py`` with ``VERSION`` (int, the same
NNNN), ``NAME`` and ``up(conn)``. ``up`` runs inside the runner's transaction:
it must not commit. Numbers are per addon (core is the addon ``core``) and
run 1, 2, 3, … without gaps or repeats; an addon touches only its own tables
(and may reference core's). To change a schema, add the next file to its
folder; it is found by its name, nothing to register. Never edit one that has
shipped. A migration that rebuilds a table other tables reference sets
``FOREIGN_KEYS_OFF = True``: the runner turns foreign keys off around it.

Addon migrations are read from the addon folders (core/addon_dirs.py) by
file path, without importing the addon. Order: core's first, then each addon's
after the addons its manifest ``needs`` (read from ``addon.py`` without running
it: :func:`core.addon_dirs.read_manifest`); among addons free to go, the folder
order (official first, then by name). A site / installed addon whose ``needs``
are missing or form a cycle is left out (:func:`plan` says why); an official one
stops the start.
"""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Set, Tuple

from core import addon_dirs
from core.addon_dirs import AddonFolder

CORE = "core"
_FILE = re.compile(r"^m(\d{4})_\w+$")
# Loaded migration modules by file: each file is executed once per process.
_modules: Dict[Path, ModuleType] = {}


@dataclass(frozen=True)
class Migration:
    """One migration of ``addon`` (``core`` for core's own)."""
    addon: str
    module: ModuleType

    @property
    def version(self) -> int:
        return int(self.module.VERSION)

    @property
    def name(self) -> str:
        return str(self.module.NAME)

    @property
    def label(self) -> str:
        return f"{self.addon}:{self.version:04d}_{self.name}"


def _load(addon: str, path: Path) -> ModuleType:
    path = path.resolve()
    if path in _modules:
        return _modules[path]
    spec = importlib.util.spec_from_file_location(f"_dana_migrations.{addon}.{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{path}: cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _modules[path] = module
    return module


def discover(addon: str, folder: Path) -> Tuple[Migration, ...]:
    """The migrations in ``folder``, in version order (checked: 1..N, ``VERSION`` matching the file name)."""
    if not folder.is_dir():
        return ()
    found = sorted((int(m.group(1)), p) for p in folder.glob("m*.py") if (m := _FILE.match(p.stem)))
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"{addon}: migration numbers must run 1..N without gaps or repeats, found {numbers}")
    out = []
    for number, path in found:
        module = _load(addon, path)
        if module.VERSION != number:
            raise RuntimeError(f"{addon} {path.name}: VERSION = {module.VERSION}, the file name says {number}")
        out.append(Migration(addon, module))
    return tuple(out)


@dataclass(frozen=True)
class Plan:
    """Every addon's migrations in the order they run (core's first), the
    site / installed addons left out because their ``needs`` cannot be met
    (name → why), and which addons are official."""
    order: List[Tuple[str, Tuple[Migration, ...]]]
    failed: Dict[str, str]
    official: Set[str]


def ordered_folders() -> Tuple[List[AddonFolder], Dict[str, str]]:
    """Every addon folder after the addons its manifest ``needs`` (a name seen
    twice keeps its first folder: the loader refuses that start), and the
    site / installed ones that cannot be placed, with why.

    Raises:
        RuntimeError: an official addon's ``needs`` are missing or form a cycle.
    """
    folders: Dict[str, AddonFolder] = {}
    for f in addon_dirs.addon_folders():
        folders.setdefault(f.name, f)
    needs = {name: f.depends for name, f in folders.items()}
    placed: List[AddonFolder] = []
    done: Set[str] = set()
    waiting = list(folders.values())
    while True:
        free = next((f for f in waiting if all(d in done for d in needs[f.name])), None)
        if free is None:
            break
        placed.append(free)
        done.add(free.name)
        waiting.remove(free)
    failed: Dict[str, str] = {}
    for f in waiting:
        missing = [d for d in needs[f.name] if d not in folders]
        if missing:
            failed[f.name] = f"needs {', '.join(missing)}, which is not there"
    changed = True
    while changed:
        changed = False
        for f in waiting:
            lost = [d for d in needs[f.name] if d in failed]
            if f.name not in failed and lost:
                failed[f.name] = f"needs {', '.join(lost)}, which cannot load"
                changed = True
    for f in waiting:
        failed.setdefault(f.name, f"its needs ({', '.join(needs[f.name])}) form a cycle")
    for f in waiting:
        if f.source.official:
            raise RuntimeError(f"The official addon {f.name!r} cannot be migrated: {failed[f.name]}")
    return placed, failed


def plan() -> Plan:
    """What :func:`core.db.migrate` runs, in order (see the module docstring)."""
    placed, failed = ordered_folders()
    order = [(CORE, discover(CORE, Path(__file__).parent))]
    for folder in placed:
        found = discover(folder.name, folder.migrations)
        if found:
            order.append((folder.name, found))
    return Plan(order, failed, {f.name for f in placed if f.source.official})


def all_migrations() -> List[Tuple[str, Tuple[Migration, ...]]]:
    """Core's migrations, then each addon's in :func:`plan` order, as (addon, migrations)."""
    return plan().order


def official_with_migrations() -> List[str]:
    """The official addons whose first migration is a baseline (their tables
    exist in a converted 9.0 database). An addon whose first migration is
    something else (``homeassistant``'s settings clean-up) is not one: a
    converted database applies it like any other pending migration."""
    return [f.name for f in addon_dirs.addon_folders() if f.source.official
            and any(m.version == 1 and m.name == "baseline" for m in discover(f.name, f.migrations))]
