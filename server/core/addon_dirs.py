"""Where addons live (../docs/addons.md, "Where addons come from"): the source
folders and the addon folders in them. Plain filesystem lookups, importing
nothing of the app, so the database runner (core/db) and the loader
(core/addons/loader.py) can both use it.

Sources, in this order:

- ``official``: ``server/addons/`` (ships with Dana);
- ``site``: the deployment's site folder's ``addons/`` (``$DANA_SITE/addons``,
  else ``site/addons`` at the repository root; docs/site.md);
- ``image``: ``/app/addons-site`` in the container image (the site addons
  ``bin/deploy`` builds in);
- ``installed``: ``<data dir>/addons`` (addons installed from the catalog).

``DANA_ADDON_DIRS`` (folders separated by ``os.pathsep``) replaces every source
but the official one (``""``: official addons only, what the tests use).
Folders that do not exist are skipped. An addon folder is a sub-folder with an
``addon.py``.
"""

import ast
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import ADDONS_DIR

SERVER = Path(__file__).resolve().parent.parent
OFFICIAL_DIR = SERVER / "addons"
IMAGE_DIR = Path("/app/addons-site")

OFFICIAL = "official"
SITE = "site"
IMAGE = "image"
INSTALLED = "installed"
# A folder named in DANA_ADDON_DIRS.
EXTRA = "extra"
# The file an installed addon's folder keeps its origin in (core/addons/install.py).
INSTALLED_FILE = ".installed.json"


@dataclass(frozen=True)
class Source:
    """One folder addons are loaded from."""
    kind: str
    path: Path

    @property
    def official(self) -> bool:
        return self.kind == OFFICIAL

    @property
    def label(self) -> str:
        """The source as people see it: official, site, image or installed (a
        ``DANA_ADDON_DIRS`` folder counts as site: it replaces the site's)."""
        return SITE if self.kind == EXTRA else self.kind


# Touches(...)'s fields in order (core/addons/hooks.py), for a manifest read without running it.
TOUCHES_FIELDS = ("hosts", "acts_outside", "stores_credentials", "reads")
_MISSING = object()


@dataclass(frozen=True)
class Manifest:
    """What an ``addon.py`` declares, read without running it: the name, the
    addons it ``depends`` on (its required ``Need("addon", ...)``), its
    ``touches`` as plain values (``Touches(...)``'s keywords; ``{}`` when it
    declares none, None when they are not written as plain values) and the
    worker's published image (``worker=Worker(..., image="...")``; "" without one)."""
    name: str
    depends: Tuple[str, ...] = ()
    touches: Optional[Dict[str, Any]] = field(default_factory=dict)
    worker_image: str = ""


def _call_name(node: ast.AST) -> str:
    return getattr(getattr(node, "func", None), "id", "") or getattr(getattr(node, "func", None), "attr", "")


def _literal(node: Optional[ast.AST], default: Any) -> Any:
    if node is None:
        return default
    try:
        return ast.literal_eval(node)
    except ValueError:
        return _MISSING


def _depends(value: ast.AST) -> Tuple[str, ...]:
    """The required addon names of a ``needs=(Need(...), ...)`` written with plain values."""
    if not isinstance(value, (ast.Tuple, ast.List)):
        return ()
    out: List[str] = []
    for item in value.elts:
        if not (isinstance(item, ast.Call) and _call_name(item) == "Need"):
            continue
        given: Dict[str, Optional[ast.AST]] = dict(zip(("kind", "what", "why", "optional"), item.args))
        given.update({k.arg: k.value for k in item.keywords if k.arg})
        kind, what, optional = (_literal(given.get("kind"), None), _literal(given.get("what"), None),
                                _literal(given.get("optional"), False))
        if kind == "addon" and isinstance(what, str) and optional is False:
            out.append(what)
    return tuple(out)


def _touches(value: ast.AST) -> Optional[Dict[str, Any]]:
    """The keywords of ``touches=Touches(...)`` when every value is plain, else None."""
    if not (isinstance(value, ast.Call) and _call_name(value) == "Touches") or len(value.args) > len(TOUCHES_FIELDS):
        return None
    given: Dict[str, Optional[ast.AST]] = dict(zip(TOUCHES_FIELDS, value.args))
    for k in value.keywords:
        if k.arg is None:
            return None
        given[k.arg] = k.value
    out = {name: _literal(node, None) for name, node in given.items()}
    return None if any(v is _MISSING for v in out.values()) else out


