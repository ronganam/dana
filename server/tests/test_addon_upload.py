"""Installing an addon from a folder (core/addons/install.py install_archive, POST /api/addons/install as multipart, cli/remote.py): the archive checks, the touches agreement, replacing an upload, never over a git install (nor the other way), the CLI's packing excludes, a version older than the database refused, `bin/dana addons install ./FOLDER` on this machine and on a running server (--server, DANA_URL, DANA_TOKEN, --restart), and `bin/dana addons workers --json`.

Run: bin/test test_addon_upload

The addons go into the data dir's addons/ (the installed source); a development
folder (``DANA_ADDON_DIRS``) holds ``devone``, an addon of another source. A
local git repository holds ``gitone`` and ``upone``. Robin (the first admin,
the env token) installs; Maya (a member) is refused. No network: the CLI talks
to a test server on 127.0.0.1.
"""

import asyncio
import io
import json
import os
import subprocess
import tarfile
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("addon_upload_test")
os.environ["API_TOKEN"] = "upload-env-token"  # a test value: the first admin's token (a headless install)
SCRATCH = Path(testenv.scratch_dir())
DATA_ADDONS = Path(DB_PATH).parent / "addons"   # core.paths.ADDONS_DIR for this database
DEV = SCRATCH / "dev_addons"
REPO = SCRATCH / "repo"
FOLDERS = SCRATCH / "folders"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def manifest(name: str, touches: str = "", extra: str = "") -> str:
    return (f"from core.addons import Addon, Touches, Worker\naddon = Addon({name!r}, description='An upload test'"
            f"{', touches=' + touches if touches else ''}{extra})\n")


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


HOST = 'Touches(hosts=("api.tides.example",))'
MORE = 'Touches(hosts=("api.tides.example",), acts_outside=True)'

# The tides folder: what an author has on disk, with what never belongs in an install.
TIDES = FOLDERS / "tides"
write(TIDES / "addon.py", manifest("tides", HOST))
write(TIDES / "tools.py", "# tools\n")
write(TIDES / "tests" / "test_tides.py", "# a test\n")
write(TIDES / "worker" / "Dockerfile", "FROM scratch\n")
write(TIDES / ".gitignore", "# local files\nbuild/\n*.local\n/top.txt\n!keep.local\n")
write(TIDES / ".git" / "HEAD", "ref: refs/heads/main\n")
write(TIDES / "__pycache__" / "tools.cpython-314.pyc", "x")
write(TIDES / "stray.pyc", "x")
write(TIDES / ".venv" / "lib" / "x.py", "x")
os.symlink("/usr/bin/python3", TIDES / ".venv" / "python")  # links inside a left-out folder do not matter
write(TIDES / "node_modules" / "x.js", "x")
write(TIDES / "build" / "out.js", "x")
write(TIDES / "notes.local", "x")
write(TIDES / "top.txt", "x")
write(TIDES / "docs" / "top.txt", "kept: /top.txt is anchored\n")
write(TIDES / ".installed.json", "{}")

write(DEV / "devone" / "addon.py", manifest("devone"))
write(FOLDERS / "devone" / "addon.py", manifest("devone"))
write(FOLDERS / "wk" / "addon.py", manifest("wk", extra=", worker=Worker(port=8099, url_env='WK_URL', "
                                                      "image='ghcr.io/example/wk-worker:1.2')"))
write(FOLDERS / "linked" / "addon.py", manifest("linked"))
os.symlink("/etc/hostname", FOLDERS / "linked" / "host.txt")

REPO.mkdir()
git("init", "-q")
write(REPO / "gitone" / "addon.py", manifest("gitone", extra=", worker=Worker(port=8098, url_env='G_URL', "
                                                             "image='ghcr.io/example/gitone-worker')"))
write(REPO / "upone" / "addon.py", manifest("upone"))
write(REPO / "gitwo" / "addon.py", manifest("gitwo"))
git("add", "-A")
git("commit", "-qm", "one")
COMMIT = git("rev-parse", "HEAD")
write(FOLDERS / "gitone" / "addon.py", manifest("gitone"))
write(FOLDERS / "upone" / "addon.py", manifest("upone"))

