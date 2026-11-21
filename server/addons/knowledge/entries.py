"""Entries for a user: the notes, records and links inside projects, what a
user can see and change, and full-text search (docs/access.md, "Sharing").

An entry belongs to the owner of its project (``entries.user_id``; an entry
without a project to the user who created it) and records who created it
(``entries.created_by``). A user sees their own entries plus every entry in a
project shared with them; a ``read`` share refuses writes
(:class:`.projects.ReadOnly`); an entry the user cannot see answers as if it
did not exist. The FTS index ``entries_fts`` holds every user's entries, so
every query of it joins ``entries`` and keeps only :func:`visible_sql` rows.

Payloads never carry the owner columns; an entry seen through a share carries
``shared_by`` / ``access``.
"""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core import outbound
from core.db import get_db_connection
from core.users import User
from core.addons import sharing
from addons.knowledge import media, projects
from addons.knowledge.values import (clean_fields, clean_str, clean_tags, norm_decision, norm_kind,
                                           norm_priority, norm_status, serialize_entry, with_share)

# Columns a person edits (undo compares and restores these).
ENTRY_COLUMNS = ("kind", "title", "body", "fields", "status", "priority", "due_at", "tags", "url", "section_id",
                 "decision")
# Who last set the URL: an undo of a URL change puts it back with the URL.
URL_SETTER = "url_set_by"


# --- Scope -----------------------------------------------------------------------------

def visible_sql(user: Optional[User], alias: str = "e") -> Tuple[str, List[Any]]:
    """A WHERE condition (and its parameters) for the entries ``user`` can
    see: their own plus those in projects shared with them; nothing without a
    user or without ``knowledge``. ``alias`` is the entries table's alias
    (``""`` for none)."""
    if not projects.has_knowledge(user):
        return "0", []
    assert user is not None
    col = f"{alias}." if alias else ""
    shared = list(projects.shared_projects(user))
    if not shared:
        return f"{col}user_id = ?", [user.id]
    return f"({col}user_id = ? OR {col}project_id IN ({','.join('?' * len(shared))}))", [user.id, *shared]


def level_of(user: Optional[User], row: Dict[str, Any]) -> Optional[str]:
    """``owner`` / ``edit`` / ``read`` / None for ``user`` on an entry row."""
    if not projects.has_knowledge(user):
        return None
    assert user is not None
    if row.get("user_id") == user.id:
        return sharing.OWNER
    return projects.shared_projects(user).get(row.get("project_id") or "")


class WrongSection(ValueError):
    """The section given for an entry is not one of its project's."""

    def __init__(self) -> None:
        super().__init__("That section is not in this project.")


