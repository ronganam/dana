"""The knowledge tools: projects and the entries saved in them, for the
calling user (their own projects and the ones shared with them;
docs/access.md, "Sharing"). Data access lives in :mod:`.projects` and
:mod:`.entries`; every change registers an undo (docs/agent-actions.md).
"""

import logging
from typing import Callable, Optional

from core import outbound, undoable
from core.db import get_db_connection
from core.text import escape_markdown
from core.users import User
from core.addons import Connection, sharing
from addons.knowledge import entries, files, projects, sections
from addons.knowledge.entries import ENTRY_COLUMNS
from addons.knowledge.addon import addon
from addons.knowledge.projects import PROJECT_COLUMNS
from addons.knowledge.sections import SECTION_COLUMNS
from addons.knowledge.values import (DECISIONS, KINDS, STATUSES, as_number, clean_str, loads_json,
                                           norm_section_kind)

DECISION_WORDS = {"": "option", "shortlist": "shortlisted", "chosen": "CHOSEN", "rejected": "rejected"}

logger = logging.getLogger(__name__)


# --- Undo support ---

def _record_project_created(user: User, project_id: str, title: str) -> None:
    created = undoable.row("projects", "id", project_id, PROJECT_COLUMNS)
    if not created:
        return

    def _undo():
        with get_db_connection() as conn:
            busy = (conn.execute("SELECT 1 FROM entries WHERE project_id = ?", (project_id,)).fetchone()
                    or conn.execute("SELECT 1 FROM tasks WHERE project_id = ?", (project_id,)).fetchone())
        if busy:
            raise undoable.UndoConflict("Things were saved into the project since, so it was kept.")
        current = undoable.row("projects", "id", project_id, PROJECT_COLUMNS)
        if current is None:
            return
        if not undoable.same(current, created, PROJECT_COLUMNS):
            raise undoable.UndoConflict("The project was changed since, so it was kept.")
        projects.delete_project(user, project_id)

    undoable.safe_record(f"Created project “{clean_str(title)}”", _undo, "create_project")


def _entry_guard(user: User, entry_id: str) -> Callable[[], None]:
    """An undo guard: refuse once ``user`` can no longer change the entry (its
    project unshared or made read-only for them); an entry that is gone is
    left to the undo itself."""
    def _guard() -> None:
        if undoable.row("entries", "id", entry_id, ("id",)) is not None and not entries.can_edit(user, entry_id):
            raise undoable.UndoConflict("You can no longer change this entry, so it was left as it is.")
    return _guard


def _error(e: Exception) -> str:
    return f"Error: {e}"


def _section_id(user: User, project_id: Optional[str], ref: Optional[str]) -> Optional[str]:
    """The id of the section ``ref`` (id or title) of ``project_id``; "" for "no section";
    None when ``ref`` is None (leave as is).

    Raises:
        LookupError: no such section in the project.
    """
    if ref is None:
        return None
    if not clean_str(ref):
        return ""
    found = sections.find_section(user, project_id or "", ref)
    if found is None:
        raise LookupError(f"No section '{clean_str(ref)}' in this project. Use `list_project_entries` to see its "
                          "sections, or `create_section`.")
    return found["id"]


def _value_text(field: dict) -> str:
    value = field.get("value")
    if value is None or value == "":
        return "—"
    if field.get("type") == "checkbox":
        return "yes" if value else "no"
    if field.get("type") == "money" and as_number(value) is not None:
        return f"{as_number(value):,} NIS"
    return str(value)


# --- Agent tools ---