os.environ["DANA_ADDON_DIRS"] = f"{DEV}{os.pathsep}{DATA_ADDONS}"

from aiohttp import FormData, web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, lifecycle, server, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.addons import install, loader  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from cli import main as cli  # noqa: E402
from cli import addons as addons_cli  # noqa: E402
from cli import remote  # noqa: E402

loader.load()
results: List[Tuple[str, bool]] = []


def check(name: str, cond: Any, detail: Any = "") -> None:
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"\n       {str(detail)[:600]}" if detail and not cond else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
MAYA = users.create("Maya")
ADMIN_AUTH = {"Authorization": "Bearer upload-env-token"}
MAYA_TOKEN = users.issue_token(MAYA.id, "t")[0]
MAYA_AUTH = {"Authorization": f"Bearer {MAYA_TOKEN}"}


def pack(folder: Path, name: str) -> Path:
    archive = SCRATCH / f"{name}.tar.gz"
    remote.pack_folder(folder, archive)
    return archive


def raw_archive(name: str, members: List[Tuple[str, Optional[bytes], str]]) -> Path:
    """A hand-made .tar.gz: (path, data, type) with type file / dir / symlink."""
    archive = SCRATCH / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path, data, kind in members:
            info = tarfile.TarInfo(path)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/hostname"
                tar.addfile(info)
            else:
                info.size = len(data or b"")
                tar.addfile(info, io.BytesIO(data or b""))
    return archive


