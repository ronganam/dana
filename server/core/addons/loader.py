"""Finding and importing every addon (../docs/addons.md, "Where addons come from").

:func:`load` (run once, by importing the ``addons`` package) takes the addon
folders of every source (core/addon_dirs.py), and for each one imports, as
``addons.<name>``:

1. ``addon.py`` — the manifest (``addon = Addon("<name>", ...)``, the name the
   folder's);
2. then, in the addon order: ``tools.py``, ``hooks.py`` and the modules its
   manifest names (``modules=``), when present, then ``api.py`` (its routes,
   registered by core/server.py through :func:`apis`).

Order: an addon comes after every addon in its ``needs``; among those free to
go, official addons first by their ``order=`` (then name), then the others by
name. That order is the order of every tool list and hook result.

Each addon is checked on its own, after its modules are imported: its prompt
slots exist, every ``needs`` of its tools and prompt parts names a loaded
addon, its page ids and keyed hooks (``core.addons.addon.KEYED_HOOKS``: card
prefixes, section, check and count keys, item types, context kinds, provider
keys) are not another addon's, its LLM purposes are not taken, and it declared exactly one addon,
its folder's. :func:`migrate` then applies the database migrations; an addon
whose migrations fail is taken out again (:func:`unload`), and so is one whose
routes core/server.py refuses.

Failures: two addons with one name stop the start (:class:`LoadError`). An
official addon that fails any of the above stops the start too (it is a bug).
A site or installed addon is skipped and recorded with the error
(:func:`failed`), with every addon that needs it: whatever it registered
(tools, hooks, jobs, routes, LLM purposes, shareable item types) is removed and
the server runs without it. Anything it raises while importing is caught
(``BaseException``, except ``KeyboardInterrupt`` and ``SystemExit``).
"""

import importlib
import importlib.metadata
import logging
import re
import sys
import traceback
from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Dict, List, Optional, Set, Tuple

from core import addon_dirs, db, purposes
from core.addon_dirs import AddonFolder
from core.addons import addon as addon_registry
from core.addons import sharing
from core.addons.addon import Addon, all_addons
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
# The addon names each folder's modules declared (normally just its own).
_declared: Dict[str, Set[str]] = {}
# The commit each installed (or site) addon was at when it loaded (its .installed.json).
_commits: Dict[str, str] = {}


def _package() -> ModuleType:
    """The ``addons`` package (being imported when :func:`load` runs from its ``__init__``)."""
    return sys.modules["addons"]


def _has(folder: AddonFolder, module: str) -> bool:
    return (folder.path / (module.replace(".", "/") + ".py")).is_file() or \
        (folder.path / module.replace(".", "/") / "__init__.py").is_file()


def _fail(folder: AddonFolder, error: str) -> None:
    """Record ``folder``'s addon as failed and remove whatever it registered
    (every addon name its modules declared, their tools and hooks, its LLM
    purposes and shareable item types, its modules).

    Raises:
        LoadError: it is an official addon.
    """
    if folder.source.official:
        raise LoadError(f"The official addon {folder.name!r} failed to load: {error}")
    logger.error(f"Addon {folder.name} ({folder.source.kind}, {folder.path}) not loaded: {error}")
    _failed[folder.name] = Failed(folder.name, folder.source.kind, str(folder.path), error)
    for name in {folder.name, *_declared.pop(folder.name, set())}:
        addon_registry.forget(name)
        purposes.forget_addon(name)
        sharing.forget_addon(name)
    _apis.pop(folder.name, None)
    for mod in [m for m in sys.modules if m == f"addons.{folder.name}" or m.startswith(f"addons.{folder.name}.")]:
        del sys.modules[mod]


def _error(e: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(e), e)).strip()