@addon.tool(is_write=True)
async def create_project(conn: Connection, title: str, description: str = "", icon: str = "📁", color: str = "#6366f1",
                         tags: Optional[list] = None, target_date: Optional[str] = None,
                         budget: Optional[float] = None) -> str:
    """Create a new project/topic that groups related sections, entries and tasks (e.g. wedding, trip, flat hunt).

    Call this only after `list_projects` shows no matching active project, and only when the topic
    clearly will collect multiple notes/links/tasks. The project title is a short label in the user's
    language; description is one sentence. The returned ID is passed to `create_section` / `save_knowledge_item`.

    Args:
        title: The name of the project (e.g. "Paris Trip 2026").
        description: Optional one-line details about the project.
        icon: Optional emoji icon for the project (e.g. "💍" for a wedding).
        color: Optional accent color as a hex value (e.g. "#e11d48").
        tags: Optional list of short labels for the project.
        target_date: Optional ISO date the project points at (the wedding day, the flight), shown as a countdown.
        budget: Optional total budget in NIS; chosen options' prices are counted against it.
    """
    try:
        project_id = projects.create_project(conn.user, title, description, icon, color, tags, target_date, budget)
        _record_project_created(conn.user, project_id, title)
        return f"Successfully created project '{escape_markdown(title)}' with ID `{project_id}`."
    except Exception as e:
        logger.error(f"Error creating project: {e}", exc_info=True)
        return f"Error creating project: {e}"


@addon.tool()
async def list_projects(conn: Connection, status: Optional[str] = None) -> str:
    """List all projects/topics (titles + IDs + entry/task counts), optionally filtered by status.

    Use as the first step before saving anything, to find the project to save into.

    Args:
        status: Status to filter by ('active' or 'archived'). Defaults to None (all projects).
    """
    try:
        found = projects.list_projects(conn.user, status)
        if not found:
            return "No projects found" + (f" with status '{status}'." if status else ".")

        res = (status.capitalize() if status else "All") + " Projects:\n"
        for p in found:
            icon = p.get("icon") or "📁"
            title = escape_markdown(p["title"])
            desc = escape_markdown(p["description"])
            st = escape_markdown(p["status"])
            counts = f"({p['entry_count']} entries, {p['task_count']} tasks"
            if p.get("decisions"):
                counts += f", {p['decided']}/{p['decisions']} decisions made"
            if p.get("target_date"):
                counts += f", date {p['target_date']}"
            if p.get("budget"):
                counts += f", budget {as_number(p['budget']):,}, committed {as_number(p['committed']) or 0:,}"
            counts += ")"
            shared = ""
            if p.get("shared_by") is not None:
                shared = (f" [shared by {escape_markdown(p['shared_by'])}"
                          f"{', read-only' if p['access'] == sharing.READ else ''}]")
            res += f"- {icon} **{title}** (`{p['id']}`) [{st}]{shared} {counts}: {desc}\n"
        return res
    except Exception as e:
        logger.error(f"Error listing projects: {e}", exc_info=True)
        return f"Error listing projects: {e}"


