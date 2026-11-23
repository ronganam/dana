"""Backups over HTTP (docs/backups.md; core/backups.py). ``manage_server`` only:
an archive holds everyone's data and the credentials key.

    GET  /api/backups              → {schedule {every, at, day}, destination, default_destination, keep,
                                      next_at, running, last, last_ok, backups: [{name, size, created_at}],
                                      credentials_key: file | env | none, timezone}
    PUT  /api/backups {schedule?, destination?, keep?}
                                   → the same; 400 ``invalid`` {field, detail} (nothing stored)
    POST /api/backups/run          → back up into the destination now, answered when done:
                                      {backup {name, size, created_at}, status}; 409 ``busy``;
                                      500 ``backup_failed`` {detail} (recorded: the self-check shows it)
    GET  /api/backups/download     → a fresh archive streamed as an attachment (built under
                                      ``<data dir>/.backup-work``, deleted once sent); 409 ``busy``
    GET  /api/backups/files/{name} → one archive of the destination; 404 for any other name
"""

import asyncio
import json
import logging
import os

from aiohttp import web

from core import backups
from core.auth import request_user, requires

logger = logging.getLogger(__name__)

CHUNK = 1 << 20


def _busy() -> web.Response:
    return web.json_response({"error": "busy", "detail": "A backup is already being made."}, status=409)


@requires("manage_server")
async def get_backups_api(request: web.Request) -> web.Response:
    """GET /api/backups"""
    return web.json_response(await asyncio.to_thread(backups.status))


@requires("manage_server")
async def put_backups_api(request: web.Request) -> web.Response:
    """PUT /api/backups"""
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "field": "", "detail": "A JSON object is expected."}, status=400)
    try:
        await asyncio.to_thread(backups.save_settings, body, by=request_user(request).id)
    except backups.Invalid as e:
        return web.json_response({"error": "invalid", "field": e.field, "detail": e.detail}, status=400)
    return web.json_response(await asyncio.to_thread(backups.status))


@requires("manage_server")
async def run_backup_api(request: web.Request) -> web.Response:
    """POST /api/backups/run"""
    try:
        backup = await asyncio.to_thread(backups.back_up, "manual")
    except backups.Busy:
        return _busy()
    except Exception:
        st = await asyncio.to_thread(backups.state)
        detail = (st["last"] or {}).get("error") or "The backup failed."
        return web.json_response({"error": "backup_failed", "detail": detail}, status=500)
    return web.json_response({"backup": backup.to_json(), "status": await asyncio.to_thread(backups.status)})


async def _send(request: web.Request, path: os.PathLike, name: str, *, delete: bool) -> web.StreamResponse:
    """Stream the file at ``path`` as an attachment; with ``delete`` it is removed first (the open
    file is still read to the end) so nothing is left behind even when the browser hangs up."""
    f = open(path, "rb")
    try:
        size = os.fstat(f.fileno()).st_size
        if delete:
            os.unlink(path)
        resp = web.StreamResponse(headers={"Content-Type": "application/gzip",
                                           "Content-Disposition": f'attachment; filename="{name}"',
                                           "Cache-Control": "no-store"})
        resp.content_length = size
        await resp.prepare(request)
        while True:
            chunk = await asyncio.to_thread(f.read, CHUNK)
            if not chunk:
                break
            await resp.write(chunk)
        await resp.write_eof()
        return resp
    finally:
        f.close()


@requires("manage_server")
async def download_backup_api(request: web.Request) -> web.StreamResponse:
    """GET /api/backups/download"""
    try:
        path = await asyncio.to_thread(backups.build_download)
    except backups.Busy:
        return _busy()
    logger.info(f"Backup download {path.name} for user {request_user(request).id}")
    return await _send(request, path, path.name, delete=True)


@requires("manage_server")
async def get_backup_file_api(request: web.Request) -> web.StreamResponse:
    """GET /api/backups/files/{name}"""
    name = request.match_info["name"]
    path = await asyncio.to_thread(backups.find, name)
    if path is None:
        return web.json_response({"error": "not_found"}, status=404)
    return await _send(request, path, name, delete=False)


def setup(app: web.Application) -> None:
    """Register the backup routes."""
    app.router.add_get("/api/backups", get_backups_api)
    app.router.add_put("/api/backups", put_backups_api)
    app.router.add_post("/api/backups/run", run_backup_api)
    app.router.add_get("/api/backups/download", download_backup_api)
    app.router.add_get("/api/backups/files/{name}", get_backup_file_api)