def _guarded(folder: AddonFolder, step: Callable[[], None]) -> bool:
    """Run ``step`` (an import of ``folder``'s addon); False when it raised and
    the addon was failed. A site / installed addon's every exception is caught
    (``BaseException`` but ``KeyboardInterrupt`` / ``SystemExit``); an official
    addon's ``Exception`` fails the start, anything else passes through."""
    before = {a.name for a in all_addons()}
    try:
        step()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:
        if folder.source.official and not isinstance(e, Exception):
            raise
        _declared.setdefault(folder.name, set()).update({a.name for a in all_addons()} - before)
        _fail(folder, _error(e))
        return False
    new = {a.name for a in all_addons()} - before
    _declared.setdefault(folder.name, set()).update(new)
    strangers = sorted(new - {folder.name})
    if strangers:
        _fail(folder, f"it declared another addon ({', '.join(strangers)}); an addon folder declares only "
                      f"Addon({folder.name!r}, ...)")
        return False
    return True


# Leading PEP 508 distribution name on a requirements.txt line.
_REQ_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def _check_requirements(folder: AddonFolder) -> Optional[str]:
    """Verify every package in ``folder/requirements.txt`` (when present) is
    installed in this Python environment; returns an error message or None."""
    req_file = folder.path / "requirements.txt"
    if not req_file.is_file():
        return None
    missing: List[str] = []
    try:
        lines = req_file.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return f"could not read requirements.txt: {e}"
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        spec = line.split(";", 1)[0].strip()
        match = _REQ_NAME.match(spec)
        if not match:
            continue
        pkg = match.group(1)
        try:
            importlib.metadata.distribution(pkg)
        except importlib.metadata.PackageNotFoundError:
            missing.append(pkg)
    if missing:
        return f"missing package(s) from requirements.txt: {', '.join(missing)}"
    return None


def _manifest(folder: AddonFolder) -> Optional[Addon]:
    """Import ``folder``'s ``addon.py``; its Addon, or None (recorded as failed)."""
    holder: Dict[str, ModuleType] = {}

    def step() -> None:
        holder["module"] = importlib.import_module(f"addons.{folder.name}.addon")

    if not _guarded(folder, step):
        return None
    declared = getattr(holder["module"], "addon", None)
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
    """Why ``a``'s prompt parts cannot be used (an unknown slot), or None."""
    for part in a.prompt_parts:
        if part.slot not in SLOTS:
            return f"prompt part slot {part.slot!r} is not one of {', '.join(SLOTS)}"
    return None


def _import_modules(folder: AddonFolder, a: Addon) -> bool:
    """Import the rest of ``folder``'s addon (tools, hooks, declared modules, api); False when it failed."""
    names = [m for m in STANDARD_MODULES if _has(folder, m)] + list(a.modules)

    def step() -> None:
        for m in names:
            importlib.import_module(f"addons.{folder.name}.{m}")
        if _has(folder, "api"):
            api = importlib.import_module(f"addons.{folder.name}.api")
            if not callable(getattr(api, "setup", None)):
                raise TypeError("api.py must define setup(app)")
            _apis[folder.name] = api

    return _guarded(folder, step)


def _register_purposes(a: Addon) -> Optional[str]:
    """Register ``a``'s LLM purposes; why they cannot be (taken, unknown profile), or None."""
    try:
        for purpose, job in a.llm_jobs.items():
            purposes.register(purpose, job.profile, job.label, addon=a.name)
    except ValueError as e:
        purposes.forget_addon(a.name)
        return str(e)
    return None


def _dependents(name: str, among: List[str]) -> List[str]:
    """The addons of ``among`` that need ``name``, directly or through another."""
    out: List[str] = []
    changed = True
    while changed:
        changed = False
        for n in among:
            if n not in out and n != name and any(d == name or d in out
                                                  for d in addon_registry.get_addon(n).needs):
                out.append(n)
                changed = True
    return out


def _drop(name: str, error: str, loaded: List[str]) -> None:
    """Fail ``name`` and every addon of ``loaded`` that needs it; removes them from ``loaded``."""
    for n in [name, *_dependents(name, loaded)]:
        if n in loaded:
            loaded.remove(n)
            _fail(_folders[n], error if n == name else f"needs {name}, which failed to load")