@addon.tool()
async def list_project_entries(conn: Connection, project_id: str, kind: Optional[str] = None,
                               status: Optional[str] = None, limit: int = 80) -> str:
    """Show a project's structure: its sections (with their columns and summary state) and every entry
    in each (id, title, decision, column values, file count, short preview).

    Use this to see what a project already contains BEFORE saving anything, so you never create a
    duplicate of something that is already there, and to find the section to save into. Bodies are
    truncated — call `get_knowledge_item` with the entry ID to read one in full (with its files).

    Args:
        project_id: The ID of the project to list (from `list_projects`).
        kind: Optional filter by kind ('note', 'record' or 'link'). Defaults to None (all kinds).
        status: Optional filter by status ('open', 'in_progress', 'done' or 'archived').
        limit: Maximum number of entries to return. Defaults to 80.
    """
    try:
        project = projects.get_project(conn.user, project_id)
        if project is None:
            return f"Error: Project with ID `{project_id}` not found. Use `list_projects` to find the right ID."
        if kind and kind not in KINDS:
            return f"Error: kind must be one of {', '.join(KINDS)}."
        if status and status not in STATUSES:
            return f"Error: status must be one of {', '.join(STATUSES)}."

        rows = entries.entry_previews(conn.user, project_id, kind, status, limit)
        secs = sections.list_sections(conn.user, project_id)
        head = f"Project {project.get('icon') or ''} {escape_markdown(project['title'])}"
        if project.get("target_date"):
            head += f" | date {project['target_date']}"
        if project.get("budget"):
            head += f" | budget {as_number(project['budget']):,}"
        res = head + "\n"
        if not rows and not secs:
            return res + "No sections and no entries yet. Nothing saved."

        def entry_line(e: dict, columns: Optional[list] = None) -> str:
            title = escape_markdown(e["title"] or "Untitled")
            bits = [e["kind"]]
            if e.get("decision"):
                bits.append(DECISION_WORDS.get(e["decision"], e["decision"]))
            if e.get("file_count"):
                bits.append(f"{e['file_count']} file{'s' if e['file_count'] != 1 else ''}")
            line = f"- **[{e['id']}]** {title} ({', '.join(bits)})"
            fields = [f for f in loads_json(e.get("fields"), []) if isinstance(f, dict)]
            if columns:
                names = [c["name"] for c in columns]
                by_name = {f.get("name"): f for f in fields}
                values = [f"{escape_markdown(n)}: {escape_markdown(_value_text(by_name[n]))}" for n in names if n in by_name]
                if values:
                    line += "\n  " + " | ".join(values)
            preview = " ".join((e["body"] or "").split())[:120]
            if preview:
                line += f"\n  {escape_markdown(preview)}"
            return line + "\n"

        by_section: dict = {}
        for e in rows:
            by_section.setdefault(e.get("section_id") or "", []).append(e)
        for sec in secs:
            items = by_section.pop(sec["id"], [])
            res += f"\n## Section {sec.get('icon') or ''} {escape_markdown(sec['title'])} (`{sec['id']}`, {sec['kind']})\n"
            if sec["kind"] == "options":
                cols = ", ".join(f"{escape_markdown(c['name'])} ({c['type']}{', price' if c.get('role') == 'price' else ''})"
                                 for c in sec["columns"])
                res += f"Columns: {cols or 'none yet'}\n"
            res += f"Summary: {'written (' + str(len(sec['summary'])) + ' chars)' if sec.get('summary') else 'none yet'}\n"
            res += "".join(entry_line(e, sec["columns"]) for e in items) or "(no entries)\n"
        loose = [e for items in by_section.values() for e in items]
        if loose:
            res += "\n## Not in a section\n" + "".join(entry_line(e) for e in loose)
        res += "\nUse `get_knowledge_item(entry_id)` to read one in full."
        return res
    except Exception as e:
        logger.error(f"Error listing project entries: {e}", exc_info=True)
        return f"Error listing project entries: {e}"


