"""``bin/dana backups …``: make and restore backups of the server (docs/backups.md; core/backups.py).

    backups create                  a backup into the destination chosen in Settings → Backups, pruned to its keep
    backups list                    the archives in the destination
    backups restore ARCHIVE [--yes] put an archive back, with the server stopped; without --yes:
                                    says what it would restore, changes nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO

from core import backups
from core.pages_api import server_version
from cli.users import CliError


def _size(n: int) -> str:
    if n < 1024:
        return f"{n:,} bytes"
    value = float(n)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            break
    return f"{value:.1f} {unit}"


def cmd_create(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    try:
        b = backups.back_up("cli")
    except backups.Busy as e:
        raise CliError(str(e))
    except backups.Invalid as e:
        raise CliError(e.detail)
    print(f"Backup written: {Path(backups.settings()['destination']) / b.name} ({_size(b.size)})", file=out)
    return 0


def cmd_list(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    folder = backups.settings()["destination"]
    found = backups.list_backups(Path(folder))
    print(f"{len(found)} backup(s) in {folder}", file=out)
    for b in found:
        print(f"  {b.name}  {_size(b.size)}  {b.created_at}", file=out)
    return 0


def cmd_restore(args: argparse.Namespace, out: TextIO, stdin: TextIO) -> int:
    archive = Path(args.archive)
    if not archive.is_file():
        raise CliError(f"{archive} is not a file.")
    try:
        manifest = backups.read_manifest(archive)
    except backups.RestoreError as e:
        raise CliError(str(e))
    key = manifest.get("credentials_key")
    print(f"Backup of {manifest.get('created_at')} (Dana {manifest.get('dana_version')}): the database, "
          f"{manifest.get('files', 0)} files in {', '.join(manifest.get('dirs') or []) or 'no folders'}"
          + (", the credentials key file" if key == "file" else ""), file=out)
    if manifest.get("dana_version") != server_version():
        print(f"This server is Dana {server_version()}: an older database is migrated at the next start; "
              "a newer one does not start on this version.", file=out)
    if not args.yes:
        print("Nothing changed. Stop the server, then run this again with --yes to replace the database "
              "and these folders.", file=out)
        return 0
    try:
        done = backups.restore(archive)
    except backups.RestoreError as e:
        raise CliError(str(e))
    if done["snapshot"]:
        print(f"The database as it was is kept in {done['snapshot']}", file=out)
    print(f"Restored the database and {', '.join(done['restored']) or 'no folders'}.", file=out)
    if key == "env":
        print("The credentials key came from CREDENTIALS_KEY when this backup was made: start the server with "
              "that same key, or the stored credentials cannot be read.", file=out)
    elif key == "none":
        print("The backup had no credentials key (nothing was encrypted yet).", file=out)
    print("Start the server.", file=out)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    """Add the ``backups`` commands."""
    p = sub.add_parser("create", help="a backup into the destination chosen in Settings → Backups")
    p.set_defaults(func=cmd_create, needs_db=True)
    p = sub.add_parser("list", help="the archives in the destination")
    p.set_defaults(func=cmd_list, needs_db=True)
    p = sub.add_parser("restore", help="put an archive back (stop the server first)")
    p.add_argument("archive", help="a dana-backup-….tar.gz file")
    p.add_argument("--yes", action="store_true", help="replace the database and folders (without it: a dry run)")
    p.set_defaults(func=cmd_restore, needs_db=False)
