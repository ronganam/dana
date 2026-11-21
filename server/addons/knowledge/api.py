"""HTTP routes of the knowledge addon: projects, entries and their media files
(docs/knowledge.md).

- ``GET|POST /api/projects``, ``PUT|DELETE /api/projects/{id}`` (owner only), ``GET /api/projects/{id}/items``
  (``{project, sections, entries, tasks}``)
- ``POST /api/projects/{id}/sections``, ``PUT|DELETE /api/sections/{id}`` (``?with_entries=1``)
- ``POST /api/entries``, ``PUT|DELETE /api/entries/{id}``, ``POST /api/entries/{id}/retry``
- ``POST /api/entries/{id}/files`` (multipart ``file`` + optional ``note``),
  ``GET|PUT|DELETE /api/entries/{id}/files/{file_id}`` — files kept on an entry
- ``GET /media/{name}`` — a stored media file of an entry the caller can see

Every route answers for the request's user, who must have ``knowledge``
connected (else 403 ``not_connected`` from the middleware).
"""

import logging
import urllib.parse
from typing import Optional

from aiohttp import web

from core import outbound
from core.auth import request_user
from core.users import User
from core.addons import sharing, store
from addons.knowledge import entries as knowledge_entries, files as knowledge_files
from addons.knowledge import projects as knowledge_projects, sections as knowledge_sections

logger = logging.getLogger(__name__)


def _knowledge_user(request: web.Request) -> User:
    """The request's user, who must have ``knowledge`` connected (else the
    middleware answers 403 ``not_connected``)."""
    user = request_user(request)
    store.require(user, knowledge_projects.ADDON)
    return user


def _knowledge_error(e: Exception) -> Optional[web.Response]:
    """The answer for a refused project / entry change, None for other errors."""
    if isinstance(e, knowledge_projects.ReadOnly):
        return web.json_response({"error": "read_only", "message": str(e)}, status=403)
    if isinstance(e, sharing.NotOwner):
        return web.json_response({"error": "owner_only", "message": str(e)}, status=403)
    if isinstance(e, outbound.Refused):
        return web.json_response({"error": "invalid", "field": "url", "message": str(e)}, status=400)
    if isinstance(e, knowledge_entries.WrongSection):
        return web.json_response({"error": "invalid", "field": "section_id", "message": str(e)}, status=400)
    if isinstance(e, knowledge_files.TooLarge):
        return web.json_response({"error": "too_large", "message": str(e)}, status=413)
    return None


