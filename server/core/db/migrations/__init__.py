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
file path, without importing the addon (the runner runs before the addons load).
"""

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, List, Tuple

from core import addon_dirs

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


def all_migrations() -> List[Tuple[str, Tuple[Migration, ...]]]:
    """Core's migrations, then each addon folder's (official first, then site /
    image / installed; by name within a source), as (addon, migrations)."""
    out = [(CORE, discover(CORE, Path(__file__).parent))]
    for folder in addon_dirs.addon_folders():
        found = discover(folder.name, folder.migrations)
        if found:
            out.append((folder.name, found))
    return out


def official_with_migrations() -> List[str]:
    """The official addons that have migrations (their baselines exist in a converted 9.0 database)."""
    return [f.name for f in addon_dirs.addon_folders() if f.source.official and discover(f.name, f.migrations)]
