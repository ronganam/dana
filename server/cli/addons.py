"""``bin/dana addons …``: the server's addons (../docs/addons.md).

    addons list                             every addon: source, version, state, fields; the failed ones
    addons table                            the official addons as a Markdown table: what each does, what it
                                            needs (required / optional) and where it works (the README's)
    addons new NAME [--web] [--worker] [--title T] [--dir DIR]
                                            write a working addon from the template (default: site/addons/NAME),
                                            its own git repository: tools, a Now card, tests with the fakes they
                                            need, AGENTS.md, REFERENCE.md; --web adds a page, --worker a sidecar
    addons check PATH [--json]              what is wrong with an addon, each problem with its fix (cli/kit/check.py)
    addons test PATH [-v]                   run an addon's tests against Dana's harness
    addons dev PATH [--once]                run it in the local test environment, reloaded on save
    addons reference [--check]              write docs/addon-reference.md from the code (--check: only compare)
    addons publish PATH [--repo R] [--catalog-repo R] [--owner O] [--dry-run]
                                            check, test, push to GitHub, release, open the catalog pull request
    addons install NAME|REPO@COMMIT[:PATH] [--dir DIR | --data] [--yes]
                                            fetch one commit of an addon (a catalog entry, or any git repository)
                                            into the site's addons/ (default), DIR, or the data dir's addons/;
                                            asks first when it may touch more than the version it replaces
                                            (--yes: agree without asking)
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
from typing import Any, TextIO

from core import addon_dirs
from core.addons import catalog, install, loader, state
from core.addons.addon import Addon, all_addons, get_addon
from core.addons.hooks import ADDON, CORE_SCREENS, ENV, REGION, Need, Touches
from cli.kit import check as check_kit
from cli.kit import dev as dev_kit
from cli.kit import publish as publish_kit
from cli.kit import reference, runner
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
                 f"needs {', '.join(k.depends)}" if k.depends else "",
                 "has a worker" if k.worker is not None else ""]
        print(f"{k.name}: {k.description} ({', '.join(f for f in flags if f)})", file=out)
        for f in k.credentials:
            print(f"  credential {f.name}{'' if f.required else ' (optional)'}: {f.description}", file=out)
        for f in k.settings:
            default = f" (default {json.dumps(f.default, ensure_ascii=False)})" if f.default is not None else ""
            print(f"  setting {f.name}{default}: {f.description}", file=out)
    for failed in loader.failed().values():
        print(f"{failed.name}: FAILED to load ({failed.source}, {failed.path}): {failed.error}", file=out)


# --- table ---------------------------------------------------------------------------------------

TABLE_HEAD = ("| Feature | What it does | Required | Optional | Region |\n"
              "|---|---|---|---|---|")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def need_words(need: Need) -> str:
    """A need as a table shows it: an env value in code, an addon by its title, the rest as written."""
    if need.kind == ENV:
        return f"`{need.what}`"
    if need.kind == ADDON:
        try:
            return f"the {get_addon(need.what).title} addon"
        except KeyError:
            return f"the {need.what} addon"
    return need.what


def addons_table(addons: list[Addon]) -> str:
    """``addons`` as a Markdown table: Feature · What it does · Required · Optional · Region."""
    rows = [TABLE_HEAD]
    for a in addons:
        required = "; ".join(need_words(n) for n in a.needs if not n.optional and n.kind != REGION)
        optional = "; ".join(need_words(n) for n in a.needs if n.optional and n.kind != REGION)
        region = ", ".join(n.what for n in a.needs if n.kind == REGION)
        rows.append(f"| {_cell(a.title)} | {_cell(a.description)} | {_cell(required) or '—'} | "
                    f"{_cell(optional) or '—'} | {_cell(region) or 'Any'} |")
    return "\n".join(rows) + "\n"


def official_table() -> str:
    """The table of the official addons (the README's "What each feature needs")."""
    return addons_table([a for a in all_addons() if loader.folder(a.name).source.official])


def cmd_table(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    out.write(official_table())


# --- new -----------------------------------------------------------------------------------------

# Template files only some addons get: what decides it (the first part of the file's path).
OPTIONAL_FILES = {"api.py.tmpl": "web", "web": "web", "worker": "worker"}
# Template files written under another name.
RENAMED = {"gitignore": ".gitignore"}
BLOCK = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)


def scaffold(name: str, root: Path, title: str = "", web: bool = False, worker: bool = False) -> Path:
    """Write a new addon ``root/<name>`` from the template (cli/template/) as its own git
    repository: a manifest with needs and touches, tools (a setting, an outside service, the
    model), a Now card and a day item, tests with the users and fakes they need
    (``tests/dana_harness.py``, ``tests/fakes.py``), AGENTS.md, README.md and REFERENCE.md
    (cli/kit/reference.py); ``web``: a page and the route it reads; ``worker``: a sidecar and
    a tool that calls it. Commits it when git knows who you are. Returns its folder.

    Raises:
        CliError: a bad name, or the folder exists.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in install.RESERVED or name in CORE_SCREENS:
        raise CliError(f"{name!r} is not a usable addon name: lower-case letters, digits and _, starting with a letter.")
    if (addon_dirs.OFFICIAL_DIR / name).is_dir():
        raise CliError(f"{name!r} is an official addon's name; pick another.")
    target = root / name
    if target.exists():
        raise CliError(f"{target} already exists.")
    flags = {"web": web, "worker": worker}
    words = {"{{name}}": name, "{{NAME}}": name.upper(), "{{title}}": title or name.replace("_", " ").capitalize(),
             "{{page}}": name.replace("_", "-"), "{{env}}": f"{name.upper()}_WORKER_URL"}
    for src in sorted(TEMPLATE.rglob("*.tmpl")):
        rel = src.relative_to(TEMPLATE)
        if not flags.get(OPTIONAL_FILES.get(rel.parts[0], ""), True):
            continue
        text = BLOCK.sub(lambda m: m.group(2) if flags[m.group(1)] else "", src.read_text(encoding="utf-8"))
        for key, value in words.items():
            text = text.replace(key, value)
        out = str(rel.with_suffix("")).replace("NAME", name)
        dest = target / RENAMED.get(out, out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    (target / "REFERENCE.md").write_text(reference.render(), encoding="utf-8")
    try:
        publish_kit.git(["init", "-q", "-b", "main"], target)
        publish_kit.git(["add", "-A"], target)
        publish_kit.git(["commit", "-q", "-m", f"Start the {name} addon (bin/dana addons new)"], target)
    except publish_kit.PublishError:
        pass  # no git, or git does not know who you are yet: the files are there to commit by hand
    return target


def cmd_new(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    root = Path(args.dir).expanduser().resolve() if args.dir else _site_addons()
    target = scaffold(args.name, root, args.title or "", web=args.web, worker=args.worker)
    files = sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file() and ".git" not in p.parts)
    try:
        committed = bool(publish_kit.git(["rev-parse", "HEAD"], target))
    except publish_kit.PublishError:
        committed = False
    print(f"Wrote the {args.name} addon in {target}: {', '.join(files)}"
          f"{' (a git repository, first commit made)' if committed else ''}.\n"
          f"Next (AGENTS.md has the loop): bin/dana addons check {target}, bin/dana addons test {target}, "
          f"bin/dana addons dev {target}.", file=out)


# --- check / test / dev / reference / publish ---------------------------------------------------

def cmd_check(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    report = check_kit.check(Path(args.path))
    out.write(json.dumps(report.to_json(), indent=1, ensure_ascii=False) + "\n" if args.json else report.text())
    return 0 if report.ok else 1


def cmd_test(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    return 1 if runner.run(Path(args.path), out, verbose=args.verbose) else 0


def cmd_dev(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    return dev_kit.dev(Path(args.path), out, once=args.once)


def cmd_reference(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    target = Path(args.out).expanduser().resolve() if args.out else reference.OUTPUT
    if args.check:
        same = target.is_file() and target.read_text(encoding="utf-8") == reference.render()
        print(f"{target} is {'up to date' if same else 'out of date: run bin/dana addons reference'}.", file=out)
        return 0 if same else 1
    print(f"Wrote {reference.write(target)}.", file=out)
    return 0


def cmd_publish(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    try:
        publish_kit.publish(Path(args.path), out, catalog_repo=args.catalog_repo, repo=args.repo or "",
                            owner=args.owner or "", dry_run=args.dry_run)
    except publish_kit.PublishError as e:
        raise CliError(str(e)) from e


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


def touches_lines(touches: Touches) -> list[str]:
    """What ``touches`` declares, a line each in plain words (none: one line saying so)."""
    lines = [f"connects to {', '.join(touches.hosts)}"] if touches.hosts else []
    if touches.acts_outside:
        lines.append("acts for you outside Dana (sends messages or mail, changes things at a service)")
    if touches.stores_credentials:
        lines.append("keeps passwords, tokens or sign-ins (encrypted)")
    if touches.reads:
        lines.append(f"reads the data of {', '.join(touches.reads)}")
    return lines or ["touches nothing beyond Dana's own data"]


def cmd_install(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> None:
    source, name = parse_spec(args.spec)
    root = _root(args)
    print(f"Installing from {source.repo} at {source.ref}{' (' + source.path + ')' if source.path else ''} "
          f"into {root}. An addon runs with the server's full access: install only code you trust.", file=out)
    try:
        try:
            done = install.install(source, root, name=name, by="cli")
        except install.ConfirmTouches as e:
            print(f"{e.name} may touch:", *(f"  - {line}" for line in touches_lines(e.touches)), sep="\n", file=out)
            print("New since the version you have (or you agreed to):",
                  *(f"  - {line}" for line in touches_lines(e.added)), sep="\n", file=out)
            if not args.yes:
                print("Install it? [y/N] ", end="", file=out, flush=True)
                if stdin.readline().strip().lower() not in ("y", "yes"):
                    raise CliError("Not installed.") from None
            done = install.install(source, root, name=name, by="cli", accept=e.touches)
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
    p = sub.add_parser("table", help="the official addons as a Markdown table: what each needs and where it works")
    p.set_defaults(func=cmd_table, needs_db=False)
    p = sub.add_parser("new", help="write a working addon (a setting, a tool, its test, a README) to start from")
    p.add_argument("name")
    p.add_argument("--title", help="its name for people (default: from the name)")
    p.add_argument("--web", action="store_true", help="also a page in the web app and the route it reads")
    p.add_argument("--worker", action="store_true", help="also a sidecar (worker/: a Dockerfile) and a tool calling it")
    p.add_argument("--dir", help="the addons folder to write into (default: the site's addons/)")
    p.set_defaults(func=cmd_new, needs_db=False)
    p = sub.add_parser("check", help="what is wrong with an addon (manifest, imports, migrations, tool docstrings, "
                       "declared vs used), each problem with its fix")
    p.add_argument("path", help="the addon's folder")
    p.add_argument("--json", action="store_true", help="the report as JSON")
    p.set_defaults(func=cmd_check, needs_db=False)
    p = sub.add_parser("test", help="run an addon's tests (tests/test_*.py, .mjs) against Dana's harness")
    p.add_argument("path", help="the addon's folder")
    p.add_argument("-v", "--verbose", action="store_true", help="print every test's output")
    p.set_defaults(func=cmd_test, needs_db=False)
    p = sub.add_parser("dev", help="run an addon in the local test environment: its log, tools and Now cards, "
                       "reloaded on save")
    p.add_argument("path", help="the addon's folder")
    p.add_argument("--once", action="store_true", help="start, show and stop (no watching)")
    p.set_defaults(func=cmd_dev, needs_db=False)
    p = sub.add_parser("reference", help="write docs/addon-reference.md from the code")
    p.add_argument("--out", help="write it here instead")
    p.add_argument("--check", action="store_true", help="only say whether the file is up to date (exit 1 if not)")
    p.set_defaults(func=cmd_reference, needs_db=False)
    p = sub.add_parser("publish", help="check, test, push the addon to GitHub, release it and open the catalog "
                       "pull request")
    p.add_argument("path", help="the addon's folder (in its own git repository)")
    p.add_argument("--repo", help="its GitHub repository OWNER/NAME (default: <you>/dana-addon-<name>)")
    p.add_argument("--catalog-repo", default=publish_kit.CATALOG_REPO, help="the catalog repository OWNER/NAME "
                   f"(default {publish_kit.CATALOG_REPO})")
    p.add_argument("--owner", help="your GitHub login (default: asked from gh)")
    p.add_argument("--dry-run", action="store_true", help="print every gh / git command instead of running it")
    p.set_defaults(func=cmd_publish, needs_db=False)
    p = sub.add_parser("install", help="fetch one commit of an addon: a catalog NAME or REPO@COMMIT[:PATH]")
    p.add_argument("spec", metavar="NAME|REPO@COMMIT[:PATH]")
    p.add_argument("--yes", action="store_true", help="agree to what it may touch without asking")
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
