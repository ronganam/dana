"""The ``bin/dana`` parser and entry point (docs/access.md, "Managing users";
docs/writing-an-addon.md).

    bin/dana users …      users, tokens, Telegram ids, shares (cli/users.py)
    bin/dana addons …     the server's addons and a user's connections (cli/addons.py)

Commands that touch the database refuse one that is not migrated (start the
server once first); ``addons new / install / remove`` work on folders only.
"""

import argparse
import logging
import sys
from typing import Optional, Sequence, TextIO

from core import db
from cli import addons as addons_cli
from cli import users as users_cli
from cli.users import CliError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bin/dana", description="Run a Dana server: its users and addons "
                                     "(on the server's database and folders).")
    sub = parser.add_subparsers(dest="area", required=True, metavar="area")
    users_cli.register(sub.add_parser("users", help="users, their tokens, Telegram ids and shares").add_subparsers(
        dest="command", required=True, metavar="command"))
    addons_cli.register(sub.add_parser("addons", help="the server's addons: list, new, install, remove, "
                                       "disable, enable; a user's: show, connect, update, disconnect").add_subparsers(
        dest="command", required=True, metavar="command"))
    return parser


def main(argv: Optional[Sequence[str]] = None, *, stdout: Optional[TextIO] = None,
         stdin: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
    """Run one command; returns the exit status (0 done, 1 refused, 2 bad usage)."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    logging.basicConfig(level=logging.WARNING)
    args = build_parser().parse_args(argv)
    if getattr(args, "needs_db", True):
        pending = db.pending_migrations()
        if pending:
            print(f"error: the database at {db.DATABASE_PATH} is not up to date "
                  f"({len(pending)} migration(s) pending); start the server once first.", file=err)
            return 1
    try:
        args.func(args, out, stdin or sys.stdin)
    except CliError as e:
        print(f"error: {e}", file=err)
        return 1
    return 0