@addon.tool()
async def get_knowledge_item(conn: Connection, item_id: str) -> str:
    """Read one saved entry in full — its complete Markdown body, structured fields, summary,
    transcript, tags and comments.

    `search_knowledge` and `list_project_entries` only return a short preview. Use this whenever
    you need the actual details of a specific item (prices, contacts, decisions, dates).

    Args:
        item_id: The entry ID, as returned by `search_knowledge` or `list_project_entries`.
    """
    try:
        entry = entries.get_entry(conn.user, item_id)
        if entry is None:
            return f"Error: No entry with ID `{item_id}`. Use `list_project_entries` or `search_knowledge` to find valid IDs."

        title = escape_markdown(entry.get("title") or "Untitled")
        res = f"### {title}\n"
        res += f"- ID: `{entry['id']}` | kind: {entry['kind']} | status: {entry['status']} | priority: {entry['priority']}\n"
        if entry.get("section_id"):
            sec = sections.get_section(conn.user, entry["section_id"])
            if sec:
                res += f"- Section: {escape_markdown(sec['title'])} (`{sec['id']}`)\n"
        if entry.get("decision"):
            res += f"- Decision: {DECISION_WORDS.get(entry['decision'], entry['decision'])}\n"
        for label, key in (("Created", "created_at"), ("Updated", "updated_at"), ("Due", "due_at"), ("URL", "url")):
            if entry.get(key):
                res += f"- {label}: {escape_markdown(str(entry[key]))}\n"
        if entry.get("tags"):
            res += f"- Tags: {', '.join(escape_markdown(str(t)) for t in entry['tags'])}\n"
        if entry.get("shared_by") is not None:
            res += (f"- Shared by {escape_markdown(entry['shared_by'])}"
                    f"{' (read-only)' if entry['access'] == sharing.READ else ''}\n")

        if entry.get("fields"):
            res += "\n**Fields:**\n"
            for f in entry["fields"]:
                if isinstance(f, dict):
                    res += f"- {escape_markdown(str(f.get('name', '?')))}: {escape_markdown(str(f.get('value', '')))}\n"
        for label, key in (("Summary", "summary"), ("Transcript", "transcript")):
            if entry.get(key):
                res += f"\n**{label}:**\n{escape_markdown(str(entry[key]))}\n"
        if entry.get("body"):
            res += f"\n**Body:**\n{entry['body']}"
        if entry.get("files"):
            res += "\n\n**Files:**\n"
            for f in entry["files"]:
                note = f" — {escape_markdown(f['note'])}" if f.get("note") else ""
                res += f"- {escape_markdown(f['name'])} ({f['mime']}, {round((f.get('size') or 0) / 1024)} KB){note}\n"
                if f.get("transcript"):
                    res += f"  Transcript: {escape_markdown(f['transcript'])}\n"
        if entry.get("comments"):
            res += "\n\n**Comments:**\n"
            for c in entry["comments"]:
                if isinstance(c, dict):
                    who = escape_markdown(str(c.get("author") or c.get("by") or ""))
                    text = escape_markdown(str(c.get("text") or c.get("body") or ""))
                    res += f"- {who}: {text}\n" if who else f"- {text}\n"
        return res
    except Exception as e:
        logger.error(f"Error getting knowledge item: {e}", exc_info=True)
        return f"Error getting knowledge item: {e}"


