"""Finding and importing every addon (../docs/addons.md, "Where addons come from").

:func:`load` (run once, by importing the ``addons`` package) takes the addon
folders of every source (core/addon_dirs.py), and for each one imports, as
``addons.<name>``:

1. ``addon.py`` — the manifest (``addon = Addon("<name>", ...)``);
2. then, in the addon order: ``tools.py``, ``hooks.py`` and the modules its
   manifest names (``modules=``), when present, then ``api.py`` (its routes,
   registered by core/server.py through :func:`route_modules`).

Order: an addon comes after every addon in its ``needs``; among those free to
go, official addons first by their ``order=`` (then name), then the others by
name. That order is the order of every tool list and hook result.

Failures: two addons with one name stop the start (:class:`LoadError`). An
official addon that fails to import, needs a missing addon or declares an
unknown prompt slot stops the start too (it is a bug). A site or installed
addon in that case is skipped and recorded with the error (:func:`failed`):
its tools, hooks and routes are not there, the server runs without it.
"""

import importlib
import logging
import sys
import traceback
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, List, Optional, Set, Tuple

from core import addon_dirs, purposes
from core.addon_dirs import AddonFolder
from core.addons import addon as addon_registry
from core.addons.addon import Addon
from core.agent import SLOTS

logger = logging.getLogger(__name__)

# The modules of an addon imported after its manifest, in this order, when present.
STANDARD_MODULES = ("tools", "hooks")


class LoadError(RuntimeError):
    """The addons cannot be loaded: a name clash, or an official addon is broken."""


@dataclass(frozen=True)
class Failed:
    """A site / installed addon that was not loaded, and why."""
    name: str
    source: str
    path: str
    error: str


_loaded: Optional[Tuple[str, ...]] = None
_loading = False
_failed: Dict[str, Failed] = {}
_folders: Dict[str, AddonFolder] = {}
_apis: Dict[str, ModuleType] = {}


def _package() -> ModuleType:
    """The ``addons`` package (being imported when :func:`load` runs from its ``__init__``)."""
    return sys.modules["addons"]


def _has(folder: AddonFolder, module: str) -> bool:
    return (folder.path / (module.replace(".", "/") + ".py")).is_file() or \
        (folder.path / module.replace(".", "/") / "__init__.py").is_file()


def _fail(folder: AddonFolder, error: str) -> None:
    """Record ``folder``'s addon as failed and remove whatever it registered.

    Raises:
        LoadError: it is an official addon.
    """
    if folder.source.official:
        raise LoadError(f"The official addon {folder.name!r} failed to load: {error}")
    logger.error(f"Addon {folder.name} ({folder.source.kind}, {folder.path}) not loaded: {error}")
    _failed[folder.name] = Failed(folder.name, folder.source.kind, str(folder.path), error)
    addon_registry.forget(folder.name)
    for mod in [m for m in sys.modules if m == f"addons.{folder.name}" or m.startswith(f"addons.{folder.name}.")]:
        del sys.modules[mod]


def _error(e: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(e), e)).strip()


def _manifest(folder: AddonFolder) -> Optional[Addon]:
    """Import ``folder``'s ``addon.py``; its Addon, or None (recorded as failed)."""
    try:
        module = importlib.import_module(f"addons.{folder.name}.addon")
    except Exception as e:
        _fail(folder, _error(e))
        return None
    declared = getattr(module, "addon", None)
    if not isinstance(declared, Addon) or declared.name != folder.name:
        _fail(folder, f"addon.py must declare addon = Addon({folder.name!r}, ...)")
        return None
    return declared


def _ordered(manifests: Dict[str, Addon]) -> List[str]:
    """``manifests`` in load order (needs first); addons whose needs are missing or circular are failed."""
    def rank(name: str) -> Tuple[int, int, str]:
        a, f = manifests[name], _folders[name]
        return (0, a.order if a.order is not None else 10_000, name) if f.source.official else (1, 0, name)

    placed: List[str] = []
    waiting = dict(manifests)
    while waiting:
        free = [n for n, a in waiting.items() if all(d in placed for d in a.needs)]
        if not free:
            break
        name = min(free, key=rank)
        placed.append(name)
        del waiting[name]
    for name, a in sorted(waiting.items()):
        missing = [d for d in a.needs if d not in manifests]
        _fail(_folders[name], f"needs {', '.join(missing)}, which is not loaded" if missing
              else f"its needs ({', '.join(a.needs)}) form a cycle")
    return placed


