"""``bin/dana addons publish <path>``: put an addon on GitHub and in the catalog.

1. The addon's git repository must have nothing uncommitted (what is published is a
   commit), and ``bin/dana addons check`` and ``test`` must pass.
2. Its GitHub repository (``--repo``, default ``<you>/dana-addon-<name>``) is created
   with ``gh`` from the local one when it does not exist, else the current branch is pushed.
3. The commit is tagged ``v<version>`` (the manifest's version) and released.
4. A pull request to the catalog repository (``--catalog-repo``, default
   :data:`CATALOG_REPO`; through a fork unless you own it) adds the addon's entry to
   ``catalog.json``, or bumps it: pinned to the full commit, with what it touches.

``--dry-run`` prints every ``gh`` / ``git`` command and the catalog entry instead of
running them (the read-only ``git rev-parse`` / ``status`` still run). Nothing is
published without ``gh`` signed in (``gh auth login``).
"""

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TextIO

from core.addons import catalog
from core.addons.install import GIT_LOCATION_VARS
from cli.kit import check as check_kit
from cli.kit import runner

CATALOG_REPO = "ronganam/dana-catalog"
CATALOG_FILE = "catalog.json"


class PublishError(RuntimeError):
    """Publishing stopped; the message says why and what to do."""


def command_env() -> Dict[str, str]:
    """The environment for git / gh: without the variables that point git at another repository."""
    return {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS}


def git(args: Sequence[str], cwd: Path) -> str:
    """Run ``git`` (read-only uses and the scaffold); its output, stripped.

    Raises:
        PublishError: git failed.
    """
    done = subprocess.run(["git", *args], cwd=cwd, env=command_env(), capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip().splitlines()[-1:]}")
    return done.stdout.strip()


@dataclass
class Runner:
    """Runs the publishing commands, or prints them (``dry_run``)."""
    out: TextIO
    dry_run: bool

    def run(self, argv: Sequence[str], cwd: Optional[Path] = None, *, check: bool = True) -> subprocess.CompletedProcess:
        where = f"(in {cwd}) " if cwd else ""
        print(f"{'would run' if self.dry_run else 'run'}: {where}{shlex.join(argv)}", file=self.out)
        if self.dry_run:
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        done = subprocess.run(list(argv), cwd=cwd, env=command_env(), capture_output=True, text=True, timeout=300)
        if check and done.returncode != 0:
            raise PublishError(f"{shlex.join(argv)} failed: {(done.stderr or done.stdout).strip()}")
        return done