@addon.tool(is_write=True)
async def save_knowledge_item(conn: Connection, project_id: str, kind: str = "note", title: str = "", body: str = "",
                              fields: Optional[list] = None, tags: Optional[list] = None,
                              priority: str = "medium", due_at: Optional[str] = None,
                              url: Optional[str] = None, section: Optional[str] = None,
                              decision: str = "") -> str:
    """Save a knowledge entry (note, record, or link) into a project and return its ID. The returned
    ID is used later with update_knowledge_item / delete_knowledge_item / save_whatsapp_file.
    In an 'options' section (a decision such as photographers or venues) the entry is one option:
    give `fields` exactly the section's column names (see `list_project_entries`), in the column's
    type. Follow these AUTHORING RULES:
    - title is a SHORT label (max ~8 words) in the user's language, with NO markdown and NO punctuation.
      Example: "Photographer — Yanai Budget". Never paste a whole paragraph into the title.
    - body holds the full details written as clean Markdown: '##' or '###' headings, **bold** for key
      figures and names, '-' bullet lists, and a blank line between paragraphs. Preserve the user's
      facts, numbers, dates and Hebrew/English wording exactly. Example body:
        ## Contact
        - Phone: **050-1234567**
        - Email: yanai@example.com
        ## Budget
        - Price: 4,500 NIS
        - Deposit paid: **yes** (12/06/2026)
    - kind: 'note' for free text (ideas, general notes), 'record' for structured data (contacts,
      vendors, prices, contracts) — pair with fields, 'link' when saving a URL (auto-processed for
      transcript, summary and media).
    - fields: capture the few facts you might want to query later as
      [{"name": "<label>", "type": "<type>", "value": <value>}] where type is one of
      text, number, money (a plain number in NIS), phone, checkbox, date, url, select, rating (1-5). Example:
      [{"name": "Phone", "type": "text", "value": "050-1234567"},
       {"name": "Price", "type": "number", "value": 4500},
       {"name": "Paid", "type": "checkbox", "value": true}]
    - tags: 2-4 short labels (words or short phrases) in the user's language to enable cross-project
      search, e.g. ["צלם", "תקציב"] or ["vendor"], ["budget"].
    - status: 'open' (default) | 'in_progress' | 'done' | 'archived'.
    - priority: 'low' | 'medium' | 'high'.
    - due_at: ISO date 'YYYY-MM-DD' only when a deadline or timing matters.
    - url: full URL to attach and auto-process; typically combined with kind 'link'.

    Args:
        project_id: The ID of the project this entry belongs to.
        kind: 'note' for free text, 'record' for structured data, or 'link' for a URL.
        title: Short human-readable label; never a whole paragraph.
        body: Markdown body with the full details or description.
        fields: Optional structured fields as [{"name": "...", "type": "...", "value": ...}].
                type is one of: text, number, checkbox, date, url, select.
        tags: Optional list of short labels (e.g. ["venue", "vendor"]).
        priority: 'low', 'medium' or 'high'.
        due_at: Optional ISO date (YYYY-MM-DD) for a deadline.
        url: Optional URL to attach and auto-process.
        section: Optional section (title or ID) of the project to put the entry in.
        decision: For an option: '' (just an option), 'shortlist', 'chosen' or 'rejected'.
    """
    try:
        section_id = _section_id(conn.user, project_id, section) if projects.get_project(conn.user, project_id) else None
        entry_id = await entries.create_entry(
            conn.user, project_id, kind=kind, title=title, body=body, fields=fields, tags=tags,
            priority=priority, due_at=due_at, url=url, section_id=section_id, decision=decision,
        )
        if entry_id is None:
            return f"Error: Project with ID `{project_id}` not found."
        # Columns the user edits; link processing fills other ones (summary,
        # transcript, media) without blocking the undo.
        cols = ("title", "body", "fields", "status", "priority", "due_at", "tags", "section_id", "decision")
        created = undoable.row("entries", "id", entry_id, cols)
        if created:
            undoable.delete_created_row(f"Saved “{clean_str(title) or 'Untitled'}”", "entries", "id", entry_id,
                                        created, cols, "save_knowledge_item",
                                        guard=_entry_guard(conn.user, entry_id))
        return f"Successfully saved {kind} entry `{escape_markdown(title or 'Untitled')}` to project. ID: `{entry_id}`."
    except (projects.ReadOnly, outbound.Refused, entries.WrongSection, LookupError) as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error saving knowledge item: {e}", exc_info=True)
        return f"Error saving knowledge item: {e}"


@addon.tool()
async def search_knowledge(conn: Connection, query: str, project_id: Optional[str] = None) -> str:
    """Search the knowledge base (titles, bodies, AI summaries, transcripts, tags, fields) with FTS.

    Use to answer the user's questions like 'what did I save/find about X?'. Prefer 1-2 concrete
    Hebrew/English keywords over full sentences. Pass project_id to scope the search to a project.

    Args:
        query: The search term(s), e.g. "צלם" or "insurance policy".
        project_id: Optional project ID to restrict the search to a specific project.
    """
    if not query.strip():
        return "Error: Search query cannot be empty."
    try:
        results = entries.search_entries(conn.user, query, project_id)
        if not results:
            return f"No results found for '{escape_markdown(query.strip())}'."

        res = f"Top results for '{escape_markdown(query.strip())}':\n"
        for item in results:
            proj = escape_markdown(item.get("project_title") or "unknown")
            title = escape_markdown(item["title"] or "Untitled")
            snippet = escape_markdown((item.get("body") or "")[:200])
            suffix = f" — {snippet}" if snippet else ""
            res += f"- **[{proj}] {title}** ({item['kind']}): {suffix}\n"
        return res
    except Exception as e:
        logger.error(f"Error searching knowledge: {e}", exc_info=True)
        return f"Error searching knowledge: {e}"