def _check_parts(a: Addon) -> Optional[str]:
    """Why ``a``'s prompt parts or tools cannot be used (an unknown slot or addon), or None."""
    for part in a.prompt_parts:
        if part.slot not in SLOTS:
            return f"prompt part slot {part.slot!r} is not one of {', '.join(SLOTS)}"
    return None


def _import_modules(folder: AddonFolder, a: Addon) -> bool:
    """Import the rest of ``folder``'s addon (tools, hooks, declared modules, api); False when it failed."""
    names = [m for m in STANDARD_MODULES if _has(folder, m)] + list(a.modules)
    try:
        for m in names:
            importlib.import_module(f"addons.{folder.name}.{m}")
        if _has(folder, "api"):
            api = importlib.import_module(f"addons.{folder.name}.api")
            if not callable(getattr(api, "setup", None)):
                raise TypeError("api.py must define setup(app)")
            _apis[folder.name] = api
    except Exception as e:
        _fail(folder, _error(e))
        return False
    return True


def load() -> Tuple[str, ...]:
    """Import every addon (once); returns the loaded names in order.

    Raises:
        LoadError: two addons share a name, or an official addon is broken.
    """
    global _loaded, _loading
    if _loaded is not None:
        return _loaded
    if _loading:
        raise LoadError("load() was called again while the addons are loading (an addon module calls it at import)")
    if "addons" not in sys.modules:
        importlib.import_module("addons")  # its __init__ runs load()
        if _loaded is not None:
            return _loaded
    _loading = True
    try:
        return _load()
    finally:
        _loading = False


def _load() -> Tuple[str, ...]:
    global _loaded
    folders = addon_dirs.addon_folders()
    for f in folders:
        if f.name in _folders:
            raise LoadError(f"Addon {f.name!r} in {f.path} clashes with another addon of that name "
                            f"({_folders[f.name].path})")
        _folders[f.name] = f
    package = _package()
    for src in addon_dirs.sources():
        if not src.official and str(src.path) not in package.__path__:
            package.__path__.append(str(src.path))  # addons.<name> resolves inside that folder too
    manifests: Dict[str, Addon] = {}
    for f in folders:
        a = _manifest(f)
        if a is not None:
            manifests[f.name] = a
    order = _ordered(manifests)
    loaded: List[str] = []
    done: Set[str] = set()
    for name in order:
        f, a = _folders[name], manifests[name]
        lost = [d for d in a.needs if d not in done]
        if lost:
            _fail(f, f"needs {', '.join(lost)}, which failed to load")
            continue
        problem = _check_parts(a)
        if problem:
            _fail(f, problem)
            continue
        if not _import_modules(f, a):
            continue
        loaded.append(name)
        done.add(name)
    addon_registry.set_order(loaded)
    try:
        addon_registry.validate()
    except ValueError as e:
        raise LoadError(str(e)) from None
    for name in loaded:
        for purpose, job in addon_registry.get_addon(name).llm_jobs.items():
            purposes.register(purpose, job.profile, job.label, addon=name)
    _loaded = tuple(loaded)
    logger.info(f"Addons loaded: {', '.join(_loaded)}"
                + (f"; failed: {', '.join(_failed)}" if _failed else ""))
    return _loaded


def loaded() -> Tuple[str, ...]:
    """The loaded addons, in order (loading them first if needed)."""
    return load()


def failed() -> Dict[str, Failed]:
    """Site / installed addons that were found but not loaded, by name."""
    load()
    return dict(_failed)


def folder(name: str) -> AddonFolder:
    """Where a loaded (or failed) addon lives.

    Raises:
        KeyError: no addon of that name was found.
    """
    load()
    return _folders[name]


def route_modules() -> Tuple[ModuleType, ...]:
    """The ``api`` module (with ``setup(app)``) of every loaded addon that has one, in order."""
    return tuple(_apis[n] for n in load() if n in _apis)


def non_official() -> Tuple[str, ...]:
    """The loaded addons that do not ship with Dana (site, image, installed), in order."""
    return tuple(n for n in load() if not _folders[n].source.official)
