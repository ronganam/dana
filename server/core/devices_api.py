"""Devices and pairing over HTTP (docs/devices.md; core/devices.py).

    POST   /api/devices/pairings {kind, name?, room?, role?, user_id?}
           → 201 {id, code, expires_at, qr, kind, name, room, role, user: {id, name}}
           ``user_id``: another user's device (``manage_users`` only); ``qr`` is
           ``dana://pair?server=<public url>&code=<code>``.
    GET    /api/devices/pairings/{id} → {id, status: pending|paired|expired, expires_at, device?}
    DELETE /api/devices/pairings/{id} → {ok}: the code stops working
    POST   /api/pair {code, name?, kind}   (public, rate-limited per client)
           → {token, device, user: {id, name}, server: {version}}
    GET    /api/devices[?user=<id>|all] → {devices: [{…, current, user_name?}],
           unpaired_tokens: [{id, user_id, label, created_at, last_used_at, current, user_name?}]}
           ``unpaired_tokens``: the same users' tokens of no device ("Signed in
           without pairing": issued by hand; never the token or its hash)
    PATCH  /api/devices/{id} {name?, room?, role?} → {device}
    DELETE /api/devices/{id} → {ok}: its token stops working at once
    DELETE /api/devices/tokens/{id} → {ok}: a token of no device stops working at once

A user sees and changes only their own devices; ``?user=`` / ``all`` and
another user's device need ``manage_users`` (someone else's device answers
404 like an unknown one). A field that is not valid: 400 ``{"error":
"invalid", "field", "detail"}``.
"""

import json
import logging
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from aiohttp import web

from core import devices, public_url, ratelimit, users
from core.auth import request_device, request_token_id, request_user
from core.devices import Device, PairingError
from core.pages_api import server_version
from core.users import User

logger = logging.getLogger(__name__)


def _invalid(field: str, detail: str) -> web.Response:
    return web.json_response({"error": "invalid", "field": field, "detail": detail}, status=400)


def _forbidden(capability: str) -> web.Response:
    return web.json_response({"error": "forbidden", "needs": capability}, status=403)


def _not_found() -> web.Response:
    return web.json_response({"error": "not_found"}, status=404)


async def _body(request: web.Request) -> Union[Dict[str, Any], web.Response]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    return body


def _text(body: Dict[str, Any], key: str) -> Optional[str]:
    """``body[key]`` as text (None when absent).

    Raises:
        ValueError: present but not text.
    """
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key, f"{key} must be text")
    return value


def pair_uri(base_url: str, code: str) -> str:
    """What a pairing's QR code holds: ``dana://pair?server=<base>&code=<code>``."""
    return "dana://pair?" + urlencode({"server": base_url, "code": code})


def _device_payload(device: Device, current: Optional[Device], with_user: bool = False) -> Dict[str, Any]:
    out = device.to_dict()
    out["current"] = current is not None and current.id == device.id
    if with_user:
        owner = users.get(device.user_id)
        out["user_name"] = owner.name if owner else ""
    return out


def _owned(request: web.Request, caller: User) -> Union[Device, web.Response]:
    device = devices.get(request.match_info["id"])
    if device is None or not devices.may_manage(caller, device):
        return _not_found()
    return device


async def start_pairing_api(request: web.Request) -> web.Response:
    """POST /api/devices/pairings"""
    caller = request_user(request)
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        kind, name, room, role, user_id = (_text(body, k) for k in ("kind", "name", "room", "role", "user_id"))
    except ValueError as e:
        return _invalid(*e.args)
    target = caller
    if user_id and user_id != caller.id:
        if not caller.level.allows("manage_users"):
            return _forbidden("manage_users")
        found = users.get(user_id)
        if found is None:
            return web.json_response({"error": "user_not_found"}, status=404)
        target = found
    try:
        pairing = devices.start_pairing(target, kind or "", by=caller, name=name or "", room=room or "",
                                        role=role or "")
    except PermissionError:
        return _forbidden("manage_users")
    except ValueError as e:
        if e.args[0] == "pairing":
            return web.json_response({"error": "too_many_pairings", "detail": e.args[1]}, status=429)
        return _invalid(*e.args)
    return web.json_response({
        "id": pairing.id, "code": pairing.code, "expires_at": pairing.expires_at,
        "qr": pair_uri(public_url.base_url(request), pairing.code), "kind": pairing.kind, "name": pairing.name,
        "room": pairing.room, "role": pairing.role, "user": {"id": target.id, "name": target.name},
    }, status=201)


async def pairing_status_api(request: web.Request) -> web.Response:
    """GET /api/devices/pairings/{id}"""
    status = devices.pairing_status(request.match_info["id"], request_user(request))
    return web.json_response(status) if status else _not_found()


