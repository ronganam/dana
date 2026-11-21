"""The addon catalog: ``catalog.json`` at the repository root, read by the
server and the CLI (../docs/addons.md, "The catalog").

``{"addons": [{name, title, description, icon, author, homepage, tags, min_dana,
source: {repo, path, ref}}]}`` (``icon``: optional, a Lucide icon name); ``ref`` is a full commit hash, so an entry
always means exactly one version of the addon. Read from ``DANA_CATALOG_FILE``
(a local file: tests, offline servers) or ``DANA_CATALOG_URL`` (default
:data:`DEFAULT_URL`), with a short timeout, and kept for
:data:`CACHE_SECONDS`. An entry that is not valid (a missing field, a ref that
is not a full commit, a name that is not an identifier, a repeated name) is
skipped and its reason logged and returned with the entries.
"""

import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.addons.addon import ICON
from core.addons.install import FULL_COMMIT, InstallError, Source, check_path, check_repo

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://raw.githubusercontent.com/ronganam/dana/master/catalog.json"
TIMEOUT_SECONDS = 5.0
CACHE_SECONDS = 600.0
MAX_BYTES = 1_000_000
_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


class CatalogError(RuntimeError):
    """The catalog cannot be read at all (network, not JSON, not ``{"addons": [...]}``)."""


@dataclass(frozen=True)
class Entry:
    """One catalog entry."""
    name: str
    title: str
    description: str
    source: Source
    author: str = ""
    homepage: str = ""
    tags: Tuple[str, ...] = ()
    min_dana: str = ""
    icon: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description, "icon": self.icon,
                "author": self.author,
                "homepage": self.homepage, "tags": list(self.tags), "min_dana": self.min_dana,
                "source": {"repo": self.source.repo, "path": self.source.path, "ref": self.source.ref}}


@dataclass
class Catalog:
    """The valid entries of a catalog, the reasons the others were skipped, and where it was read from."""
    entries: List[Entry]
    problems: List[str] = field(default_factory=list)
    origin: str = ""

    def get(self, name: str) -> Optional[Entry]:
        return next((e for e in self.entries if e.name == name), None)


_cache: Dict[str, Tuple[float, Catalog]] = {}


def origin() -> str:
    """Where the catalog is read from: ``DANA_CATALOG_FILE``, else ``DANA_CATALOG_URL``, else :data:`DEFAULT_URL`."""
    return (os.environ.get("DANA_CATALOG_FILE") or "").strip() or \
        (os.environ.get("DANA_CATALOG_URL") or "").strip() or DEFAULT_URL


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _entry(raw: Any) -> Entry:
    """One entry from its JSON.

    Raises:
        ValueError: why it is not valid.
    """
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    name = _text(raw.get("name"))
    if not name.isidentifier() or name != name.lower():
        raise ValueError(f"name {raw.get('name')!r} is not a lower-case identifier")
    src = raw.get("source")
    if not isinstance(src, dict):
        raise ValueError("source must be {repo, path, ref}")
    ref = _text(src.get("ref")).lower()
    if not FULL_COMMIT.match(ref):
        raise ValueError(f"source.ref {src.get('ref')!r} is not a full commit hash")
    try:
        source = Source(check_repo(_text(src.get("repo"))), check_path(_text(src.get("path"))), ref)
    except InstallError as e:
        raise ValueError(str(e)) from None
    tags = raw.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("tags must be a list of strings")
    icon = _text(raw.get("icon"))
    if icon and not ICON.match(icon):
        raise ValueError(f"icon {icon!r} is not a Lucide icon name")
    min_dana = _text(raw.get("min_dana"))
    if min_dana and not re.match(r"^\d+(\.\d+)*$", min_dana):
        raise ValueError(f"min_dana {min_dana!r} is not a version")
    return Entry(name=name, title=_text(raw.get("title")) or name.replace("_", " ").capitalize(),
                 description=_text(raw.get("description")), source=source, author=_text(raw.get("author")),
                 homepage=_text(raw.get("homepage")), tags=tuple(tags), min_dana=min_dana, icon=icon)


def parse(data: Any, where: str = "") -> Catalog:
    """The catalog in ``data`` (parsed JSON); invalid entries skipped with a logged reason.

    Raises:
        CatalogError: not ``{"addons": [...]}``.
    """
    if not isinstance(data, dict) or not isinstance(data.get("addons"), list):
        raise CatalogError(f"The catalog at {where or 'its source'} is not {{\"addons\": [...]}}.")
    out = Catalog(entries=[], origin=where)
    for i, raw in enumerate(data["addons"]):
        label = raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else f"#{i + 1}"
        try:
            entry = _entry(raw)
        except ValueError as e:
            out.problems.append(f"{label}: {e}")
            continue
        if out.get(entry.name) is not None:
            out.problems.append(f"{label}: listed twice (the first is kept)")
            continue
        out.entries.append(entry)
    for p in out.problems:
        logger.warning(f"Catalog entry skipped: {p}")
    return out


def _read(where: str) -> bytes:
    if where.startswith(("http://", "https://")):
        req = urllib.request.Request(where, headers={"User-Agent": "dana-server", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:  # noqa: S310 — an http(s) URL only
            return r.read(MAX_BYTES + 1)
    return Path(where).expanduser().read_bytes()


def load(*, fresh: bool = False) -> Catalog:
    """The catalog (blocking: from a thread in the server), cached for :data:`CACHE_SECONDS`.

    Raises:
        CatalogError: it cannot be read or is not a catalog.
    """
    where = origin()
    hit = _cache.get(where)
    if hit is not None and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        raw = _read(where)
    except Exception as e:
        raise CatalogError(f"The catalog at {where} cannot be read ({type(e).__name__}: {e}).") from e
    if len(raw) > MAX_BYTES:
        raise CatalogError(f"The catalog at {where} is larger than {MAX_BYTES} bytes.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise CatalogError(f"The catalog at {where} is not JSON ({e}).") from e
    catalog = parse(data, where)
    _cache[where] = (time.monotonic(), catalog)
    return catalog


def dana_version() -> str:
    """This server's version (the VERSION file, without a release suffix)."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip().split("-")[0]
    except OSError:
        return "0"


def _tuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def compatible(entry: Entry, version: Optional[str] = None) -> bool:
    """This server is at least the entry's ``min_dana``."""
    return not entry.min_dana or _tuple(version or dana_version()) >= _tuple(entry.min_dana)