def _public(user: User, row: Any, files: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    raw = dict(row)
    entry = with_share(serialize_entry(raw), *projects.share_note(user, raw.get("user_id") or "", level_of(user, raw)))
    if files is None:
        from addons.knowledge.files import files_of  # files imports this module
        files = files_of([raw["id"]]).get(raw["id"], [])
    entry["files"] = files
    return entry


def _section_of(project_id: Optional[str], section_id: Any) -> Optional[str]:
    """``section_id`` when it is a section of ``project_id``; None for none ("" / None).

    Raises:
        WrongSection: it is another project's (or no section).
    """
    section_id = clean_str(section_id)
    if not section_id:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT project_id FROM project_sections WHERE id = ?", (section_id,)).fetchone()
    if row is None or row["project_id"] != project_id:
        raise WrongSection()
    return section_id


def entry_row(user: Optional[User], entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The entry row (all columns) if ``user`` can see it."""
    if not entry_id:
        return None
    where, args = visible_sql(user, "")
    with get_db_connection() as conn:
        row = conn.execute(f"SELECT * FROM entries WHERE id = ? AND {where}", (entry_id, *args)).fetchone()
    return dict(row) if row else None


def writable_entry(user: Optional[User], entry_id: Optional[str]) -> Dict[str, Any]:
    """The entry row if ``user`` may change it.

    Raises:
        LookupError: they cannot see it (or it does not exist).
        projects.ReadOnly: its project is shared with them read-only.
    """
    row = entry_row(user, entry_id)
    if row is None:
        raise LookupError("Entry not found")
    if level_of(user, row) == sharing.READ:
        raise projects.ReadOnly()
    return row


def can_edit(user: Optional[User], entry_id: Optional[str]) -> bool:
    try:
        writable_entry(user, entry_id)
    except (LookupError, projects.ReadOnly):
        return False
    return True


# --- Reads -----------------------------------------------------------------------------

def get_entry(user: User, entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Entry ``entry_id`` in API shape if ``user`` can see it."""
    row = entry_row(user, entry_id)
    return _public(user, row) if row else None


def list_entries(user: User, project_id: str) -> List[Dict[str, Any]]:
    """The entries of a project ``user`` can see, newest first (empty when
    they cannot see it)."""
    if projects.access(user, project_id) is None:
        return []
    from addons.knowledge.files import files_of  # files imports this module
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM entries WHERE project_id = ? ORDER BY created_at DESC",
                            (project_id,)).fetchall()
    files = files_of([r["id"] for r in rows])
    return [_public(user, row, files.get(row["id"], [])) for row in rows]


def entry_previews(user: User, project_id: str, kind: Optional[str] = None, status: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
    """Short rows (id, kind, title, body, status, priority, tags, url,
    updated_at, section_id, decision, fields, file_count) of a project ``user``
    can see, newest first."""
    if projects.access(user, project_id) is None:
        return []
    where, args = "WHERE project_id = ?", [project_id]
    if kind:
        where += " AND kind = ?"
        args.append(kind)
    if status:
        where += " AND status = ?"
        args.append(status)
    args.append(max(1, min(int(limit or 50), 200)))
    with get_db_connection() as db:
        rows = db.execute(
            f"""
            SELECT id, kind, title, body, status, priority, tags, url, updated_at, section_id, decision, fields,
                   (SELECT COUNT(*) FROM entry_files f WHERE f.entry_id = entries.id) AS file_count
            FROM entries {where}
            ORDER BY created_at DESC LIMIT ?
            """,
            tuple(args),
        ).fetchall()
    return [dict(r) for r in rows]


def fts_match_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression: every word must appear, order-free."""
    words = query.split()
    terms = ['"' + word.replace('"', '""') + '"' for word in words if word]
    return " AND ".join(terms)


def search_entries(user: User, query: str, project_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Full-text search across the entries ``user`` can see, optionally scoped to one project."""
    match = fts_match_query(query.strip())
    if not match:
        return []
    where, vis_args = visible_sql(user)
    sql = f"""
        SELECT e.*, p.title AS project_title, rank AS score
        FROM entries_fts
        JOIN entries e ON entries_fts.id = e.id
        JOIN projects p ON e.project_id = p.id
        WHERE entries_fts MATCH ? AND {where}
    """
    args: List[Any] = [match, *vis_args]
    if project_id:
        sql += " AND e.project_id = ?"
        args.append(project_id)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)

    with get_db_connection() as conn:
        rows = conn.execute(sql, args).fetchall()
    results = []
    for row in rows:
        entry = _public(user, row)
        entry["project_title"] = row["project_title"]
        results.append(entry)
    return results


def matching_ids(user: Optional[User], fts: str, like: str, limit: int = 30) -> List[str]:
    """Ids of the entries ``user`` can see that match the FTS expression
    ``fts`` (best first) or whose title / body match the LIKE pattern
    ``like`` (``\\`` escapes) — for the search box (``core/search.py``)."""
    where, args = visible_sql(user)
    found: Dict[str, None] = {}
    with get_db_connection() as conn:
        if fts:
            try:
                for r in conn.execute(f"SELECT e.id FROM entries_fts JOIN entries e ON e.id = entries_fts.id "
                                      f"WHERE entries_fts MATCH ? AND {where} ORDER BY rank LIMIT ?",
                                      (fts, *args, limit)).fetchall():
                    found.setdefault(r["id"])
            except Exception:
                pass  # a query FTS can't parse: the LIKE pass still runs
        for r in conn.execute(f"SELECT e.id FROM entries e WHERE (e.title LIKE ? ESCAPE '\\' OR e.body LIKE ? "
                              f"ESCAPE '\\') AND {where} LIMIT ?", (like, like, *args, limit)).fetchall():
            found.setdefault(r["id"])
    return list(found)


def search_rows(user: Optional[User], ids: List[str]) -> List[Dict[str, Any]]:
    """``[{id, title, kind, project_id, project}]`` of the given entries that ``user`` can see."""
    if not ids:
        return []
    where, args = visible_sql(user)
    marks = ",".join("?" * len(ids))
    with get_db_connection() as conn:
        rows = conn.execute(f"SELECT e.id, e.title, e.kind, e.project_id, p.title AS project FROM entries e "
                            f"LEFT JOIN projects p ON p.id = e.project_id WHERE e.id IN ({marks}) AND {where}",
                            (*ids, *args)).fetchall()
    return [dict(r) for r in rows]


# --- Writes ----------------------------------------------------------------------------

def check_url(user: User, url: Optional[str]) -> None:
    """A link ``user`` is about to set on an entry: for a user without
    ``manage_server``, http(s) on a host that is not internal as far as it can
    be told without a lookup (:func:`core.outbound.check_url_form`; the
    download checks it fully again). "" / None (no link) always passes.

    Raises:
        outbound.Refused: why, in words for the user.
    """
    if clean_str(url) and not outbound.trusted(user):
        outbound.check_url_form(clean_str(url))


async def create_entry(user: User, project_id: str, *, kind: str = "note", title: str = "", body: str = "",
                       fields: Any = None, tags: Any = None, status: str = "open", priority: str = "medium",
                       due_at: Optional[str] = None, url: Optional[str] = None, section_id: Optional[str] = None,
                       decision: str = "") -> Optional[str]:
    """Insert an entry created by ``user`` into a project; it belongs to the
    project's owner. A URL is processed in the background (as that owner).
    Returns its ID, or None when ``user`` cannot see the project.

    Raises:
        projects.ReadOnly: the project is shared with them read-only.
        outbound.Refused: the link is not one they may make the server fetch.
        WrongSection: ``section_id`` is not a section of the project.
    """
    try:
        owner = projects.writable_project(user, project_id)
    except LookupError:
        return None
    check_url(user, url)
    section = _section_of(project_id, section_id)
    entry_id = str(uuid.uuid4())
    with get_db_connection() as conn:
        conn.execute(
            """
            INSERT INTO entries (id, project_id, kind, title, body, fields, status, priority, due_at, tags, url,
                                 user_id, created_by, url_set_by, section_id, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, project_id,
                norm_kind(kind), clean_str(title), body or "",
                clean_fields(fields), norm_status(status), norm_priority(priority),
                clean_str(due_at) or None, clean_tags(tags), clean_str(url) or None,
                owner, user.id, user.id if clean_str(url) else None, section, norm_decision(decision),
            ),
        )
        conn.commit()

    if clean_str(url):
        media.start_processing(entry_id, clean_str(url), user)
    return entry_id


def update_entry(user: User, entry_id: str, *, kind: Optional[str] = None, title: Optional[str] = None,
                 body: Optional[str] = None, fields: Any = None, tags: Any = None, status: Optional[str] = None,
                 priority: Optional[str] = None, due_at: Optional[str] = None,
                 url: Optional[str] = None, section_id: Optional[str] = None,
                 decision: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Partially update an entry ``user`` may change. `None` means 'leave
    unchanged' (pass "" to clear a value). A URL that differs from the stored
    one records ``user`` as who set it (``url_set_by``). Returns the updated
    entry, or None when they cannot see it.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
        outbound.Refused: the new link is not one they may make the server fetch.
        WrongSection: ``section_id`` is not a section of the entry's project.
    """
    try:
        current = writable_entry(user, entry_id)
    except LookupError:
        return None
    section = _section_of(current.get("project_id"), section_id) if section_id is not None else None
    new_url = (clean_str(url) or None) if url is not None else current.get("url")
    url_changed = new_url != current.get("url")
    if url_changed:
        check_url(user, new_url)
    sets, args = [], []
    if kind is not None:
        sets.append("kind = ?"); args.append(norm_kind(kind))
    if title is not None and clean_str(title):
        sets.append("title = ?"); args.append(clean_str(title))
    if body is not None:
        sets.append("body = ?"); args.append(body)
    if fields is not None:
        sets.append("fields = ?"); args.append(clean_fields(fields))
    if tags is not None:
        sets.append("tags = ?"); args.append(clean_tags(tags))
    if status is not None:
        sets.append("status = ?"); args.append(norm_status(status))
    if priority is not None:
        sets.append("priority = ?"); args.append(norm_priority(priority))
    if due_at is not None:
        sets.append("due_at = ?"); args.append(clean_str(due_at) or None)
    if url is not None:
        sets.append("url = ?"); args.append(new_url)
    if section_id is not None:
        sets.append("section_id = ?"); args.append(section)
    if decision is not None:
        sets.append("decision = ?"); args.append(norm_decision(decision))
    if url_changed:
        # Who set the link decides how freely it is downloaded (media.process_knowledge_media).
        sets.append("url_set_by = ?"); args.append(user.id)
    with get_db_connection() as conn:
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", (*args, entry_id))
            conn.commit()
        row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
    return _public(user, row)


def delete_entry(user: User, entry_id: str) -> bool:
    """Delete an entry ``user`` may change. Returns False when they cannot see it.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
    """
    from addons.knowledge import files  # files imports this module
    try:
        writable_entry(user, entry_id)
    except LookupError:
        return False
    with get_db_connection() as conn:
        files.forget_entries(conn, [entry_id])
        rows = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,)).rowcount
        conn.commit()
    return rows > 0


def retry_media(user: User, entry_id: str) -> Dict[str, Any]:
    """Process the URL of an entry ``user`` may change again (in the
    background, as its owner; nothing when it has no URL). Returns the entry row.

    Raises:
        LookupError: they cannot see it.
        projects.ReadOnly: its project is shared with them read-only.
    """
    row = writable_entry(user, entry_id)
    if row.get("url"):
        media.start_processing(entry_id, row["url"], user)
    return row


def media_file(user: Optional[User], name: str) -> Optional[Path]:
    """The stored media file ``name`` (``/media/<name>``) when an entry
    ``user`` can see has it; None otherwise (answer 404)."""
    where, args = visible_sql(user, "")
    return media.file_for(where, args, name)
