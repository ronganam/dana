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

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

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


@dataclass(frozen=True)
class Source:
    """One folder addons are loaded from."""
    kind: str
    path: Path

    @property
    def official(self) -> bool:
        return self.kind == OFFICIAL


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


def site_dir() -> Path:
    """The deployment's site folder: ``DANA_SITE``, else ``site/`` at the repository root."""
    raw = os.environ.get("DANA_SITE", "").strip()
    return Path(raw).expanduser().resolve() if raw else SERVER.parent / "site"


def sources() -> Tuple[Source, ...]:
    """The source folders that exist, official first."""
    raw = os.environ.get("DANA_ADDON_DIRS")
    if raw is not None:
        others = [Source(EXTRA, Path(d).expanduser().resolve()) for d in raw.split(os.pathsep) if d.strip()]
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
