"""The Israeli banks moved to their own provider addon, ``money_il``: every user
who has ``money`` connected and a source or an account at one of its
companies (``hapoalim``, ``visaCal``, ``psagot``, ``pluxee``) gets ``money_il``
connected, so their logins keep syncing with no action. Sources, their
encrypted credentials and device keys stay in ``money_sources`` (money's
table, keyed by company); ``money_il`` has no credentials of its own."""

import datetime
import sqlite3

VERSION = 2
NAME = "money_il"

IL_COMPANIES = ("hapoalim", "visaCal", "psagot", "pluxee")


def up(conn: sqlite3.Connection) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")
    marks = ",".join("?" * len(IL_COMPANIES))
    conn.execute(
        "INSERT OR IGNORE INTO connections (user_id, addon, credentials, settings, account, created_at, updated_at) "
        "SELECT c.user_id, 'money_il', NULL, '{}', NULL, ?, ? FROM connections c WHERE c.addon = 'money' AND ("
        f"EXISTS (SELECT 1 FROM money_sources s WHERE s.user_id = c.user_id AND s.company IN ({marks})) OR "
        f"EXISTS (SELECT 1 FROM money_accounts a WHERE a.user_id = c.user_id AND a.company IN ({marks})))",
        (now, now, *IL_COMPANIES, *IL_COMPANIES))