async def upload(c: TestClient, archive: Optional[Path], auth: Dict[str, str],
                 accept: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    form = FormData()
    if archive is not None:
        form.add_field("archive", archive.read_bytes(), filename="addon.tar.gz", content_type="application/gzip")
    else:
        form.add_field("note", "no archive here", content_type="text/plain")
    if accept is not None:
        form.add_field("accept", json.dumps(accept), content_type="application/json")
    r = await c.post("/api/addons/install", data=form, headers=auth)
    try:
        return r.status, await r.json()
    except Exception:
        return r.status, {"text": await r.text()}


async def call(c: TestClient, method: str, path: str, auth: Dict[str, str], body: Any = None) -> Tuple[int, Any]:
    r = await c.request(method, path, headers=auth, json=body)
    return r.status, await r.json()


def record(name: str) -> Dict[str, Any]:
    return json.loads((DATA_ADDONS / name / ".installed.json").read_text())


# --- 1. Packing ------------------------------------------------------------------------------------

def test_pack() -> None:
    files = sorted(str(p) for p in remote.packed_files(TIDES))
    check("pack: .git, __pycache__, .venv, node_modules, *.pyc, the install record and the .gitignore patterns "
          "are left out; the rest is kept",
          files == [".gitignore", "addon.py", "docs/top.txt", "tests/test_tides.py", "tools.py", "worker/Dockerfile"],
          files)
    archive = pack(TIDES, "tides_check")
    with tarfile.open(archive, "r:gz") as tar:
        names = sorted(m.name for m in tar.getmembers())
    check("pack: the files at the archive's root, relative paths only", names == files, names)
    try:
        remote.packed_files(FOLDERS / "linked")
        check("pack: a symbolic link is refused", False)
    except remote.PackError as e:
        check("pack: a symbolic link is refused", "host.txt" in str(e), str(e))
    try:
        remote.packed_files(TIDES / "tests")
        check("pack: a folder without addon.py is refused", False)
    except remote.PackError as e:
        check("pack: a folder without addon.py is refused", "no addon.py" in str(e), str(e))
    check("spec: ./tides and /abs paths are folders; a name or REPO@COMMIT are not",
          addons_cli.is_folder_spec("./tides") and addons_cli.is_folder_spec(str(TIDES))
          and not addons_cli.is_folder_spec("tides") and not addons_cli.is_folder_spec(f"{REPO}@{COMMIT}:gitone")
          and not addons_cli.is_folder_spec(f"{REPO}@main:gitone"))


# --- 2. The API ------------------------------------------------------------------------------------

async def test_api(c: TestClient) -> None:
    tides = pack(TIDES, "tides")
    status, body = await upload(c, tides, MAYA_AUTH)
    check("upload: admins only", status == 403 and not (DATA_ADDONS / "tides").exists(), body)

    status, body = await upload(c, tides, ADMIN_AUTH)
    check("upload: an addon that touches something is not installed until the admin agrees (409)",
          status == 409 and body.get("error") == "confirm_touches" and body.get("name") == "tides"
          and body["touches"]["hosts"] == ["api.tides.example"] and body["added"]["hosts"] == ["api.tides.example"]
          and not (DATA_ADDONS / "tides").exists(), body)
    status, body = await upload(c, tides, ADMIN_AUTH, accept=body["touches"])
    rec = record("tides") if status == 201 else {}
    check("upload: agreed → installed into the data dir, a restart needed",
          status == 201 and body["name"] == "tides" and body["kind"] == "upload" and body["replaced"] is False
          and body["restart_needed"] is True and body["touches"]["hosts"] == ["api.tides.example"]
          and (DATA_ADDONS / "tides" / "tools.py").is_file() and (DATA_ADDONS / "tides" / "worker" / "Dockerfile").is_file()
          and not (DATA_ADDONS / "tides" / ".git").exists() and not (DATA_ADDONS / "tides" / "build").exists(), body)
    check("upload: the record says upload, who and when, no commit",
          rec.get("source") == {"kind": "upload"} and rec.get("installed_by") == ADMIN.id
          and isinstance(rec.get("installed_at"), str) and "commit" not in rec and "repo" not in rec, rec)
    status, listing = await call(c, "GET", "/api/addons", ADMIN_AUTH)
    check("upload: listed as a change a restart loads", {"name": "tides", "change": "install"} in listing["pending"],
          listing.get("pending"))

    await asyncio.sleep(1.1)  # installed_at has a second's resolution: the replacement is a later one
    status, body = await upload(c, tides, ADMIN_AUTH)
    check("upload again, the same touches: replaces the earlier upload without asking",
          status == 201 and body["replaced"] is True and record("tides")["installed_at"] != rec.get("installed_at"), body)
    write(TIDES / "addon.py", manifest("tides", MORE))
    grown = pack(TIDES, "tides_more")
    status, body = await upload(c, grown, ADMIN_AUTH)
    check("upload with grown touches: asks again, with only what is new",
          status == 409 and body.get("added") == {"hosts": [], "acts_outside": True, "stores_credentials": False,
                                                  "reads": []}
          and "acts_outside=True" not in (DATA_ADDONS / "tides" / "addon.py").read_text(), body)
    status, body = await upload(c, grown, ADMIN_AUTH, accept=body.get("touches"))
    check("upload with grown touches, agreed: replaced",
          status == 201 and "acts_outside=True" in (DATA_ADDONS / "tides" / "addon.py").read_text(), body)

    refusals = [
        ("absolute", raw_archive("absolute", [("/etc/evil.py", b"x", "file"), ("addon.py", manifest("abs").encode(), "file")]),
         "absolute path or leaves"),
        ("dotdot", raw_archive("dotdot", [("addon.py", manifest("dd").encode(), "file"), ("../evil.py", b"x", "file")]),
         "absolute path or leaves"),
        ("symlink", raw_archive("symlink", [("addon.py", manifest("sl").encode(), "file"), ("host.txt", None, "symlink")]),
         "is a link"),
        ("noaddon", raw_archive("noaddon", [("tools.py", b"x", "file")]), "no addon.py"),
        ("two tops", raw_archive("twotops", [("a/addon.py", manifest("aa").encode(), "file"),
                                             ("b/addon.py", manifest("bb").encode(), "file")]), "no addon.py"),
        ("official", raw_archive("official", [("addon.py", manifest("tasks").encode(), "file")]), "official addon"),
        ("reserved", raw_archive("reserved", [("addon.py", manifest("catalog").encode(), "file")]), "reserved"),
        ("bad manifest", raw_archive("badmanifest", [("addon.py", b"addon = 1\n", "file")]), "must declare"),
        ("vague touches", raw_archive("vague", [("addon.py", (
            "H = ('x',)\n" + manifest("vague", "Touches(hosts=H)")).encode(), "file")]), "plain values"),
        ("another source", pack(FOLDERS / "devone", "devone"), "already in"),
    ]
    for label, archive, words in refusals:
        status, body = await upload(c, archive, ADMIN_AUTH)
        check(f"upload refused: {label}", status == 400 and body.get("error") == "install_refused"
              and words in body.get("message", ""), body)
    check("upload refused: nothing written for any of them",
          sorted(p.name for p in DATA_ADDONS.iterdir()) == ["tides"], sorted(p.name for p in DATA_ADDONS.iterdir()))
    status, body = await upload(c, raw_archive("toptree", [("mine/addon.py", manifest("toptree").encode(), "file"),
                                                           ("mine/tools.py", b"# t\n", "file")]), ADMIN_AUTH)
    check("upload: the folder may be the archive's one top folder",
          status == 201 and (DATA_ADDONS / "toptree" / "tools.py").is_file(), body)

    not_gzip = SCRATCH / "plain.tar.gz"
    not_gzip.write_bytes(b"this is not a gzip file")
    status, body = await upload(c, not_gzip, ADMIN_AUTH)
    check("upload refused: not a .tar.gz", status == 400 and "readable .tar.gz" in body.get("message", ""), body)
    status, body = await upload(c, None, ADMIN_AUTH)
    check("upload refused: no archive part", status == 400 and "archive" in body.get("message", ""), body)

    many = raw_archive("many", [("addon.py", manifest("many").encode(), "file")]
                       + [(f"f{i}.txt", b"x", "file") for i in range(6)])
    big = raw_archive("big", [("addon.py", manifest("big").encode(), "file"), ("blob.bin", b"\0" * 5000, "file")])
    saved = (install.MAX_ARCHIVE_FILES, install.MAX_UNPACKED_BYTES, install.MAX_ARCHIVE_BYTES)
    install.MAX_ARCHIVE_FILES, install.MAX_UNPACKED_BYTES = 5, 4000
    try:
        status, body = await upload(c, many, ADMIN_AUTH)
        check("upload refused: more files than the cap", status == 400 and "more than 5 files" in body.get("message", ""),
              body)
        status, body = await upload(c, big, ADMIN_AUTH)
        check("upload refused: unpacks to more than the cap", status == 400 and "unpacks to more" in body.get("message", ""),
              body)
        install.MAX_ARCHIVE_BYTES = 100
        status, body = await upload(c, tides, ADMIN_AUTH)
        check("upload refused: an archive over the size cap → 413 too_large",
              status == 413 and body.get("error") == "too_large", body)
    finally:
        install.MAX_ARCHIVE_FILES, install.MAX_UNPACKED_BYTES, install.MAX_ARCHIVE_BYTES = saved

    # Never over a git install, nor the other way round.
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "gitone", "ref": COMMIT})
    check("git install: recorded with its kind", status == 201 and body["kind"] == "git"
          and record("gitone")["source"] == {"kind": "git", "repo": str(REPO), "path": "gitone", "commit": COMMIT}, body)
    status, body = await upload(c, pack(FOLDERS / "gitone", "gitone"), ADMIN_AUTH)
    check("upload refused: it never replaces a git install of that name (and says so)",
          status == 400 and "an upload does not replace it" in body.get("message", "")
          and record("gitone")["source"]["kind"] == "git", body)
    status, body = await upload(c, pack(FOLDERS / "upone", "upone"), ADMIN_AUTH)
    check("upload: upone installed", status == 201, body)
    status, body = await call(c, "POST", "/api/addons/install", ADMIN_AUTH,
                              {"repo": str(REPO), "path": "upone", "ref": COMMIT})
    check("git install refused: it never replaces an uploaded install (and says so)",
          status == 400 and "a git install does not replace it" in body.get("message", "")
          and record("upone")["source"] == {"kind": "upload"}, body)

    status, listing = await call(c, "GET", "/api/addons", ADMIN_AUTH)
    check("list: a restart loads the uploads and the git install",
          all({"name": n, "change": "install"} in listing["pending"] for n in ("tides", "toptree", "gitone", "upone")),
          listing.get("pending"))
    status, body = await call(c, "POST", "/api/addons/upone/remove", ADMIN_AUTH)
    check("remove: an uploaded addon is removed like any installed one",
          status == 200 and not (DATA_ADDONS / "upone").exists(), body)