def catalog_entry(report: check_kit.Report, repo: str, owner: str, path_in_repo: str, commit: str,
                  existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The catalog entry for this version (``existing``: the entry it replaces, whose tags are kept)."""
    m = report.manifest
    return {"name": report.name, "title": m.get("title") or report.name, "description": m.get("description") or "",
            "icon": m.get("icon") or "", "author": owner, "homepage": f"https://github.com/{repo}",
            "tags": list((existing or {}).get("tags") or []), "min_dana": catalog.dana_version(),
            "touches": report.touches, "source": {"repo": f"https://github.com/{repo}", "path": path_in_repo,
                                                  "ref": commit}}


def with_entry(data: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """``data`` (a catalog) with ``entry`` added, or replacing the entry of that name; entries by name."""
    others = [e for e in data.get("addons", []) if not (isinstance(e, dict) and e.get("name") == entry["name"])]
    return {**data, "addons": sorted([*others, entry], key=lambda e: str(e.get("name", "")))}


def write_catalog(path: Path, data: Dict[str, Any]) -> None:
    """Write a catalog the way the catalog repository keeps it (one space indent, a final newline)."""
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def publish(path: Path, out: TextIO, *, catalog_repo: str = CATALOG_REPO, repo: str = "", owner: str = "",
            dry_run: bool = False) -> None:
    """Publish the addon at ``path`` (see the module docstring).

    Raises:
        PublishError: a check or test failed, the repository is not ready, or a command failed.
    """
    path = path.expanduser().resolve()
    try:
        top = Path(git(["rev-parse", "--show-toplevel"], path))
    except PublishError as e:
        raise PublishError(f"{path} is not in a git repository: git init, add and commit it first.") from e
    if git(["status", "--porcelain"], top):
        raise PublishError(f"{top} has uncommitted changes: commit them first (a release is a commit).")
    report = check_kit.check(path)
    out.write(report.text())
    if not report.ok:
        raise PublishError("bin/dana addons check found errors: fix them first.")
    if runner.run(path, out):
        raise PublishError("Its tests failed: fix them first.")
    if not report.version:
        raise PublishError("The manifest has no version: add version=\"0.1.0\" (releases are tagged with it).")
    commit = git(["rev-parse", "HEAD"], top)
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], top)
    path_in_repo = "" if path == top.resolve() else str(path.relative_to(top.resolve()))
    tag = f"v{report.version}"
    sh = Runner(out, dry_run)

    if not owner:
        found = sh.run(["gh", "api", "user", "--jq", ".login"])
        owner = found.stdout.strip() or "OWNER"
    repo = repo or f"{owner}/dana-addon-{report.name.replace('_', '-')}"
    title = report.manifest.get("title") or report.name
    print(f"Publishing {report.name} {report.version} ({commit}) as {repo}, then a pull request to {catalog_repo}.",
          file=out)

    # 1. The addon's repository.
    exists = sh.run(["gh", "repo", "view", repo, "--json", "name"], check=False).returncode == 0
    if dry_run:
        print(f"  (when {repo} does not exist yet, instead of the push below:)", file=out)
        sh.run(["gh", "repo", "create", repo, "--public", "--description", report.manifest.get("description") or title,
                "--source", str(top), "--remote", "origin", "--push"])
        sh.run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], top)
    elif exists:
        sh.run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], top)
    else:
        sh.run(["gh", "repo", "create", repo, "--public", "--description", report.manifest.get("description") or title,
                "--source", str(top), "--remote", "origin", "--push"])

    # 2. The release.
    tagged = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"], cwd=top,
                            env=command_env(), capture_output=True, text=True).stdout.strip()
    if tagged and tagged != commit:
        raise PublishError(f"The tag {tag} already names another commit ({tagged[:12]}): bump version= in addon.py.")
    if not tagged:
        sh.run(["git", "tag", "-a", tag, "-m", f"{title} {report.version}", commit], top)
    sh.run(["git", "push", "origin", tag], top)
    sh.run(["gh", "release", "create", tag, "--repo", repo, "--title", f"{title} {report.version}", "--notes",
            f"{report.manifest.get('description') or title}\n\nCommit {commit}."], check=False)

    # 3. The catalog pull request.
    catalog_owner = catalog_repo.split("/", 1)[0]
    branch_name = f"addon-{report.name}-{report.version}"
    with tempfile.TemporaryDirectory(prefix="dana-catalog-") as tmp:
        clone = Path(tmp) / "catalog"
        sh.run(["gh", "repo", "clone", catalog_repo, str(clone), "--", "--depth", "1"])
        remote = "origin"
        if owner != catalog_owner:
            sh.run(["gh", "repo", "fork", catalog_repo, "--clone=false", "--remote=false"])
            sh.run(["git", "remote", "add", "fork", f"https://github.com/{owner}/{catalog_repo.split('/', 1)[1]}.git"],
                   clone)
            remote = "fork"
        sh.run(["git", "checkout", "-b", branch_name], clone)
        current: Dict[str, Any] = {"addons": []}
        if not dry_run:
            current = json.loads((clone / CATALOG_FILE).read_text(encoding="utf-8"))
        before = next((e for e in current.get("addons", []) if isinstance(e, dict) and e.get("name") == report.name),
                      None)
        entry = catalog_entry(report, repo, owner, path_in_repo, commit, before)
        verb = "Update" if before else "Add"
        print(f"{'would write' if dry_run else 'write'}: {CATALOG_FILE} with this entry:\n"
              + json.dumps(entry, indent=1, ensure_ascii=False), file=out)
        if not dry_run:
            write_catalog(clone / CATALOG_FILE, with_entry(current, entry))
        sh.run(["git", "commit", "-am", f"{verb} {report.name} {report.version}"], clone)
        sh.run(["git", "push", remote, branch_name], clone)
        body = (f"{verb} **{title}** {report.version} from https://github.com/{repo} at `{commit}`.\n\n"
                f"May touch: {json.dumps(report.touches)}\n\n`bin/dana addons check` and its tests passed locally.")
        sh.run(["gh", "pr", "create", "--repo", catalog_repo, "--head", f"{owner}:{branch_name}",
                "--title", f"{verb} {report.name} {report.version}", "--body", body])
    print("Dry run: nothing was published." if dry_run else
          f"Published {report.name} {report.version}: the catalog pull request is open.", file=out)