@addon.tool(is_write=True)
async def update_project(conn: Connection, project_id: str, title: Optional[str] = None, description: Optional[str] = None,
                         status: Optional[str] = None, icon: Optional[str] = None,
                         color: Optional[str] = None, tags: Optional[list] = None,
                         target_date: Optional[str] = None, budget: Optional[float] = None) -> str:
    """Update a project's metadata (partial update: only provided arguments change). Entries and tasks
    are not touched here — use update_knowledge_item for entries. Rename the topic, change its icon/
    color/tags, or archive the whole project with status='archived' when it is finished.

    Args:
        project_id: The ID of the project to update.
        title: Optional new title.
        description: Optional new description.
        status: Optional new status ('active' or 'archived').
        icon: Optional new emoji icon.
        color: Optional new accent color (hex).
        tags: Optional new list of labels.
        target_date: Optional ISO date the project points at (e.g. the wedding day), or "" to clear.
        budget: Optional total budget in NIS.
    """
    try:
        before = undoable.row("projects", "id", project_id, PROJECT_COLUMNS)
        updated = projects.update_project(
            conn.user, project_id, title=title, description=description, status=status,
            icon=icon, color=color, tags=tags, target_date=target_date, budget=budget,
        )
        if updated is None:
            return f"Error: Project `{project_id}` not found."
        after = undoable.row("projects", "id", project_id, PROJECT_COLUMNS)
        if before and after and not undoable.same(after, before, PROJECT_COLUMNS):
            label = (f"Archived “{after['title']}”" if after["status"] == "archived" != before["status"]
                     else f"Changed project “{after['title']}”")
            undoable.restore_row(label, "projects", "id", project_id, before, after, PROJECT_COLUMNS, "update_project")
        return f"Successfully updated project `{project_id}`."
    except sharing.NotOwner as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error updating project: {e}", exc_info=True)
        return f"Error updating project: {e}"


@addon.tool(is_write=True, confirm=True)
async def delete_project(conn: Connection, project_id: str) -> str:
    """Delete a project and its associated entries and tasks.

    Args:
        project_id: The ID of the project to delete.
    """
    try:
        project = projects.get_project(conn.user, project_id)
        if project is None:
            return f"Error: Project `{project_id}` not found."
        projects.delete_project(conn.user, project_id)
        return f"Successfully deleted project '{escape_markdown(project['title'])}' and its associated items."
    except sharing.NotOwner as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error deleting project: {e}", exc_info=True)
        return f"Error deleting project: {e}"


