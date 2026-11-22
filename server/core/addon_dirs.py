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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Manifest:
    """What an ``addon.py`` declares, read without running it: the name, the ``needs`` and the
    worker's published image (``worker=Worker(..., image="...")``; "" without one)."""
    name: str
    needs: Tuple[str, ...] = ()
    worker_image: str = ""


def read_manifest(addon_py: Path) -> Optional[Manifest]:
    """The name, ``needs`` and worker image that ``addon.py`` declares (``addon = Addon("<name>",
    needs=(...), worker=Worker(..., image="..."), ...)``), read with :mod:`ast`, never run; None when
    no such declaration is found. ``needs`` must be a literal tuple / list of strings and ``image`` a
    literal string to be read (anything else reads as none)."""
    try:
        tree = ast.parse(addon_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "addon" for t in node.targets)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and getattr(call.func, "id", getattr(call.func, "attr", "")) == "Addon"):
            continue
        args: List[Any] = list(call.args[:1]) + [k.value for k in call.keywords if k.arg == "name"]
        if not (args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
            continue
        needs: Tuple[str, ...] = ()
        for k in call.keywords:
            if k.arg == "needs":
                try:
                    value = ast.literal_eval(k.value)
                except ValueError:
                    value = ()
                if isinstance(value, (tuple, list)) and all(isinstance(v, str) for v in value):
                    needs = tuple(value)
        image = ""
        for k in call.keywords:
            if k.arg == "worker" and isinstance(k.value, ast.Call):
                image = next((w.value.value for w in k.value.keywords if w.arg == "image"
                              and isinstance(w.value, ast.Constant) and isinstance(w.value.value, str)), "")
        return Manifest(args[0].value, needs, image)
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
    def needs(self) -> Tuple[str, ...]:
        """The ``needs`` its ``addon.py`` declares, read without importing it (:func:`read_manifest`)."""
        manifest = read_manifest(self.path / "addon.py")
        return manifest.needs if manifest else ()


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
