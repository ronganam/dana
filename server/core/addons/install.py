"""Installing an addon into ``<data dir>/addons`` (the ``installed`` source;
../docs/addons.md, "Installing an addon"): from a git repository at one commit
(a catalog entry or any repository) or from an uploaded folder, and removing
it. Used by the Addons API (core/addons_api.py) and ``bin/dana addons install``.

:func:`install` fetches exactly one commit (``git init`` + ``git fetch --depth 1
<repo> <commit>`` + ``git checkout``, in a temporary folder, never a shell);
:func:`install_archive` unpacks a ``.tar.gz`` of the addon folder (its files at
the archive's root, or in one top folder) after checking every member: regular
files and folders only (no links, devices), no absolute path or ``..``, at most
:data:`MAX_ARCHIVE_FILES` files and :data:`MAX_UNPACKED_BYTES` unpacked. Both
then check the folder the same way (an ``addon.py`` declaring ``Addon("<name>",
...)``, read never imported; the name a free identifier, not official, reserved
or an addon of another source; no symbolic links) and copy it to
``<root>/<name>`` with an :data:`~core.addon_dirs.INSTALLED_FILE` record: its
``source`` (``{"kind": "catalog" | "git", "repo", "path", "commit"}`` or
``{"kind": "upload"}``), ``installed_at`` and ``installed_by`` (plus ``repo``,
``path``, ``commit`` at the top for a git install). Nothing is imported or run
here: the server loads the addon at its next start, which is why a restart is
needed. An installed addon runs with the server's full access; only admins
install, and the page says so before.

An upload replaces an earlier upload of that name, and a git install an earlier
git install (an update); an upload never replaces a git (or catalog) install
nor the other way round: remove the one there first.

**Touches** (``Touches(...)`` in the manifest, read without running it):
installing asks first when the addon may touch something the admin has not
agreed to. Agreed is the version being replaced (an update) plus ``accept``
(what the admin was shown: the catalog entry's touches, or what an earlier
refusal returned). When the manifest declares more, :class:`ConfirmTouches` is
raised with all its touches and the new ones, and nothing is written; an
update that adds nothing goes ahead without asking. A manifest whose
``touches=`` is not written with plain values is refused.

Refused besides: a ref that is not a full 40-character commit hash, a path
leaving the repository, a folder without ``addon.py`` or whose manifest names
another addon. :func:`remove` deletes the folder (the addon's tables and rows
stay).
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
from zlib import error as zlib_error
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

from core import addon_dirs
from core.addons.hooks import Touches

FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# Names that would shadow the Addons API's own paths (/api/addons/catalog, /install).
RESERVED = frozenset({"catalog", "install", "restart", "welcome", "addons", "core"})
GIT_TIMEOUT_SECONDS = 120.0
# Folders and files never copied out of the fetched repository.
SKIPPED = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "node_modules", addon_dirs.INSTALLED_FILE)

# What an uploaded archive may hold (install_archive).
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000

# The kinds of source an installed addon's record names.
CATALOG = "catalog"
GIT = "git"
UPLOAD = "upload"

_lock = threading.Lock()


class InstallError(ValueError):
    """An install or remove that is refused or failed; the message says why in a sentence."""


class ConfirmTouches(InstallError):
    """The addon may touch more than the admin agreed to: ``touches`` is all it
    declares, ``added`` what is new; install again with ``accept=touches``."""

    def __init__(self, name: str, touches: Touches, added: Touches) -> None:
        super().__init__(f"{name} may touch more than you agreed to; look at what it touches and confirm.")
        self.name = name
        self.touches = touches
        self.added = added


@dataclass(frozen=True)
class Source:
    """Where an addon comes from: a git repository, a folder in it ("" = its root), a full commit hash."""
    repo: str
    path: str
    ref: str


@dataclass(frozen=True)
class Installed:
    """An addon written to ``folder``: ``kind`` catalog / git / upload, ``source`` the
    git source (None for an upload); ``replaced``: it was there before (an update)."""
    name: str
    folder: Path
    kind: str
    source: Optional[Source]
    replaced: bool
    touches: Touches = Touches()


def source_kind(record: Dict[str, Any]) -> str:
    """The kind of source an installed addon's record names (catalog / git / upload; "" when it says none)."""
    source = record.get("source")
    return str(source.get("kind") or "") if isinstance(source, dict) else ""


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