# --- 3. The CLI ------------------------------------------------------------------------------------

def run(*argv: str, stdin: str = "") -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    try:
        rc = cli.main(list(argv), stdout=out, stdin=io.StringIO(stdin), stderr=err)
    except SystemExit as e:
        rc = int(e.code or 0)
    return rc, out.getvalue(), err.getvalue()


async def test_cli_remote(url: str) -> None:
    folder = FOLDERS / "remote_one"
    write(folder / "addon.py", manifest("remote_one", HOST))
    write(folder / "__pycache__" / "x.pyc", "x")
    os.environ["DANA_TOKEN"] = "upload-env-token"
    try:
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(folder), "--server", url)
        check("cli --server: what it touches is shown and asked; no answer → not installed",
              rc == 1 and "remote_one may touch:" in out and "connects to api.tides.example" in out
              and "Not installed" in err and not (DATA_ADDONS / "remote_one").exists(), out + err)
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(folder), "--server", url, stdin="y\n")
        check("cli --server: answered yes → packed, uploaded, installed on the server; says to restart",
              rc == 0 and "Packed 1 file(s)" in out and record("remote_one")["source"] == {"kind": "upload"}
              and not (DATA_ADDONS / "remote_one" / "__pycache__").exists() and "Restart" in out, out + err)
        write(folder / "addon.py", manifest("remote_one", MORE))
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(folder), "--server", url, "--yes",
                                               "--restart")
        await asyncio.wait_for(lifecycle.wait_for_restart(), 3)
        check("cli --server --yes --restart: grown touches agreed, replaced, the server asked to restart",
              rc == 0 and "acts for you outside Dana" in out and "is restarting" in out
              and "acts_outside=True" in (DATA_ADDONS / "remote_one" / "addon.py").read_text()
              and lifecycle.restart_requested(), out + err)
        os.environ["DANA_URL"] = url
        rc, out, err = await asyncio.to_thread(run, "addons", "install", f"{REPO}@{COMMIT}:gitwo")
        check("cli: DANA_URL instead of --server; a REPO@COMMIT:PATH installed through the API",
              rc == 0 and record("gitwo")["source"]["kind"] == "git" and record("gitwo")["installed_by"] == ADMIN.id,
              out + err)
        os.environ["DANA_TOKEN"] = MAYA_TOKEN
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(FOLDERS / "wk"))
        check("cli --server: a member's token is refused, with what to give instead",
              rc == 1 and "admin's token" in err and not (DATA_ADDONS / "wk").exists(), err)
        os.environ.pop("DANA_TOKEN")
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(FOLDERS / "wk"), stdin="upload-env-token\n")
        check("cli --server: without DANA_TOKEN the token is asked for", rc == 0 and (DATA_ADDONS / "wk").is_dir(),
              out + err)
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(FOLDERS / "gitone"),
                                               stdin="upload-env-token\n")
        check("cli --server: the server's refusal is shown", rc == 1 and "an upload does not replace it" in err, err)
        rc, out, err = await asyncio.to_thread(run, "addons", "install", str(FOLDERS / "wk"), "--dir", str(SCRATCH))
        check("cli: --dir with a server is refused", rc == 1 and "--dir installs on this machine" in err, err)
    finally:
        os.environ.pop("DANA_URL", None)
        os.environ.pop("DANA_TOKEN", None)


