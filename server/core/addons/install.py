"""Installing an addon from a git repository at one commit, and removing it
(../docs/addons.md, "The catalog"; the web Addons page and ``bin/dana addons
install``).

:func:`install` fetches exactly one commit (``git init`` + ``git fetch --depth 1
<repo> <commit>`` + ``git checkout``, in a temporary folder, never a shell),
checks the folder ``path`` in it (an ``addon.py`` declaring ``Addon("<name>",
...)``, the name a free identifier, no symbolic links) and copies it to
``<root>/<name>`` with an :data:`~core.addon_dirs.INSTALLED_FILE` saying where
it came from. ``root`` is ``<data dir>/addons`` for the server (the
``installed`` source) or the site's ``addons/`` for the CLI. Nothing is
imported or run here: the server loads the addon at its next start, which is
why a restart is needed. An installed addon runs with the server's full
access; only admins install, and the page says so before.

Refused: a ref that is not a full 40-character commit hash, a name that is an
official addon or an addon of another source (two addons with one name stop
the start), a reserved name, a path leaving the repository, a folder without
``addon.py`` or whose manifest names another addon. :func:`remove` deletes the
folder (the addon's tables and rows stay).
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from core import addon_dirs

FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Names that would shadow the Addons API's own paths (/api/addons/catalog, /install).
RESERVED = frozenset({"catalog", "install", "restart", "addons", "core"})
GIT_TIMEOUT_SECONDS = 120.0
# Folders and files never copied out of the fetched repository.
SKIPPED = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "node_modules", addon_dirs.INSTALLED_FILE)

_lock = threading.Lock()


class InstallError(ValueError):
    """An install or remove that is refused or failed; the message says why in a sentence."""


@dataclass(frozen=True)
class Source:
    """Where an addon comes from: a git repository, a folder in it ("" = its root), a full commit hash."""
    repo: str
    path: str
    ref: str


@dataclass(frozen=True)
class Installed:
    """An addon written to ``folder`` from ``source``; ``replaced``: it was there before (an update)."""
    name: str
    folder: Path
    source: Source
    replaced: bool


def check_ref(ref: str) -> str:
    """``ref`` when it is a full commit hash (lower case).

    Raises:
        InstallError: anything else (a branch, a tag, a short hash).
    """
    ref = (ref or "").strip().lower()
    if not FULL_COMMIT.match(ref):
        raise InstallError("The ref must be a full 40-character commit hash (not a branch, tag or short hash).")
    return ref


def check_repo(repo: str) -> str:
    """``repo`` when it can be given to git as a repository.

    Raises:
        InstallError: empty, starting with ``-`` (an option), or with spaces / control characters.
    """
    repo = (repo or "").strip()
    if not repo or repo.startswith("-") or any(c.isspace() or ord(c) < 32 for c in repo):
        raise InstallError("The repository must be a git URL (https://…, git@…) or a path.")
    return repo


def check_path(path: str) -> str:
    """``path`` as a relative POSIX path inside a repository ("" for its root).

    Raises:
        InstallError: absolute, or with ``..``.
    """
    raw = (path or "").strip().strip("/")
    if not raw:
        return ""
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts or "\\" in raw:
        raise InstallError(f"The path {path!r} must be a folder inside the repository.")
    return str(p)


def check_name(name: str, root: Path) -> None:
    """``name`` may be installed into ``root``: an identifier, not reserved,
    not an official addon nor an addon of a source other than ``root``.

    Raises:
        InstallError: why not.
    """
    if not name.isidentifier() or name != name.lower():
        raise InstallError(f"The addon name {name!r} must be a lower-case identifier (letters, digits, _).")
    if name in RESERVED:
        raise InstallError(f"The addon name {name!r} is reserved.")
    if (addon_dirs.OFFICIAL_DIR / name).is_dir():
        raise InstallError(f"{name!r} is an official addon; it ships with Dana and cannot be installed.")
    root = root.resolve()
    for src in addon_dirs.sources():
        if src.official or src.path.resolve() == root:
            continue
        if (src.path / name / "addon.py").is_file():
            raise InstallError(f"An addon called {name!r} is already in {src.path} ({src.label}); "
                               "two addons with one name would stop the server.")


def _git(args: Iterable[str], cwd: Path) -> None:
    if shutil.which("git") is None:
        raise InstallError("git is not installed on this server.")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", GIT_CONFIG_NOSYSTEM="1")
    try:
        done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as e:
        raise InstallError(f"git took longer than {GIT_TIMEOUT_SECONDS:.0f} seconds.") from e
    if done.returncode != 0:
        lines = [ln for ln in (done.stderr or "").strip().splitlines() if ln.strip()]
        raise InstallError(f"git failed: {lines[-1] if lines else f'exit status {done.returncode}'}")


def _symlinks(folder: Path) -> List[str]:
    return [str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_symlink()]


def fetch(source: Source, into: Path) -> Path:
    """Check out ``source.ref`` of ``source.repo`` into the empty folder ``into``; returns the folder ``source.path`` in it.

    Raises:
        InstallError: git failed, or the path is not a folder of that commit.
    """
    _git(["init", "-q"], into)
    _git(["fetch", "-q", "--depth", "1", "--", source.repo, source.ref], into)
    _git(["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], into)
    folder = (into / source.path).resolve() if source.path else into.resolve()
    if not folder.is_relative_to(into.resolve()) or not folder.is_dir():
        raise InstallError(f"The commit has no folder {source.path!r}.")
    return folder


def install(source: Source, root: Path, *, name: Optional[str] = None, by: str = "") -> Installed:
    """Install the addon at ``source`` into ``root/<name>`` (replacing an earlier
    install of it there). ``name``: the name it must have (a catalog entry's);
    ``by``: who installs it (kept in the folder's record).

    Raises:
        InstallError: refused (see the module docstring) or git failed.
    """
    source = Source(check_repo(source.repo), check_path(source.path), check_ref(source.ref))
    root = root.resolve()
    with _lock, tempfile.TemporaryDirectory(prefix="dana-addon-") as tmp:
        folder = fetch(source, Path(tmp))
        if not (folder / "addon.py").is_file():
            raise InstallError(f"{source.path or 'The repository root'} has no addon.py at {source.ref[:12]}.")
        manifest = addon_dirs.read_manifest(folder / "addon.py")
        declared = manifest.name if manifest else None
        if declared is None:
            raise InstallError('addon.py must declare addon = Addon("<name>", ...).')
        if name is not None and declared != name:
            raise InstallError(f"The manifest at that commit declares {declared!r}, not {name!r}.")
        check_name(declared, root)
        links = _symlinks(folder)
        if links:
            raise InstallError(f"The addon folder has symbolic links ({', '.join(links[:3])}); they are not installed.")
        root.mkdir(parents=True, exist_ok=True)
        target = root / declared
        staging = root / f".{declared}.new"
        old = root / f".{declared}.old"
        for leftover in (staging, old):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.copytree(folder, staging, ignore=SKIPPED)
        record: Dict[str, Any] = {"name": declared, "repo": source.repo, "path": source.path, "commit": source.ref,
                                  "installed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                                  "installed_by": by}
        (staging / addon_dirs.INSTALLED_FILE).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        replaced = target.exists()
        if replaced:
            target.rename(old)
        staging.rename(target)
        shutil.rmtree(old, ignore_errors=True)
    return Installed(declared, target, source, replaced)


def remove(name: str, root: Path) -> Path:
    """Delete the addon folder ``root/<name>`` (its tables stay). Returns the folder removed.

    Raises:
        InstallError: an official addon, or no such addon folder in ``root``.
    """
    if (addon_dirs.OFFICIAL_DIR / name).is_dir():
        raise InstallError(f"{name!r} is an official addon; disable it instead.")
    root = root.resolve()
    target = (root / name).resolve()
    if not name.isidentifier() or target.parent != root or not (target / "addon.py").is_file():
        raise InstallError(f"There is no addon {name!r} in {root}.")
    with _lock:
        shutil.rmtree(target)
    return target


def installed(root: Path) -> Dict[str, Dict[str, Any]]:
    """The addon folders in ``root`` by name, each with its record (``{}`` when it has none)."""
    if not root.is_dir():
        return {}
    return {p.name: addon_dirs.installed_info(p) or {} for p in sorted(root.iterdir())
            if p.is_dir() and p.name.isidentifier() and (p / "addon.py").is_file()}
