"""Entries are found by the text of their files: ``entries_fts`` gains a
``files`` column (each of the entry's files as ``name note transcript``, one
per line; ``transcript`` is the text read from the file, addons/knowledge/files.py).

FTS5 cannot add a column, so the table is dropped and made again, and every
entry indexed again from ``entries`` and ``entry_files`` (no row of either is
touched). The triggers on ``entries`` are made again to fill ``files``; new
triggers on ``entry_files`` (insert, update, delete) index that entry again.
"""

import sqlite3

VERSION = 2
NAME = "files_in_search"

COLUMNS = "id, project_id, title, body, summary, transcript, tags, fields, files"


def _files_of(entry: str) -> str:
    """The ``files`` value of entry ``entry`` (an SQL expression)."""
    return (f"(SELECT group_concat(f.name || ' ' || f.note || ' ' || COALESCE(f.transcript, ''), char(10)) "
            f"FROM entry_files f WHERE f.entry_id = {entry})")


def _index(entry: str) -> str:
    """Index entry ``entry`` again (an SQL expression for its id): nothing when it is gone."""
    return (f"DELETE FROM entries_fts WHERE id = {entry};\n"
            f"INSERT INTO entries_fts({COLUMNS}) SELECT e.id, e.project_id, e.title, e.body, e.summary, "
            f"e.transcript, e.tags, e.fields, {_files_of('e.id')} FROM entries e WHERE e.id = {entry};")


STATEMENTS = [
    "DROP TRIGGER entries_ai",
    "DROP TRIGGER entries_ad",
    "DROP TRIGGER entries_au",
    "DROP TABLE entries_fts",
    """CREATE VIRTUAL TABLE entries_fts USING fts5(
        id UNINDEXED, project_id UNINDEXED, title, body, summary, transcript, tags, fields, files,
        tokenize='unicode61'
    )""",
    f"""CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
        INSERT INTO entries_fts({COLUMNS})
        VALUES (new.id, new.project_id, new.title, new.body, new.summary, new.transcript, new.tags, new.fields,
                {_files_of('new.id')});
    END""",
    """CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
        DELETE FROM entries_fts WHERE id = old.id;
    END""",
    f"""CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
        {_index('new.id')}
    END""",
    f"""CREATE TRIGGER entry_files_ai AFTER INSERT ON entry_files BEGIN
        {_index('new.entry_id')}
    END""",
    f"""CREATE TRIGGER entry_files_au AFTER UPDATE ON entry_files BEGIN
        {_index('new.entry_id')}
    END""",
    f"""CREATE TRIGGER entry_files_ad AFTER DELETE ON entry_files BEGIN
        {_index('old.entry_id')}
    END""",
    f"""INSERT INTO entries_fts({COLUMNS}) SELECT e.id, e.project_id, e.title, e.body, e.summary, e.transcript,
        e.tags, e.fields, {_files_of('e.id')} FROM entries e""",
]


def up(conn: sqlite3.Connection) -> None:
    for sql in STATEMENTS:
        conn.execute(sql)
