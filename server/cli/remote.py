"""Installing an addon on a running Dana through its API, and packing an addon
folder for an upload (``bin/dana addons install … --server URL``; cli/addons.py).

:func:`pack_folder` writes a ``.tar.gz`` of an addon folder with its files at
the archive's root, leaving out what never belongs in an install: ``.git/``,
``__pycache__/``, ``.venv/``, ``node_modules/``, ``*.pyc``, the install record,
and what the folder's own ``.gitignore`` names (plain patterns: ``name``,
``dir/``, ``/anchored``, globs; ``!`` lines are not supported and skipped). A
symbolic link is refused (the server refuses them too).

:class:`Server` talks to ``POST /api/addons/install`` (JSON for a catalog entry
or a git source, multipart for an uploaded folder) and ``POST
/api/server/restart`` with an admin's Bearer token, standard library only.
"""

from __future__ import annotations

import fnmatch
import json
import tarfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from core import addon_dirs

# Never packed, whatever .gitignore says.
ALWAYS_LEFT_OUT_DIRS = frozenset({".git", "__pycache__", ".venv", "node_modules"})
ALWAYS_LEFT_OUT_FILES = ("*.pyc", addon_dirs.INSTALLED_FILE)
TIMEOUT_SECONDS = 120.0


class RemoteError(RuntimeError):
    """The server could not be reached, or answered with an error; the message says why."""


class PackError(ValueError):
    """The folder cannot be packed (a symbolic link, no addon.py)."""


def _gitignore(folder: Path) -> List[str]:
    try:
        lines = (folder / ".gitignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(("#", "!"))]


def _ignored(rel: PurePosixPath, is_dir: bool, patterns: List[str]) -> bool:
    """Whether ``rel`` (a path inside the addon folder) matches one of the ``.gitignore`` patterns."""
    for raw in patterns:
        pattern = raw
        dir_only = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if dir_only and not is_dir:
            continue
        if pattern.startswith("/") or "/" in pattern:
            if fnmatch.fnmatchcase(str(rel), pattern.lstrip("/")):
                return True
        elif fnmatch.fnmatchcase(rel.name, pattern):
            return True
    return False


def packed_files(folder: Path) -> List[Path]:
    """The files of the addon ``folder`` an upload carries (sorted, relative to it).

    Raises:
        PackError: no addon.py, or a symbolic link among the files packed.
    """
    folder = folder.resolve()
    if not (folder / "addon.py").is_file():
        raise PackError(f"{folder} has no addon.py: not an addon folder.")
    patterns = _gitignore(folder)
    out: List[Path] = []

    def walk(d: Path) -> None:
        for p in sorted(d.iterdir(), key=lambda p: p.name):
            rel = PurePosixPath(p.relative_to(folder).as_posix())
            if p.is_symlink():
                if p.is_dir() and p.name in ALWAYS_LEFT_OUT_DIRS:
                    continue
                if not _ignored(rel, p.is_dir(), patterns):
                    raise PackError(f"{rel} is a symbolic link; the server does not install links.")
                continue
            if p.is_dir():
                if p.name in ALWAYS_LEFT_OUT_DIRS or _ignored(rel, True, patterns):
                    continue
                walk(p)
            elif p.is_file():
                if any(fnmatch.fnmatchcase(p.name, pat) for pat in ALWAYS_LEFT_OUT_FILES) \
                        or _ignored(rel, False, patterns):
                    continue
                out.append(Path(rel))

    walk(folder)
    return out


def pack_folder(folder: Path, archive: Path) -> int:
    """Write the addon ``folder`` as the ``.tar.gz`` ``archive`` (files at its root); returns how many files.

    Raises:
        PackError: see :func:`packed_files`.
    """
    folder = folder.resolve()
    files = packed_files(folder)
    with tarfile.open(archive, "w:gz") as tar:
        for rel in files:
            info = tar.gettarinfo(str(folder / rel), arcname=rel.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with (folder / rel).open("rb") as fh:
                tar.addfile(info, fh)
    return len(files)


@dataclass(frozen=True)
class Answer:
    """A server's answer: the HTTP status and its JSON body."""
    status: int
    body: Dict[str, Any]


class Server:
    """A running Dana at ``url``, called with an admin's ``token``."""

    def __init__(self, url: str, token: str) -> None:
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise RemoteError(f"The server address {url!r} must start with http:// or https://.")
        self.url = url
        self.token = token.strip()

    def _call(self, path: str, data: Optional[bytes], content_type: str) -> Answer:
        req = urllib.request.Request(self.url + path, data=data, method="POST",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": content_type, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise RemoteError(f"Could not reach {self.url}: {getattr(e, 'reason', e)}") from e
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if status in (401, 403):
            raise RemoteError(f"{self.url} refused the token ({status}): give an admin's token (DANA_TOKEN).")
        return Answer(status, body)

    def install_json(self, body: Dict[str, Any]) -> Answer:
        """``POST /api/addons/install`` with a JSON body (``{name}`` or ``{repo, ref, path}``, + ``accept``)."""
        return self._call("/api/addons/install", json.dumps(body).encode("utf-8"), "application/json")

    def install_archive(self, archive: Path, accept: Optional[Dict[str, Any]] = None) -> Answer:
        """``POST /api/addons/install`` as multipart: ``archive`` (+ ``accept`` as JSON)."""
        boundary = f"dana-{uuid.uuid4().hex}"
        parts: List[Tuple[str, str, bytes, str]] = [("archive", "addon.tar.gz", archive.read_bytes(),
                                                     "application/gzip")]
        if accept is not None:
            parts.append(("accept", "", json.dumps(accept).encode("utf-8"), "application/json"))
        chunks: List[bytes] = []
        for name, filename, data, kind in parts:
            disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
            chunks += [f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {kind}\r\n\r\n"
                       .encode("utf-8"), data, b"\r\n"]
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return self._call("/api/addons/install", b"".join(chunks), f"multipart/form-data; boundary={boundary}")

    def restart(self) -> Answer:
        """``POST /api/server/restart``."""
        return self._call("/api/server/restart", b"", "application/json")