@addon.tool(is_write=True)
async def update_knowledge_item(conn: Connection, item_id: str, title: Optional[str] = None, body: Optional[str] = None,
                                kind: Optional[str] = None, fields: Optional[list] = None,
                                tags: Optional[list] = None, status: Optional[str] = None,
                                priority: Optional[str] = None, due_at: Optional[str] = None,
                                url: Optional[str] = None, section: Optional[str] = None,
                                decision: Optional[str] = None) -> str:
    """Edit an existing knowledge entry (partial update: only the arguments you provide change).
    Record a decision on an option with decision='chosen' / 'shortlist' / 'rejected' ('' resets).

    Use this to correct/extend an earlier save instead of creating a duplicate — pass the item ID
    shown when the entry was saved. To move an entry along its workflow, change status to
    'in_progress' or 'done'; to sweep it away from active views, use 'archived'. Apply the same
    authoring rules (short title, markdown body, structured fields) as in save_knowledge_item.

    Args:
        item_id: The ID of the entry to update.
        title: Optional new short title.
        body: Optional new markdown body.
        kind: Optional new kind ('note', 'record' or 'link').
        fields: Optional new structured fields list.
        tags: Optional new list of labels.
        status: Optional new status ('open', 'in_progress', 'done' or 'archived').
        priority: Optional new priority ('low', 'medium' or 'high').
        due_at: Optional new ISO date, or "" to clear.
        url: Optional new URL, or "" to clear.
        section: Optional section (title or ID) to move the entry to, or "" for none.
        decision: Optional decision: '', 'shortlist', 'chosen' or 'rejected'.
    """
    cols = (*ENTRY_COLUMNS, entries.URL_SETTER)  # an undo puts the link back with who had set it
    try:
        if decision is not None and decision not in DECISIONS:
            return "Error: decision must be '', 'shortlist', 'chosen' or 'rejected'."
        before = undoable.row("entries", "id", item_id, cols)
        current = entries.entry_row(conn.user, item_id)
        section_id = _section_id(conn.user, current.get("project_id"), section) if current else None
        updated = entries.update_entry(
            conn.user, item_id, title=title, body=body, kind=kind, fields=fields, tags=tags,
            status=status, priority=priority, due_at=due_at, url=url, section_id=section_id, decision=decision,
        )
        if updated is None:
            return f"Error: Knowledge item `{item_id}` not found."
        after = undoable.row("entries", "id", item_id, cols)
        if before and after and not undoable.same(after, before, cols):
            undoable.restore_row(f"Changed “{after['title'] or 'Untitled'}”", "entries", "id", item_id,
                                 before, after, cols, "update_knowledge_item",
                                 guard=_entry_guard(conn.user, item_id))
        return f"Successfully updated knowledge item `{item_id}`."
    except (projects.ReadOnly, outbound.Refused, entries.WrongSection, LookupError) as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error updating knowledge item: {e}", exc_info=True)
        return f"Error updating knowledge item: {e}"


@addon.tool(is_write=True, confirm=True)
async def delete_knowledge_item(conn: Connection, item_id: str) -> str:
    """Delete a specific knowledge entry.

    Args:
        item_id: The ID of the entry to delete.
    """
    try:
        if not entries.delete_entry(conn.user, item_id):
            return f"Error: Knowledge item `{item_id}` not found."
        return f"Successfully deleted knowledge item `{item_id}`."
    except projects.ReadOnly as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error deleting knowledge item: {e}", exc_info=True)
        return f"Error deleting knowledge item: {e}"


# --- Sections ---

@addon.tool(is_write=True)
async def create_section(conn: Connection, project_id: str, title: str, kind: str = "notes",
                         columns: Optional[list] = None, icon: str = "", summary: str = "") -> str:
    """Add a section to a project. Use kind='options' for a decision among alternatives (photographers,
    venues, flats, flights): the options are entries in it that share the section's `columns`, and the
    user compares them side by side and marks one chosen. Use kind='notes' for free items (ideas,
    links, reminders). Check `list_project_entries` first so you never duplicate a section.

    Args:
        project_id: The project's ID.
        title: Short name in the user's language (e.g. "צלמים", "Venues").
        kind: 'options' or 'notes'.
        columns: For 'options': the facts every option is compared on, as
            [{"name": "מחיר", "type": "money", "role": "price"}, {"name": "טלפון", "type": "phone"}, ...].
            type: text, number, money, phone, checkbox, date, url, select, rating. role "price" marks the
            column counted toward the project's budget once an option is chosen. best: "low"/"high" marks
            which value wins a comparison (money defaults to low, rating to high). 3-8 columns.
        icon: Optional emoji (e.g. "📸").
        summary: Optional Markdown write-up of the section (the TL;DR / recommendation).
    """
    try:
        section_id = sections.create_section(conn.user, project_id, title, kind=kind, columns=columns,
                                             icon=icon, summary=summary)
        if section_id is None:
            return f"Error: Project `{project_id}` not found (or the title is empty)."
        created = undoable.row("project_sections", "id", section_id, SECTION_COLUMNS)

        def _undo():
            with get_db_connection() as db:
                busy = db.execute("SELECT 1 FROM entries WHERE section_id = ?", (section_id,)).fetchone()
            if busy:
                raise undoable.UndoConflict("Things were saved into the section since, so it was kept.")
            current = undoable.row("project_sections", "id", section_id, SECTION_COLUMNS)
            if current is None:
                return
            if created and not undoable.same(current, created, SECTION_COLUMNS):
                raise undoable.UndoConflict("The section was changed since, so it was kept.")
            sections.delete_section(conn.user, section_id)

        undoable.safe_record(f"Added section “{clean_str(title)}”", _undo, "create_section")
        return f"Created section '{escape_markdown(clean_str(title))}' ({norm_section_kind(kind)}). ID: `{section_id}`."
    except projects.ReadOnly as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error creating section: {e}", exc_info=True)
        return f"Error creating section: {e}"