async def get_projects_api(request: web.Request) -> web.Response:
    """GET /api/projects — the caller's projects and the ones shared with them."""
    user = _knowledge_user(request)
    try:
        return web.json_response(knowledge_projects.list_projects(user))
    except Exception as e:
        logger.error(f"API Error fetching projects: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_project_items_api(request: web.Request) -> web.Response:
    """GET /api/projects/{id}/items -> {project, sections, entries, tasks} (tasks: those the caller can see)"""
    user = _knowledge_user(request)
    project_id = request.match_info.get("id")
    try:
        project = knowledge_projects.get_project(user, project_id)
        if project is None:
            return web.json_response({"error": "Project not found"}, status=404)
        entries = knowledge_entries.list_entries(user, project_id)
        tasks = knowledge_projects.project_tasks(user, project_id)
        sections = knowledge_sections.list_sections(user, project_id)
        return web.json_response({"project": project, "sections": sections, "entries": entries, "tasks": tasks})
    except Exception as e:
        logger.error(f"API Error fetching project items for {project_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_project_api(request: web.Request) -> web.Response:
    """POST /api/projects"""
    user = _knowledge_user(request)
    try:
        data = await request.json()
        title = (data.get("title") or "").strip()
        if not title:
            return web.json_response({"error": "Title is required"}, status=400)

        project_id = knowledge_projects.create_project(
            user,
            title=title,
            description=data.get("description"),
            icon=data.get("icon"),
            color=data.get("color"),
            tags=data.get("tags"),
            target_date=data.get("target_date"),
            budget=data.get("budget"),
        )
        return web.json_response({"status": "created", "id": project_id})
    except Exception as e:
        logger.error(f"API Error creating project: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_project_api(request: web.Request) -> web.Response:
    """PUT /api/projects/{id} (owner only)"""
    user = _knowledge_user(request)
    project_id = request.match_info.get("id")
    try:
        data = await request.json()
        updated = knowledge_projects.update_project(
            user,
            project_id,
            title=data.get("title"),
            description=data.get("description"),
            status=data.get("status"),
            icon=data.get("icon"),
            color=data.get("color"),
            tags=data.get("tags"),
            target_date=data.get("target_date"),
            budget=data.get("budget"),
        )
        if updated is None:
            return web.json_response({"error": "Project not found"}, status=404)
        return web.json_response({"status": "updated", "id": project_id, "project": updated})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error updating project {project_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_project_api(request: web.Request) -> web.Response:
    """DELETE /api/projects/{id} (owner only)"""
    user = _knowledge_user(request)
    project_id = request.match_info.get("id")
    try:
        if not knowledge_projects.delete_project(user, project_id):
            return web.json_response({"error": "Project not found"}, status=404)
        return web.json_response({"status": "deleted", "id": project_id})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error deleting project {project_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_entry_api(request: web.Request) -> web.Response:
    """POST /api/entries"""
    user = _knowledge_user(request)
    try:
        data = await request.json()
        project_id = data.get("project_id")
        if not project_id:
            return web.json_response({"error": "project_id is required"}, status=400)

        entry_id = await knowledge_entries.create_entry(
            user,
            project_id,
            kind=data.get("kind", "note"),
            title=data.get("title", ""),
            body=data.get("body", ""),
            fields=data.get("fields"),
            tags=data.get("tags"),
            status=data.get("status", "open"),
            priority=data.get("priority", "medium"),
            due_at=data.get("due_at"),
            url=data.get("url"),
            section_id=data.get("section_id"),
            decision=data.get("decision", ""),
        )
        if entry_id is None:
            return web.json_response({"error": "Project not found"}, status=404)
        return web.json_response({"status": "created", "id": entry_id,
                                  "entry": knowledge_entries.get_entry(user, entry_id)})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error creating entry: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_entry_api(request: web.Request) -> web.Response:
    """PUT /api/entries/{id} (partial update; only provided fields change)."""
    user = _knowledge_user(request)
    entry_id = request.match_info.get("id")
    try:
        data = await request.json()
        editable = ("kind", "title", "body", "fields", "tags", "status", "priority", "due_at", "url", "section_id",
                    "decision")
        changes = {key: value for key, value in data.items() if key in editable}
        updated = knowledge_entries.update_entry(user, entry_id, **changes)
        if updated is None:
            return web.json_response({"error": "Entry not found"}, status=404)
        return web.json_response({"status": "updated", "id": entry_id, "entry": updated})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error updating entry {entry_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_entry_api(request: web.Request) -> web.Response:
    """DELETE /api/entries/{id}"""
    user = _knowledge_user(request)
    entry_id = request.match_info.get("id")
    try:
        if not knowledge_entries.delete_entry(user, entry_id):
            return web.json_response({"error": "Entry not found"}, status=404)
        return web.json_response({"status": "deleted", "id": entry_id})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error deleting entry {entry_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def retry_entry_api(request: web.Request) -> web.Response:
    """POST /api/entries/{id}/retry"""
    user = _knowledge_user(request)
    entry_id = request.match_info.get("id")
    try:
        entry = knowledge_entries.writable_entry(user, entry_id)
        if not entry.get("url"):
            return web.json_response({"error": "Entry has no URL to retry"}, status=400)
        knowledge_entries.retry_media(user, entry_id)
        return web.json_response({"status": "retrying", "id": entry_id})
    except LookupError:
        return web.json_response({"error": "Entry not found"}, status=404)
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error retrying entry {entry_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def create_section_api(request: web.Request) -> web.Response:
    """POST /api/projects/{id}/sections {title, kind, columns, icon, summary} → {status, id, section}"""
    user = _knowledge_user(request)
    project_id = request.match_info.get("id", "")
    try:
        data = await request.json()
        if not (data.get("title") or "").strip():
            return web.json_response({"error": "Title is required"}, status=400)
        section_id = knowledge_sections.create_section(
            user, project_id, data.get("title", ""), kind=data.get("kind", "notes"), columns=data.get("columns"),
            icon=data.get("icon", ""), summary=data.get("summary", ""))
        if section_id is None:
            return web.json_response({"error": "Project not found"}, status=404)
        return web.json_response({"status": "created", "id": section_id,
                                  "section": knowledge_sections.get_section(user, section_id)})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error creating a section in {project_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def update_section_api(request: web.Request) -> web.Response:
    """PUT /api/sections/{id} (partial) → {status, id, section}"""
    user = _knowledge_user(request)
    section_id = request.match_info.get("id", "")
    try:
        data = await request.json()
        editable = ("title", "icon", "kind", "columns", "summary", "position")
        updated = knowledge_sections.update_section(user, section_id,
                                                    **{k: v for k, v in data.items() if k in editable})
        if updated is None:
            return web.json_response({"error": "Section not found"}, status=404)
        return web.json_response({"status": "updated", "id": section_id, "section": updated})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error updating section {section_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_section_api(request: web.Request) -> web.Response:
    """DELETE /api/sections/{id}[?with_entries=1] — its entries stay loose unless with_entries."""
    user = _knowledge_user(request)
    section_id = request.match_info.get("id", "")
    try:
        with_entries = request.query.get("with_entries") in ("1", "true")
        if not knowledge_sections.delete_section(user, section_id, with_entries=with_entries):
            return web.json_response({"error": "Section not found"}, status=404)
        return web.json_response({"status": "deleted", "id": section_id})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error deleting section {section_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def upload_file_api(request: web.Request) -> web.Response:
    """POST /api/entries/{id}/files (multipart: ``file``, optional ``note``) → {status, file}"""
    user = _knowledge_user(request)
    entry_id = request.match_info.get("id", "")
    try:
        knowledge_entries.writable_entry(user, entry_id)
        reader = await request.multipart()
        data, name, mime, note = None, "", "", ""
        async for part in reader:
            if part.name == "file":
                # RFC 5987 clients send filename*=UTF-8''<percent-encoded>; browsers send raw UTF-8.
                name, mime = urllib.parse.unquote(part.filename or ""), part.headers.get("Content-Type", "")
                chunks, size = [], 0
                while chunk := await part.read_chunk():
                    size += len(chunk)
                    if size > knowledge_files.MAX_BYTES:
                        raise knowledge_files.TooLarge()
                    chunks.append(chunk)
                data = b"".join(chunks)
            elif part.name == "note":
                note = (await part.text())[:500]
        if not data:
            return web.json_response({"error": "No file"}, status=400)
        saved = knowledge_files.add_file(user, entry_id, name, mime, data, note=note)
        if saved is None:
            return web.json_response({"error": "Entry not found"}, status=404)
        return web.json_response({"status": "created", "file": saved})
    except LookupError:
        return web.json_response({"error": "Entry not found"}, status=404)
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error uploading a file to {entry_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def get_file_api(request: web.Request) -> web.StreamResponse:
    """GET /api/entries/{id}/files/{file_id}[?download=1] — inline (Range works), 404 unless the entry is visible."""
    user = _knowledge_user(request)
    entry_id, file_id = request.match_info.get("id", ""), request.match_info.get("file_id", "")
    path = knowledge_files.file_path(user, entry_id, file_id)
    row = knowledge_files.file_row(user, entry_id, file_id) if path else None
    if path is None or row is None:
        raise web.HTTPNotFound()
    disposition = "attachment" if request.query.get("download") in ("1", "true") else "inline"
    quoted = urllib.parse.quote(row["name"])
    return web.FileResponse(path, headers={
        "Content-Type": row["mime"],
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quoted}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600",
    })


async def update_file_api(request: web.Request) -> web.Response:
    """PUT /api/entries/{id}/files/{file_id} {name?, note?}"""
    user = _knowledge_user(request)
    entry_id, file_id = request.match_info.get("id", ""), request.match_info.get("file_id", "")
    try:
        data = await request.json()
        saved = knowledge_files.update_file(user, entry_id, file_id, name=data.get("name"), note=data.get("note"))
        if saved is None:
            return web.json_response({"error": "File not found"}, status=404)
        return web.json_response({"status": "updated", "file": saved})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error updating file {file_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def delete_file_api(request: web.Request) -> web.Response:
    """DELETE /api/entries/{id}/files/{file_id}"""
    user = _knowledge_user(request)
    entry_id, file_id = request.match_info.get("id", ""), request.match_info.get("file_id", "")
    try:
        if not knowledge_files.delete_file(user, entry_id, file_id):
            return web.json_response({"error": "File not found"}, status=404)
        return web.json_response({"status": "deleted", "id": file_id})
    except Exception as e:
        refused = _knowledge_error(e)
        if refused is not None:
            return refused
        logger.error(f"API Error deleting file {file_id}: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


async def media_file_api(request: web.Request) -> web.StreamResponse:
    """GET /media/{name} — a stored media file of an entry the caller can see (else 404)."""
    user = _knowledge_user(request)
    path = knowledge_entries.media_file(user, request.match_info.get("name", ""))
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def setup(app: web.Application) -> None:
    """Register the projects, entries and media routes."""
    app.router.add_get("/api/projects", get_projects_api)
    app.router.add_post("/api/projects", create_project_api)
    app.router.add_put("/api/projects/{id}", update_project_api)
    app.router.add_delete("/api/projects/{id}", delete_project_api)
    app.router.add_get("/api/projects/{id}/items", get_project_items_api)
    app.router.add_post("/api/entries", create_entry_api)
    app.router.add_put("/api/entries/{id}", update_entry_api)
    app.router.add_delete("/api/entries/{id}", delete_entry_api)
    app.router.add_post("/api/entries/{id}/retry", retry_entry_api)
    app.router.add_post("/api/projects/{id}/sections", create_section_api)
    app.router.add_put("/api/sections/{id}", update_section_api)
    app.router.add_delete("/api/sections/{id}", delete_section_api)
    app.router.add_post("/api/entries/{id}/files", upload_file_api)
    app.router.add_get("/api/entries/{id}/files/{file_id}", get_file_api)
    app.router.add_put("/api/entries/{id}/files/{file_id}", update_file_api)
    app.router.add_delete("/api/entries/{id}/files/{file_id}", delete_file_api)
    app.router.add_get("/media/{name}", media_file_api)