GIT_LOCATION_VARS = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX", "GIT_NAMESPACE",
    "GIT_QUARANTINE_PATH",
})


def _git(args: Iterable[str], cwd: Path) -> None:
    if shutil.which("git") is None:
        raise InstallError("git is not installed on this server.")
    # Variables that point git at another repository (set when Dana runs inside a git hook)
    # would make every command act on that repository instead of `cwd`.
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", GIT_CONFIG_NOSYSTEM="1")
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


def manifest_touches(addon_py: Path) -> Touches:
    """The touches ``addon_py`` declares, read without running it (none declared: touches nothing).

    Raises:
        InstallError: ``touches=`` is not written with plain values, or is not valid.
    """
    manifest = addon_dirs.read_manifest(addon_py)
    raw = manifest.touches if manifest is not None else {}
    if raw is None:
        raise InstallError("touches= in addon.py must be written with plain values (strings, True / False, "
                           "tuples), so it can be read before installing.")
    try:
        return Touches(**{k: tuple(v) if isinstance(v, list) else v for k, v in raw.items()})
    except (TypeError, ValueError) as e:
        raise InstallError(f"touches= in addon.py is not valid: {e}") from e


def installed_touches(folder: Path) -> Touches:
    """What the addon in ``folder`` declares it touches; touches nothing when it cannot be read."""
    try:
        return manifest_touches(folder / "addon.py")
    except InstallError:
        return Touches()


def _place(folder: Path, root: Path, kind: str, source: Optional[Source], *, name: Optional[str],
           by: str, accept: Optional[Touches]) -> Installed:
    """Check the addon ``folder`` and copy it to ``root/<its name>`` with its record (the lock held)."""
    if not (folder / "addon.py").is_file():
        where = f"{source.path or 'The repository root'} has no addon.py at {source.ref[:12]}." if source \
            else "The folder has no addon.py."
        raise InstallError(where)
    manifest = addon_dirs.read_manifest(folder / "addon.py")
    declared = manifest.name if manifest else None
    if declared is None:
        raise InstallError('addon.py must declare addon = Addon("<name>", ...).')
    if name is not None and declared != name:
        raise InstallError(f"The manifest {'at that commit' if source else 'in that folder'} declares "
                           f"{declared!r}, not {name!r}.")
    check_name(declared, root)
    target = root / declared
    if target.is_dir():
        was = source_kind(addon_dirs.installed_info(target) or {})
        if kind == UPLOAD and was != UPLOAD:
            raise InstallError(f"{declared} is installed from {was or 'another source'} here; an upload does not "
                               f"replace it. Remove it first to install it from a folder.")
        if kind != UPLOAD and was == UPLOAD:
            raise InstallError(f"{declared} is installed from an uploaded folder here; a git install does not "
                               f"replace it. Remove it first to install it from git.")
    touches = manifest_touches(folder / "addon.py")
    added = touches.added_since(installed_touches(target) if target.is_dir() else Touches())
    if accept is not None:
        added = added.added_since(accept)
    if not added.empty:
        raise ConfirmTouches(declared, touches, added)
    links = _symlinks(folder)
    if links:
        raise InstallError(f"The addon folder has symbolic links ({', '.join(links[:3])}); they are not installed.")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{declared}.new"
    old = root / f".{declared}.old"
    for leftover in (staging, old):
        shutil.rmtree(leftover, ignore_errors=True)
    shutil.copytree(folder, staging, ignore=SKIPPED)
    record: Dict[str, Any] = {"name": declared}
    if source is not None:
        record.update(repo=source.repo, path=source.path, commit=source.ref,
                      source={"kind": kind, "repo": source.repo, "path": source.path, "commit": source.ref})
    else:
        record["source"] = {"kind": kind}
    record.update(installed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                  installed_by=by)
    (staging / addon_dirs.INSTALLED_FILE).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    replaced = target.exists()
    if replaced:
        target.rename(old)
    staging.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    return Installed(declared, target, kind, source, replaced, touches)