@addon.tool(is_write=True)
async def update_section(conn: Connection, section_id: str, title: Optional[str] = None,
                         columns: Optional[list] = None, summary: Optional[str] = None,
                         icon: Optional[str] = None, kind: Optional[str] = None) -> str:
    """Change a section (partial update). Use `summary` to write or refresh the section's Markdown
    write-up — for an options section: a short TL;DR comparison table, the recommendation, and what
    is still open — after options are added or change. Passing `columns` replaces the whole list
    (keep existing names to keep their values; a renamed column keeps its values when you pass its
    `key`, as shown in the section's columns).

    Args:
        section_id: The section's ID (from `list_project_entries`).
        title: Optional new title.
        columns: Optional full new list of columns (see `create_section`).
        summary: Optional new Markdown summary ("" clears it).
        icon: Optional new emoji.
        kind: Optional 'options' or 'notes'.
    """
    try:
        before = undoable.row("project_sections", "id", section_id, SECTION_COLUMNS)
        updated = sections.update_section(conn.user, section_id, title=title, columns=columns, summary=summary,
                                          icon=icon, kind=kind)
        if updated is None:
            return f"Error: Section `{section_id}` not found."
        after = undoable.row("project_sections", "id", section_id, SECTION_COLUMNS)
        if before and after and not undoable.same(after, before, SECTION_COLUMNS) and columns is None:
            # A column change also renames values inside entries; only plain changes are undoable.
            undoable.restore_row(f"Changed section “{after['title']}”", "project_sections", "id", section_id,
                                 before, after, SECTION_COLUMNS, "update_section")
        return f"Updated section `{section_id}`."
    except projects.ReadOnly as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error updating section: {e}", exc_info=True)
        return f"Error updating section: {e}"


@addon.tool(is_write=True, confirm=True)
async def delete_section(conn: Connection, section_id: str, with_entries: bool = False) -> str:
    """Delete a section. Its entries stay in the project without a section, unless with_entries is true.

    Args:
        section_id: The section's ID.
        with_entries: Also delete the entries (and their files) in it.
    """
    try:
        section = sections.get_section(conn.user, section_id)
        if section is None:
            return f"Error: Section `{section_id}` not found."
        sections.delete_section(conn.user, section_id, with_entries=with_entries)
        return f"Deleted section '{escape_markdown(section['title'])}'."
    except projects.ReadOnly as e:
        return _error(e)
    except Exception as e:
        logger.error(f"Error deleting section: {e}", exc_info=True)
        return f"Error deleting section: {e}"


def record_file_added(user: User, entry_id: str, saved: dict) -> None:
    """Register the undo of a file just kept on an entry (removes it again)."""
    def _undo():
        if not entries.can_edit(user, entry_id):
            raise undoable.UndoConflict("You can no longer change this entry, so it was left as it is.")
        files.delete_file(user, entry_id, saved["id"])

    undoable.safe_record(f"Saved file “{saved['name']}”", _undo, "save_file")