def _settle(loaded: List[str]) -> None:
    """Fail every addon of ``loaded`` whose tools or prompt parts need an addon
    that is not loaded, until none does (failing one can strand another)."""
    while True:
        problem = next(((n, p) for n in loaded
                        if (p := addon_registry.needs_problem(addon_registry.get_addon(n), set(loaded)))), None)
        if problem is None:
            return
        _drop(problem[0], problem[1], loaded)


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
        info = addon_dirs.installed_info(f.path) if not f.source.official else None
        if info and isinstance(info.get("commit"), str):
            _commits[f.name] = info["commit"]
    package = _package()
    for src in addon_dirs.sources():
        if not src.official and str(src.path) not in package.__path__:
            package.__path__.append(str(src.path))  # addons.<name> resolves inside that folder too
    manifests: Dict[str, Addon] = {}
    for f in folders:
        req_err = _check_requirements(f)
        if req_err:
            _fail(f, req_err)
            continue
        a = _manifest(f)
        if a is not None:
            manifests[f.name] = a
    order = _ordered(manifests)
    loaded: List[str] = []
    for name in order:
        f, a = _folders[name], manifests[name]
        lost = [d for d in a.needs if d not in loaded]
        if lost:
            _fail(f, f"needs {', '.join(lost)}, which failed to load")
            continue
        problem = _check_parts(a)
        if problem:
            _fail(f, problem)
            continue
        if not _import_modules(f, a):
            continue
        problem = addon_registry.key_clash(a, [addon_registry.get_addon(n) for n in loaded]) or _register_purposes(a)
        if problem:
            _fail(f, problem)
            continue
        loaded.append(name)
    _settle(loaded)
    addon_registry.set_order(loaded)
    _loaded = tuple(loaded)
    logger.info(f"Addons loaded: {', '.join(_loaded)}"
                + (f"; failed: {', '.join(_failed)}" if _failed else ""))
    return _loaded


def unload(name: str, error: str) -> Tuple[str, ...]:
    """Take a loaded site / installed addon out again (its migrations failed,
    core/server.py refused its routes), with every addon that needs it; they
    are recorded as failed with ``error``. Returns the names taken out.

    Raises:
        LoadError: it is an official addon.
    """
    global _loaded
    current = list(load())
    if name not in current:
        return ()
    _drop(name, error, current)
    _settle(current)
    gone = tuple(n for n in _loaded or () if n not in current)
    _loaded = tuple(current)  # forgetting them took them out of the addon order too
    return gone


def migrate(path: Optional[db.PathLike] = None) -> db.Migrated:
    """Load the addons, then apply the database migrations of core and of
    every addon not failed (core/db): a site / installed addon whose
    migrations fail (or cannot be ordered) is taken out (:func:`unload`) and
    recorded as failed; the server starts without it. Returns what
    :func:`core.db.migrate` returns.

    Raises:
        core.db.SchemaError: the database cannot be migrated.
        Exception: a migration of core or of an official addon failed.
    """
    load()
    result = db.migrate(path, skip=set(_failed))
    for name, why in result.failed.items():
        unload(name, why)
    return result


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


def apis() -> Tuple[Tuple[str, ModuleType], ...]:
    """(addon name, its ``api`` module) for every loaded addon that has one, in order."""
    return tuple((n, _apis[n]) for n in load() if n in _apis)


def loaded_commit(name: str) -> Optional[str]:
    """The commit addon ``name`` was installed from when this process loaded it (None: not installed from git)."""
    load()
    return _commits.get(name)


def folders() -> Dict[str, AddonFolder]:
    """Every addon folder found at load (loaded and failed), by name."""
    load()
    return dict(_folders)


def non_official() -> Tuple[str, ...]:
    """The loaded addons that do not ship with Dana (site, image, installed), in order."""
    return tuple(n for n in load() if not _folders[n].source.official)
