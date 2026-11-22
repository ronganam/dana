"""The dashboards of tablets and TVs (docs/dashboard.md; the rules: core/dashboard.py).

- ``GET /dashboard?device=<id>`` — the page that draws a dashboard (no ``device``: the requesting tablet or
  TV's own, else the page lists the user's tablets and TVs); its CSS / JS versioned by the web release
- ``GET /api/dashboard?device=<id>`` — the layout and every widget's data (no ``device``: the requesting tablet
  or TV's own; anything else 400 ``device_required``)
- ``GET /api/dashboard/widget?device=<id>&id=<widget id>`` — one widget's data again (403 ``not_connected``
  for a widget of an addon the device's owner does not have)
- ``GET /api/dashboard/catalog?device=<id>`` — the presets and the widgets the device's owner can place
- ``PUT /api/dashboard?device=<id>`` ``{preset, widgets: [...] | null}`` — choose a preset / save the edits

A device id the caller may not read answers 404 ``not_found``, like a missing one.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from aiohttp import web

from core import dashboard, pages_api, users
from core.addons.hooks import WIDGET_SIZES
from core.auth import request_device, request_user
from core.devices import Device

logger = logging.getLogger(__name__)

_PAGE = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"
_NOT_FOUND = {"error": "not_found", "message": "No such dashboard"}


def _not_found() -> web.Response:
    return web.json_response(_NOT_FOUND, status=404)


def _device(request: web.Request) -> Union[Device, web.Response]:
    """The device named by ``?device=`` (or the requesting tablet / TV), when the caller may read its dashboard."""
    user, requester = request_user(request), request_device(request)
    wanted = request.query.get("device", "").strip()
    if not wanted:
        if requester is not None and dashboard.has_dashboard(requester):
            wanted = requester.id
        else:
            return web.json_response({"error": "device_required",
                                      "message": "Name the tablet or TV: ?device=<id>"}, status=400)
    device = dashboard.readable(user, requester, wanted)
    return device if device is not None else _not_found()


async def dashboard_page(request: web.Request) -> web.StreamResponse:
    """GET /dashboard — the renderer page (no-cache; its own files versioned, so a kiosk WebView gets each release)."""
    wanted = request.query.get("device", "").strip()
    if wanted and dashboard.readable(request_user(request), request_device(request), wanted) is None:
        return web.Response(text="No such dashboard", status=404)
    html = _PAGE.read_text(encoding="utf-8")
    release = pages_api.web_release()
    for asset in ("/static/dashboard.css", "/static/dashboard.js", "/static/dana.js"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={release}"')
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def dashboard_api(request: web.Request) -> web.Response:
    """GET /api/dashboard — the layout and its data."""
    device = _device(request)
    if isinstance(device, web.Response):
        return device
    user = dashboard.data_user(request_user(request), request_device(request), device)
    return web.json_response(await dashboard.payload(device, user, pages_api.web_release()))


async def widget_api(request: web.Request) -> web.Response:
    """GET /api/dashboard/widget?id= — one placed widget's data."""
    device = _device(request)
    if isinstance(device, web.Response):
        return device
    placed = dashboard.placed(device, request.query.get("id", ""))
    if placed is None:
        return web.json_response({"error": "not_found", "message": "No such widget on this dashboard"}, status=404)
    user = dashboard.data_user(request_user(request), request_device(request), device)
    if user is not None:
        try:
            dashboard.require_widget(user, placed["widget"])   # NotConnected → 403 not_connected (core/auth.py)
        except LookupError:
            pass   # no enabled addon declares it: widget_data says "unavailable"
    return web.json_response({"id": placed["id"], **await dashboard.widget_data(user, placed)})


async def catalog_api(request: web.Request) -> web.Response:
    """GET /api/dashboard/catalog — what the editor offers for this device."""
    device = _device(request)
    if isinstance(device, web.Response):
        return device
    owner = users.get(device.user_id)
    return web.json_response({"presets": dashboard.presets_payload(),
                              "widgets": dashboard.catalog(owner) if owner is not None else [],
                              "sizes": list(WIDGET_SIZES),
                              "default_preset": dashboard.default_preset(device)})


async def save_api(request: web.Request) -> web.Response:
    """PUT /api/dashboard — ``{preset, widgets}``: choose a preset (``widgets`` null) or save the edits."""
    device = _device(request)
    if isinstance(device, web.Response):
        return device
    user = request_user(request)
    if not dashboard.editable(user, request_device(request), device):
        return web.json_response({"error": "forbidden", "message": "A tablet or TV cannot change its dashboard"},
                                 status=403)
    try:
        body: Any = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "message": "Send {preset, widgets}"}, status=400)
    widgets: Optional[Any] = body.get("widgets")
    try:
        dashboard.save(device, user, body.get("preset"), widgets)
    except dashboard.Invalid as e:
        return web.json_response({"error": "invalid", "message": str(e)}, status=400)
    out: Dict[str, Any] = dashboard.layout_payload(device, pages_api.web_release())
    return web.json_response(out)


def setup(app: web.Application) -> None:
    """Register the dashboard page and its API."""
    r = app.router
    r.add_get("/dashboard", dashboard_page)
    r.add_get("/api/dashboard", dashboard_api)
    r.add_put("/api/dashboard", save_api)
    r.add_get("/api/dashboard/widget", widget_api)
    r.add_get("/api/dashboard/catalog", catalog_api)