def read_manifest(addon_py: Path) -> Optional[Manifest]:
    """What ``addon.py`` declares (``addon = Addon("<name>", needs=(...), touches=Touches(...),
    worker=Worker(..., image="..."), ...)``), read with :mod:`ast`, never run; None when no such
    declaration is found. Only ``Need(...)`` entries written with plain values count towards
    ``depends``, and ``image`` must be a literal string to be read."""
    try:
        tree = ast.parse(addon_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "addon" for t in node.targets)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and _call_name(call) == "Addon"):
            continue
        args: List[Any] = list(call.args[:1]) + [k.value for k in call.keywords if k.arg == "name"]
        if not (args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
            continue
        keywords = {k.arg: k.value for k in call.keywords if k.arg}
        depends = _depends(keywords["needs"]) if "needs" in keywords else ()
        touches = _touches(keywords["touches"]) if "touches" in keywords else {}
        worker = keywords.get("worker")
        image = ""
        if isinstance(worker, ast.Call):
            image = next((w.value.value for w in worker.keywords if w.arg == "image"
                          and isinstance(w.value, ast.Constant) and isinstance(w.value.value, str)), "")
        return Manifest(args[0].value, depends, touches, image)
    return None


@dataclass(frozen=True)
class AddonFolder:
    """One addon's folder: its name (the folder name) and where it came from."""
    name: str
    path: Path
    source: Source

    @property
    def migrations(self) -> Path:
        """Its migrations folder (may not exist)."""
        return self.path / "migrations"

    @property
    def depends(self) -> Tuple[str, ...]:
        """The addons its ``addon.py`` depends on, read without importing it (:func:`read_manifest`)."""
        manifest = read_manifest(self.path / "addon.py")
        return manifest.depends if manifest else ()


def installed_dir() -> Path:
    """Where the server installs addons (the ``installed`` source): ``<data dir>/addons``."""
    return ADDONS_DIR


def site_dir() -> Path:
    """The deployment's site folder: ``DANA_SITE``, else ``site/`` at the repository root."""
    raw = os.environ.get("DANA_SITE", "").strip()
    return Path(raw).expanduser().resolve() if raw else SERVER.parent / "site"


def sources() -> Tuple[Source, ...]:
    """The source folders that exist, official first."""
    raw = os.environ.get("DANA_ADDON_DIRS")
    if raw is not None:
        # The data dir's addons/ named here is still the installed source (what the server installs into).
        others = [Source(INSTALLED if p == ADDONS_DIR.resolve() else EXTRA, p)
                  for p in (Path(d).expanduser().resolve() for d in raw.split(os.pathsep) if d.strip())]
    else:
        others = [Source(SITE, site_dir() / "addons"), Source(IMAGE, IMAGE_DIR), Source(INSTALLED, ADDONS_DIR)]
    found: List[Source] = [Source(OFFICIAL, OFFICIAL_DIR)]
    for src in others:
        if src.path.is_dir() and all(s.path != src.path for s in found):
            found.append(src)
    return tuple(found)


def addon_folders() -> Tuple[AddonFolder, ...]:
    """Every addon folder of every source: sources in order, then by name (names may repeat: the loader refuses that)."""
    out: List[AddonFolder] = []
    for src in sources():
        for p in sorted(src.path.iterdir(), key=lambda p: p.name):
            if p.is_dir() and (p / "addon.py").is_file() and p.name.isidentifier():
                out.append(AddonFolder(p.name, p, src))
    return tuple(out)


def installed_info(path: Path) -> Optional[Dict[str, Any]]:
    """What an installed addon's folder says about its origin (``repo``,
    ``path``, ``commit``, ``installed_at``…), or None without a readable
    :data:`INSTALLED_FILE`."""
    try:
        data = json.loads((path / INSTALLED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def worker_image(path: Path) -> Optional[str]:
    """The image reference the worker of the addon in ``path`` runs, pinned: the manifest's
    ``Worker(image=...)`` as written when it names a tag or digest, else tagged with the commit
    it was installed at (its :data:`INSTALLED_FILE`); None when it declares no image (a ``worker/``
    folder is built instead: docs/operations.md, "Workers").

    Raises:
        ValueError: an image without a tag in a folder that records no commit.
    """
    manifest = read_manifest(path / "addon.py")
    image = manifest.worker_image if manifest else ""
    if not image:
        return None
    if "@" in image or ":" in image.rsplit("/", 1)[-1]:
        return image
    commit = str((installed_info(path) or {}).get("commit") or "")
    if not commit:
        raise ValueError(f"{path.name}: the worker image {image} has no tag and the addon records no installed commit")
    return f"{image}:{commit}"
