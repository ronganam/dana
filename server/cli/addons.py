"""``bin/dana addons …``: the server's addons (../docs/addons.md).

    addons list                             every addon: source, version, state, fields; the failed ones
    addons new NAME [--dir DIR]             write a working addon from the template (default: site/addons/NAME)
    addons install NAME|REPO@COMMIT[:PATH] [--dir DIR | --data]
                                            fetch one commit of an addon (a catalog entry, or any git repository)
                                            into the site's addons/ (default), DIR, or the data dir's addons/
    addons remove NAME [--dir DIR | --data] delete an addon folder there (its tables stay)
    addons disable NAME / enable NAME       for everyone on this server (the running server sees it within 5 s)

``install`` writes into the site's ``addons/`` by default: what ``bin/deploy``
builds into the image. ``--data`` writes into ``<data dir>/addons`` like the
Addons page does (then restart the server). Either way the addon runs with the
server's full access: install only code you trust. The per-user commands
(``show``, ``connect``, ``update``, ``disconnect``) are in cli/users.py.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, TextIO

from core import addon_dirs
from core.addons import catalog, install, loader, state
from core.addons.addon import all_addons, get_addon
from cli.users import CliError, register_connections

TEMPLATE = Path(__file__).resolve().parent / "template"
# REPO@COMMIT[:PATH] (the repo itself may hold '@' and ':': git@github.com:owner/repo.git@<commit>:path)
SPEC = re.compile(r"^(?P<repo>.+)@(?P<ref>[0-9a-fA-F]{40})(?::(?P<path>.*))?$")


def _site_addons() -> Path:
    return addon_dirs.site_dir() / "addons"


def _root(args: argparse.Namespace) -> Path:
    if getattr(args, "data", False):
        return addon_dirs.installed_dir()
    return Path(args.dir).expanduser().resolve() if getattr(args, "dir", None) else _site_addons()


# --- list ----------------------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    off = state.disabled()
    for k in all_addons():
        folder = loader.folder(k.name)
        where = folder.source.label
        commit = loader.loaded_commit(k.name)
        if commit:
            where += f" @ {commit[:12]}"
        flags = [f"v{k.version}" if k.version else "", where,
                 "disabled on this server" if k.name in off else "",
                 "every user has it" if k.everyone else "",
                 f"needs the '{k.requires}' capability: admins only" if k.requires else "",
                 f"needs {', '.join(k.needs)}" if k.needs else "",
                 "has a worker" if k.worker is not None else ""]
        print(f"{k.name}: {k.description} ({', '.join(f for f in flags if f)})", file=out)
        for f in k.credentials:
            print(f"  credential {f.name}{'' if f.required else ' (optional)'}: {f.description}", file=out)
        for f in k.settings:
            default = f" (default {json.dumps(f.default, ensure_ascii=False)})" if f.default is not None else ""
            print(f"  setting {f.name}{default}: {f.description}", file=out)
    for failed in loader.failed().values():
        print(f"{failed.name}: FAILED to load ({failed.source}, {failed.path}): {failed.error}", file=out)


# --- new -----------------------------------------------------------------------------------------

def scaffold(name: str, root: Path, title: str = "") -> Path:
    """Write a new addon ``root/<name>`` from the template (cli/template/); returns its folder.

    Raises:
        CliError: a bad name, or the folder exists.
    """
    if not name.isidentifier() or name != name.lower() or name in install.RESERVED:
        raise CliError(f"{name!r} is not a usable addon name: a lower-case identifier (letters, digits, _).")
    if (addon_dirs.OFFICIAL_DIR / name).is_dir():
        raise CliError(f"{name!r} is an official addon's name; pick another.")
    target = root / name
    if target.exists():
        raise CliError(f"{target} already exists.")
    words: Dict[str, str] = {"{{name}}": name, "{{title}}": title or name.replace("_", " ").capitalize()}
    for src in sorted(TEMPLATE.rglob("*.tmpl")):
        rel = src.relative_to(TEMPLATE).with_suffix("")
        dest = target / str(rel).replace("NAME", name)
        text = src.read_text(encoding="utf-8")
        for key, value in words.items():
            text = text.replace(key, value)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    return target


def cmd_new(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    root = Path(args.dir).expanduser().resolve() if args.dir else _site_addons()
    target = scaffold(args.name, root, args.title or "")
    print(f"Wrote the {args.name} addon in {target}: addon.py (a setting, a prompt line), tools.py "
          f"({args.name}_hello), api.py (GET /api/{args.name}), tests/test_{args.name}.py, README.md.\n"
          f"Test it: bin/test {args.name} (from server/). Restart Dana to load it, then turn it on in "
          "Settings → Addons.", file=out)


# --- install / remove ----------------------------------------------------------------------------

def parse_spec(spec: str) -> tuple[install.Source, str | None]:
    """``NAME`` (a catalog entry: its source and name) or ``REPO@COMMIT[:PATH]`` (no name).

    Raises:
        CliError: neither, or the catalog cannot be read / has no such entry.
    """
    m = SPEC.match(spec.strip())
    if m:
        return install.Source(m["repo"], m["path"] or "", m["ref"].lower()), None
    if not spec.isidentifier():
        raise CliError(f"{spec!r} is neither a catalog name nor REPO@COMMIT[:PATH] (a full 40-character commit).")
    try:
        entry = catalog.load().get(spec)
    except catalog.CatalogError as e:
        raise CliError(str(e)) from e
    if entry is None:
        raise CliError(f"The catalog ({catalog.origin()}) has no addon {spec!r}.")
    if not catalog.compatible(entry):
        raise CliError(f"{spec} needs Dana {entry.min_dana} or later; this is {catalog.dana_version()}.")
    return entry.source, entry.name


def cmd_install(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    source, name = parse_spec(args.spec)
    root = _root(args)
    print(f"Installing from {source.repo} at {source.ref}{' (' + source.path + ')' if source.path else ''} "
          f"into {root}. An addon runs with the server's full access: install only code you trust.", file=out)
    try:
        done = install.install(source, root, name=name, by="cli")
    except install.InstallError as e:
        raise CliError(str(e)) from e
    after = ("Restart the server to load it." if root == addon_dirs.installed_dir().resolve()
             else "bin/deploy builds it into the image; a local server loads it at its next start.")
    print(f"{'Updated' if done.replaced else 'Installed'} {done.name} in {done.folder}. {after}", file=out)


def cmd_remove(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    try:
        gone = install.remove(args.name, _root(args))
    except install.InstallError as e:
        raise CliError(str(e)) from e
    print(f"Removed {gone} (its tables and rows stay in the database). Restart the server (or bin/deploy) "
          "to unload it.", file=out)


# --- disable / enable ----------------------------------------------------------------------------

def _set(args: argparse.Namespace, out: TextIO, value: bool) -> None:
    try:
        get_addon(args.name)
    except KeyError as e:
        raise CliError(f"No loaded addon {args.name!r} (see: bin/dana addons list).") from e
    try:
        state.set_disabled(args.name, value)
    except state.CannotDisable as e:
        raise CliError(str(e)) from e
    also = sorted(state.disabled() - state.disabled_by_admin()) if value else []
    print(f"{'Disabled' if value else 'Enabled'} {args.name} for everyone on this server"
          + (f" (also off, as they need it: {', '.join(also)})" if also else "")
          + ". The running server sees it within a few seconds.", file=out)


def cmd_disable(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    _set(args, out, True)


def cmd_enable(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    _set(args, out, False)


# --- Parser --------------------------------------------------------------------------------------

def _where(p: argparse.ArgumentParser) -> None:
    group = p.add_mutually_exclusive_group()
    group.add_argument("--dir", help="the addons folder to use (default: the site's addons/)")
    group.add_argument("--data", action="store_true", help="the data dir's addons/ (what the Addons page installs into)")


def register(sub: Any) -> None:
    """Add the ``addons`` commands to ``sub`` (the ``bin/dana addons`` sub-parsers)."""
    p = sub.add_parser("list", help="every addon with its source, state and fields; the ones that failed")
    p.set_defaults(func=cmd_list, needs_db=True)
    p = sub.add_parser("new", help="write a working addon (a setting, a tool, a route, a test) to start from")
    p.add_argument("name")
    p.add_argument("--title", help="its name for people (default: from the name)")
    p.add_argument("--dir", help="the addons folder to write into (default: the site's addons/)")
    p.set_defaults(func=cmd_new, needs_db=False)
    p = sub.add_parser("install", help="fetch one commit of an addon: a catalog NAME or REPO@COMMIT[:PATH]")
    p.add_argument("spec", metavar="NAME|REPO@COMMIT[:PATH]")
    _where(p)
    p.set_defaults(func=cmd_install, needs_db=False)
    p = sub.add_parser("remove", help="delete an addon folder (its tables stay)")
    p.add_argument("name")
    _where(p)
    p.set_defaults(func=cmd_remove, needs_db=False)
    for name, func, text in (("disable", cmd_disable, "disable an addon for everyone on this server"),
                             ("enable", cmd_enable, "enable a disabled addon again")):
        p = sub.add_parser(name, help=text)
        p.add_argument("name")
        p.set_defaults(func=func, needs_db=True)
    register_connections(sub)