def test_cli_local() -> None:
    target = SCRATCH / "local_addons"
    folder = FOLDERS / "localone"
    write(folder / "addon.py", manifest("localone", HOST))
    write(folder / "node_modules" / "x.js", "x")
    rc, out, err = run("addons", "install", str(folder), "--dir", str(target), "--yes")
    rec = json.loads((target / "localone" / ".installed.json").read_text()) if rc == 0 else {}
    check("cli without a server: a folder installed into --dir, recorded as an upload, packing excludes applied",
          rc == 0 and rec.get("source") == {"kind": "upload"} and rec.get("installed_by") == "cli"
          and not (target / "localone" / "node_modules").exists() and "Restart the server" in out, out[-300:] + err)
    rc, out, err = run("addons", "install", str(SCRATCH / "nothing_here"), "--dir", str(target))
    check("cli: a missing folder is refused", rc == 1 and "is not a folder" in err, err)
    rc, out, err = run("addons", "install", str(FOLDERS / "wk"), "--restart")
    check("cli: --restart without a server is refused", rc == 1 and "--restart" in err, err)
    rc, out, err = run("addons", "workers", "--json")
    found = json.loads(out) if rc == 0 else []
    check("cli workers --json: each installed addon's published worker image, pinned (a tag the manifest names, "
          "else the installed commit)",
          rc == 0 and {"name": "wk", "image": "ghcr.io/example/wk-worker", "tag": "1.2"} in found
          and {"name": "gitone", "image": "ghcr.io/example/gitone-worker", "tag": COMMIT} in found
          and all(w["name"] in ("wk", "gitone") for w in found), out + err)
    rc, out, err = run("addons", "workers")
    check("cli workers: one line each", rc == 0 and f"gitone: ghcr.io/example/gitone-worker:{COMMIT}" in out, out)


