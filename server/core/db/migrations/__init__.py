"""Ordered schema migrations, applied by :func:`core.db.migrate`.

Each migration is a module ``mNNNN_<name>.py`` in this package with ``VERSION``
(int, the same NNNN), ``NAME`` and ``up(conn)``. ``up`` runs inside the runner's
transaction: it must not commit. To change the schema, add the next file; it is
found by its name, nothing to register. Never edit one that has shipped. A
migration that rebuilds a table other tables reference sets
``FOREIGN_KEYS_OFF = True``: the runner turns foreign keys off around it.

Versions must run 1, 2, 3, … without gaps or repeats; two branches that each
added the same number fail here at import, before anything touches a database.
"""

import importlib
import pkgutil
import re
from types import ModuleType
from typing import Tuple

_FILE = re.compile(r"^m(\d{4})_\w+$")


def _discover() -> Tuple[ModuleType, ...]:
    """Every migration module of this package, in version order (checked)."""
    found = sorted((int(m.group(1)), info.name)
                   for info in pkgutil.iter_modules(__path__)
                   if (m := _FILE.match(info.name)))
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"migration numbers must run 1..N without gaps or repeats, found {numbers}")
    modules = tuple(importlib.import_module(f"{__name__}.{name}") for _, name in found)
    for (number, name), module in zip(found, modules):
        if module.VERSION != number:
            raise RuntimeError(f"{name}: VERSION = {module.VERSION}, the file name says {number}")
    return modules


MIGRATIONS: Tuple[ModuleType, ...] = _discover()
