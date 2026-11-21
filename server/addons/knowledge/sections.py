"""Sections of a project (docs/knowledge.md, "Sections").

A section is a part of a project: ``options`` — a decision among options that
share typed ``columns`` (each option is an entry whose ``fields`` hold the
column values by name, and whose ``decision`` says where it stands) — or
``notes`` — free items. Its ``summary`` is the Markdown write-up of the
section (the TL;DR, the recommendation).

A section follows its project: whoever sees the project sees its sections;
the owner and an ``edit`` share add, change and delete them; a ``read`` share
is refused (:class:`.projects.ReadOnly`). A section the user cannot see
answers as if it did not exist.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from core.db import get_db_connection
from core.users import User
from addons.knowledge import projects
from addons.knowledge.values import (clean_columns, clean_str, loads_json, norm_section_kind,
                                           serialize_section)

# Columns a person edits (undo compares and restores these).
SECTION_COLUMNS = ("title", "icon", "kind", "columns", "summary", "position")


def section_row(user: Optional[User], section_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The section row if ``user`` can see its project."""
    if not section_id:
        return None
    with get_db_connection() as conn:
        row = conn.execute("SELECT * FROM project_sections WHERE id = ?", (section_id,)).fetchone()
    if row is None or projects.access(user, row["project_id"]) is None:
        return None
    return dict(row)


def writable_section(user: Optional[User], section_id: Optional[str]) -> Dict[str, Any]:
    """The section row if ``user`` may change it.

    Raises:
        LookupError: they cannot see it (or it does not exist).
        projects.ReadOnly: its project is shared with them read-only.
    """
    row = section_row(user, section_id)
    if row is None:
        raise LookupError("Section not found")
    projects.writable_project(user, row["project_id"])
    return row


def list_sections(user: User, project_id: str) -> List[Dict[str, Any]]:
    """The sections of a project ``user`` can see, in order (empty when they cannot see it)."""
    if projects.access(user, project_id) is None:
        return []
    with get_db_connection() as conn:
        rows = conn.execute("SELECT * FROM project_sections WHERE project_id = ? ORDER BY position, created_at",
                            (project_id,)).fetchall()
    return [serialize_section(r) for r in rows]


def get_section(user: User, section_id: Optional[str]) -> Optional[Dict[str, Any]]:
    row = section_row(user, section_id)
    return serialize_section(row) if row else None


def find_section(user: User, project_id: str, ref: Optional[str]) -> Optional[Dict[str, Any]]:
    """The section of ``project_id`` with id or title ``ref`` (title case-insensitive)."""
    ref = clean_str(ref)
    if not ref:
        return None
    for section in list_sections(user, project_id):
        if section["id"] == ref or section["title"].lower() == ref.lower():
            return section
    return None


def create_section(user: User, project_id: str, title: str, *, kind: str = "notes", columns: Any = None,
                   icon: str = "", summary: str = "", position: Optional[int] = None) -> Optional[str]:
    """Add a section to a project ``user`` may put things into; returns its id,
    or None when they cannot see the project (or the title is empty).

    Raises:
        projects.ReadOnly: the project is shared with them read-only.
    """
    try:
        projects.writable_project(user, project_id)
    except LookupError:
        return None
    title = clean_str(title)
    if not title:
        return None
    section_id = str(uuid.uuid4())
    kind = norm_section_kind(kind)
    with get_db_connection() as conn:
        if position is None:
            row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next FROM project_sections "
                               "WHERE project_id = ?", (project_id,)).fetchone()
            position = int(row["next"])
        conn.execute(
            "INSERT INTO project_sections (id, project_id, title, icon, kind, columns, summary, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (section_id, project_id, title, clean_str(icon), kind,
             json.dumps(clean_columns(columns) if kind == "options" else [], ensure_ascii=False),
             summary or "", int(position)),
        )
        conn.commit()
    return section_id


def _rename_fields(conn: Any, section_id: str, renames: Dict[str, str]) -> None:
    """Carry a column rename into the fields of the section's entries."""
    if not renames:
        return
    # scoped: the section was checked writable by update_section (writable_section)
    for row in conn.execute("SELECT id, fields FROM entries WHERE section_id = ?", (section_id,)).fetchall():
        fields = loads_json(row["fields"], [])
        changed = False
        for field in fields:
            if isinstance(field, dict) and field.get("name") in renames:
                field["name"] = renames[field["name"]]
                changed = True
        if changed:
            # scoped: the section was checked writable by update_section (writable_section)
            conn.execute("UPDATE entries SET fields = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (json.dumps(fields, ensure_ascii=False), row["id"]))


def update_section(user: User, section_id: str, *, title: Optional[str] = None, icon: Optional[str] = None,
                   kind: Optional[str] = None, columns: Any = None, summary: Optional[str] = None,
                   position: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Partially update a section ``user`` may change (None: leave as is). A
    renamed column (same ``key``, new name) renames the value in every entry of
    the section. Returns the section, or None when they cannot see it.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
    """
    try:
        current = writable_section(user, section_id)
    except LookupError:
        return None
    sets: List[str] = []
    args: List[Any] = []
    renames: Dict[str, str] = {}
    if title is not None and clean_str(title):
        sets.append("title = ?"); args.append(clean_str(title))
    if icon is not None:
        sets.append("icon = ?"); args.append(clean_str(icon))
    if kind is not None:
        sets.append("kind = ?"); args.append(norm_section_kind(kind))
    if columns is not None:
        previous = loads_json(current["columns"], [])
        cleaned = clean_columns(columns, previous)
        old_names = {c["key"]: c["name"] for c in previous if isinstance(c, dict) and c.get("key")}
        renames = {old_names[c["key"]]: c["name"] for c in cleaned
                   if c["key"] in old_names and old_names[c["key"]] != c["name"]}
        sets.append("columns = ?"); args.append(json.dumps(cleaned, ensure_ascii=False))
    if summary is not None:
        sets.append("summary = ?"); args.append(summary)
    if position is not None:
        sets.append("position = ?"); args.append(int(position))
    with get_db_connection() as conn:
        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            conn.execute(f"UPDATE project_sections SET {', '.join(sets)} WHERE id = ?", (*args, section_id))
            _rename_fields(conn, section_id, renames)
            conn.commit()
        row = conn.execute("SELECT * FROM project_sections WHERE id = ?", (section_id,)).fetchone()
    return serialize_section(row)


def delete_section(user: User, section_id: str, *, with_entries: bool = False) -> bool:
    """Delete a section ``user`` may change. Its entries stay in the project
    without a section, or are deleted too with ``with_entries``. Returns False
    when they cannot see it.

    Raises:
        projects.ReadOnly: its project is shared with them read-only.
    """
    from addons.knowledge import files  # files → entries → sections would be circular at import
    try:
        writable_section(user, section_id)
    except LookupError:
        return False
    with get_db_connection() as conn:
        if with_entries:
            # scoped: writable_section(user, section_id) checked above
            ids = [r["id"] for r in conn.execute("SELECT id FROM entries WHERE section_id = ?", (section_id,))]
            files.forget_entries(conn, ids)
            # scoped: writable_section(user, section_id) checked above
            conn.execute("DELETE FROM entries WHERE section_id = ?", (section_id,))
        else:
            # scoped: writable_section(user, section_id) checked above
            conn.execute("UPDATE entries SET section_id = NULL WHERE section_id = ?", (section_id,))
        rows = conn.execute("DELETE FROM project_sections WHERE id = ?", (section_id,)).rowcount
        conn.commit()
    return rows > 0