def test_older_than_database() -> None:
    """A version without a migration the database already applied for that addon is refused, nothing written."""
    folder = FOLDERS / "olderone"
    write(folder / "addon.py", manifest("olderone"))
    write(folder / "migrations" / "__init__.py", "")
    write(folder / "migrations" / "m0001_baseline.py", "VERSION = 1\nNAME = 'baseline'\ndef up(conn):\n    pass\n")
    conn = db.get_db_connection(DB_PATH)
    conn.executemany("INSERT INTO schema_migrations (addon, version, name, applied_at) VALUES ('olderone', ?, ?, '2026-01-01')",
                     [(1, "baseline"), (2, "newer")])
    conn.commit()
    conn.close()
    try:
        install.install_archive(pack(folder, "olderone"), DATA_ADDONS, by="test")
        check("older than the database: refused", False)
    except install.InstallError as e:
        check("older than the database: refused, naming the migration", "older" in str(e) and "2" in str(e), str(e))
    check("older than the database: nothing written", not (DATA_ADDONS / "olderone").exists())
    write(folder / "migrations" / "m0002_newer.py", "VERSION = 2\nNAME = 'newer'\ndef up(conn):\n    pass\n")
    install.install_archive(pack(folder, "olderone"), DATA_ADDONS, by="test")
    check("the same version as the database: installed", (DATA_ADDONS / "olderone" / "addon.py").is_file())


async def main() -> None:
    test_pack()
    app = web.Application()
    setup_auth(app)
    server.setup_routes(app)
    async with TestClient(TestServer(app, host="127.0.0.1")) as c:
        await test_api(c)
        await test_cli_remote(str(c.make_url("")).rstrip("/"))
    test_cli_local()
    test_older_than_database()


asyncio.run(main())
failed = [n for n, ok in results if not ok]
if failed:
    raise SystemExit(f"{len(failed)} check(s) failed")
print(f"\nAll {len(results)} upload checks passed")