def install(source: Source, root: Path, *, name: Optional[str] = None, by: str = "",
            accept: Optional[Touches] = None, kind: str = GIT) -> Installed:
    """Install the addon at ``source`` into ``root/<name>`` (replacing an earlier
    git install of it there). ``name``: the name it must have (a catalog entry's);
    ``by``: who installs it (kept in the folder's record); ``accept``: the
    touches the admin agreed to, beyond the replaced version's; ``kind``: what the
    record calls the source (:data:`CATALOG` for a catalog entry, else :data:`GIT`).

    Raises:
        ConfirmTouches: it may touch more than agreed (nothing was written).
        InstallError: refused (see the module docstring) or git failed.
    """
    if kind not in (CATALOG, GIT):
        raise ValueError(f"not a git source kind: {kind!r}")
    source = Source(check_repo(source.repo), check_path(source.path), check_ref(source.ref))
    root = root.resolve()
    with _lock, tempfile.TemporaryDirectory(prefix="dana-addon-") as tmp:
        folder = fetch(source, Path(tmp))
        return _place(folder, root, kind, source, name=name, by=by, accept=accept)


def _member_problem(member: tarfile.TarInfo) -> Optional[str]:
    """Why an archive member may not be unpacked, or None."""
    p = PurePosixPath(member.name)
    if member.name.startswith("/") or p.is_absolute() or ".." in p.parts or "\\" in member.name or "\0" in member.name:
        return f"{member.name!r} is an absolute path or leaves the folder"
    if member.issym() or member.islnk():
        return f"{member.name!r} is a link"
    if not (member.isfile() or member.isdir()):
        return f"{member.name!r} is not a plain file or folder"
    return None


def unpack(archive: Path, into: Path) -> Path:
    """Unpack the ``.tar.gz`` ``archive`` into the empty folder ``into`` after checking
    every member; returns the addon folder in it (``into`` when ``addon.py`` is at the
    archive's root, else its one top folder).

    Raises:
        InstallError: not a gzip tar, too big, too many files, or a member refused.
    """
    try:
        size = archive.stat().st_size
    except OSError as e:
        raise InstallError(f"The archive cannot be read: {e}") from e
    if size > MAX_ARCHIVE_BYTES:
        raise InstallError(f"The archive is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB.")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            files = [m for m in members if m.isfile()]
            if len(files) > MAX_ARCHIVE_FILES:
                raise InstallError(f"The archive has more than {MAX_ARCHIVE_FILES} files.")
            if sum(m.size for m in files) > MAX_UNPACKED_BYTES:
                raise InstallError(f"The archive unpacks to more than {MAX_UNPACKED_BYTES // (1024 * 1024)} MB.")
            for m in members:
                problem = _member_problem(m)
                if problem:
                    raise InstallError(f"The archive is refused: {problem}.")
            tar.extractall(into, filter="data")
    except (tarfile.TarError, EOFError, OSError, zlib_error) as e:
        raise InstallError(f"The upload is not a readable .tar.gz of the addon folder: {e}") from e
    if (into / "addon.py").is_file():
        return into
    tops = list(into.iterdir())
    if len(tops) == 1 and tops[0].is_dir() and (tops[0] / "addon.py").is_file():
        return tops[0]
    raise InstallError("The archive has no addon.py at its root (or in its one top folder).")


def install_archive(archive: Path, root: Path, *, by: str = "", accept: Optional[Touches] = None) -> Installed:
    """Install the addon folder packed in the ``.tar.gz`` ``archive`` into ``root/<its name>``
    (replacing an earlier upload of it there); ``by`` and ``accept`` as for :func:`install`.

    Raises:
        ConfirmTouches: it may touch more than agreed (nothing was written).
        InstallError: refused (see the module docstring and :func:`unpack`).
    """
    root = root.resolve()
    with _lock, tempfile.TemporaryDirectory(prefix="dana-upload-") as tmp:
        folder = unpack(archive, Path(tmp))
        return _place(folder, root, UPLOAD, None, name=None, by=by, accept=accept)


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