async def cancel_pairing_api(request: web.Request) -> web.Response:
    """DELETE /api/devices/pairings/{id}"""
    if not devices.cancel_pairing(request.match_info["id"], request_user(request)):
        return _not_found()
    return web.json_response({"ok": True})


async def pair_api(request: web.Request) -> web.Response:
    """POST /api/pair — public: a pairing code for the new device's token."""
    key = ratelimit.client_key(request)
    wait = ratelimit.PAIRING_FAILURES.retry_after(key)
    if wait:
        return ratelimit.too_many(wait)
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        code, name, kind = (_text(body, k) for k in ("code", "name", "kind"))
    except ValueError as e:
        return _invalid(*e.args)
    if not kind or kind.strip().lower() not in devices.KINDS:
        return _invalid("kind", f"kind must be one of {', '.join(devices.KINDS)}")
    try:
        token, device, user = devices.redeem(code or "", kind=kind, name=name or "")
    except PairingError as e:
        if e.error == "wrong_kind":
            return web.json_response({"error": "wrong_kind", "expected": e.expected, "detail": str(e)}, status=409)
        ratelimit.PAIRING_FAILURES.fail(key)
        logger.warning(f"Pairing refused from {key}: wrong code")
        return web.json_response({"error": "invalid_code", "detail": str(e)}, status=403)
    return web.json_response({"token": token, "device": device.to_dict(), "user": {"id": user.id, "name": user.name},
                              "server": {"version": server_version()}})


def _token_payload(token: users.Token, current_token: Optional[str], with_user: bool = False) -> Dict[str, Any]:
    out = {"id": token.id, "user_id": token.user_id, "label": token.label, "created_at": token.created_at,
           "last_used_at": token.last_used_at, "current": current_token is not None and current_token == token.id}
    if with_user:
        owner = users.get(token.user_id)
        out["user_name"] = owner.name if owner else ""
    return out


def _listing(request: web.Request, user_id: Optional[str], with_user: bool) -> web.Response:
    current, current_token = request_device(request), request_token_id(request)
    found = devices.list_all() if user_id is None else devices.list_for(user_id)
    return web.json_response({
        "devices": [_device_payload(d, current, with_user) for d in found],
        "unpaired_tokens": [_token_payload(t, current_token, with_user) for t in devices.unpaired_tokens(user_id)]})


async def list_devices_api(request: web.Request) -> web.Response:
    """GET /api/devices[?user=<id>|all]"""
    caller = request_user(request)
    wanted = (request.query.get("user") or "").strip()
    if not wanted or wanted == caller.id:
        return _listing(request, caller.id, False)
    if not caller.level.allows("manage_users"):
        return _forbidden("manage_users")
    if wanted == "all":
        return _listing(request, None, True)
    if users.get(wanted) is None:
        return web.json_response({"error": "user_not_found"}, status=404)
    return _listing(request, wanted, True)


async def revoke_token_api(request: web.Request) -> web.Response:
    """DELETE /api/devices/tokens/{id} — a token of no device: the caller's
    own, or anyone's with ``manage_users`` (someone else's, a device's or an
    unknown id: 404)."""
    caller = request_user(request)
    token = devices.unpaired_token(request.match_info["id"])
    if token is None or not (token.user_id == caller.id or caller.level.allows("manage_users")):
        return _not_found()
    users.revoke_token(token.id)
    logger.info(f"Token {token.id} of user {token.user_id} (no device) revoked by user {caller.id}")
    return web.json_response({"ok": True})


async def update_device_api(request: web.Request) -> web.Response:
    """PATCH /api/devices/{id}"""
    caller = request_user(request)
    device = _owned(request, caller)
    if isinstance(device, web.Response):
        return device
    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    try:
        name, room, role = (_text(body, k) for k in ("name", "room", "role"))
        updated = devices.update(device, name=name, room=room, role=role)
    except ValueError as e:
        return _invalid(*e.args)
    return web.json_response({"device": _device_payload(updated, request_device(request))})


async def delete_device_api(request: web.Request) -> web.Response:
    """DELETE /api/devices/{id}"""
    caller = request_user(request)
    device = _owned(request, caller)
    if isinstance(device, web.Response):
        return device
    devices.remove(device)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    """Register the devices and pairing routes."""
    app.router.add_post("/api/devices/pairings", start_pairing_api)
    app.router.add_get("/api/devices/pairings/{id}", pairing_status_api)
    app.router.add_delete("/api/devices/pairings/{id}", cancel_pairing_api)
    app.router.add_post("/api/pair", pair_api)
    app.router.add_get("/api/devices", list_devices_api)
    app.router.add_patch("/api/devices/{id}", update_device_api)
    app.router.add_delete("/api/devices/tokens/{id}", revoke_token_api)
    app.router.add_delete("/api/devices/{id}", delete_device_api)
