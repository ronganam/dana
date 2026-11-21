"""Who the caller is, and their addons (docs/access.md, "Managing users").

    GET    /api/me                       → {id, name, access, capabilities: [..], kinds: [..]} (``kinds``: the addons they have)
    GET    /api/users                    → {users: [{id, name, access}]} (``manage_users`` only)
    GET    /api/addons                   → {user_id, addons: [<addon>, ...]}
    GET    /api/addons/{name}            → <addon>
    PUT    /api/addons/{name}            {credentials?: {name: str|null}, settings?: {name: value|null}}
                                         → <addon> (connects it, or updates it)
    DELETE /api/addons/{name}            → <addon> (connected: false) + disconnected: bool
    POST   /api/addons/{name}/test       → {name, checked, ok, reason}
    GET    /api/addons/{name}/options    → {name, settings: {name: [{value, label}]}}

``<addon>`` = ``{name, description, connected, requires, can_change,
why_not, credentials: [{name, description, required, set, type, secret,
value?, can_change, why_not, rule}], settings: [{name, description, default,
value, type, can_change, why_not, rule}], account, check, options, connect_url, connect_label}``:
credential values are write-only (only whether each is ``set``) except an
address (``secret: false``: its ``value`` is returned, to the connection's own
user and to an admin acting with ``?user=``; ``Field.secret``); ``check`` /
``options`` say whether the addon has a connection check / setting
candidates; ``connect_url`` (else null) is the path that connects the addon by
a sign-in at the service (the Google addons: one consent for all of them),
only for the caller's own addons. ``can_change`` / ``why_not`` (addon and field) say in advance
whether the caller may change it for this user and, when not, why in a short
phrase ("Set by an admin"); ``rule`` is one line on what a field's value may
be. Both come from ``store.addon_rule`` / ``store.field_rule``, which the
store's own checks go through. Only while the account the connection's
service reports is held by another user's connection (``whatsapp``),
``account_conflict`` carries that account (and ``account`` is null); only
while a stored address / name is not valid (saved before the rules),
``problem`` = ``{field, message}``.

Every addon the user may connect is listed (not ``essentials``, which everyone has;
``ops`` only for a level with the ``ops`` capability: anyone else gets 403
``forbidden`` for it). ``PUT`` merges: credentials given replace the stored
ones of the same name (``null`` removes an optional one), settings given
replace theirs (``null`` = back to the default); a bad body, an unknown or
missing field answers 400 ``{"error": "invalid", "field", "message"}``.
Changes take effect at once (the store's cache, the addons' ``on_connect`` /
``on_credentials_change`` / ``on_change``).

``?user=<id>`` acts on another user: only with the ``manage_users``
capability (else 403 ``forbidden``); an unknown id answers 404. The caller is
who makes the change (``store.connect / update / disconnect(by=)``): an addon
with ``change_requires`` (``cameras``) needs it (403 ``forbidden``), address
and Telegram chat fields follow docs/access.md → "Security rules" (400
``invalid`` naming the field); ``test`` / ``options`` refuse a stored address
that is not valid (400) without sending anything. ``GET /api/users`` lists
the users such a caller can pick (id, name, access level only). 503
``credentials_key`` when stored credentials cannot be read or written.
Loaded by core/server.py (``setup(app)``). The web page: static/addons.js.
"""

import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from aiohttp import web

from core import users
from core.access import CAPABILITIES
from core.auth import request_user, requires
from core.users import User
from core.addons import crypto, store
from core.addons.addon import Addon, InvalidValue, get_addon, all_addons

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 20.0
OPTIONS_TIMEOUT_SECONDS = 20.0


class Invalid(ValueError):
    """A request body the endpoint refuses (400), naming the field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _invalid(error: Invalid) -> web.Response:
    return web.json_response({"error": "invalid", "field": error.field, "message": str(error)}, status=400)


def _forbidden(capability: str) -> web.Response:
    return web.json_response({"error": "forbidden", "needs": capability}, status=403)


def me_payload(user: User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "access": user.access,
        "capabilities": [c for c in CAPABILITIES if user.level.allows(c)],
        "kinds": list(store.connected_addons(user)),
    }


def _rule_fields(rule: store.ChangeRule) -> Dict[str, Any]:
    return {"can_change": rule.can_change, "why_not": rule.why_not, "rule": rule.rule}


def addon_payload(user: User, addon: Addon, by: User) -> Dict[str, Any]:
    """One addon as ``user``'s, seen by ``by`` (the caller: ``user`` themselves
    or an admin acting for them): never a secret credential's value.

    Raises:
        CredentialsKeyError: the stored credentials cannot be decrypted.
    """
    conn = store.get(user, addon.name)
    creds: Mapping[str, str] = conn.credentials if conn is not None else {}
    values: Mapping[str, Any] = conn.settings if conn is not None else addon.setting_defaults()
    credentials: List[Dict[str, Any]] = []
    for f in addon.credentials:
        item: Dict[str, Any] = {"name": f.name, "description": f.description, "required": f.required,
                                "set": bool(creds.get(f.name)), "type": f.type, "secret": f.secret}
        if not f.secret:
            item["value"] = creds.get(f.name) or None
        credentials.append({**item, **_rule_fields(store.field_rule(addon, user, f.name, by))})
    addon_rule = store.addon_rule(addon, by)
    out = {
        "name": addon.name,
        "description": addon.description,
        "connected": conn is not None,
        "requires": addon.requires,
        "can_change": addon_rule.can_change,
        "why_not": addon_rule.why_not,
        "credentials": credentials,
        "settings": [{"name": f.name, "description": f.description, "default": f.default,
                      "value": values.get(f.name), "type": f.type,
                      **_rule_fields(store.field_rule(addon, user, f.name, by))} for f in addon.settings],
        "account": (conn.account or None) if conn is not None else None,
        "check": addon.test is not None,
        "options": addon.options is not None,
        # The sign-in is the caller's own: not offered to an admin acting for someone else.
        "connect_url": addon.connect_url if by.id == user.id else None,
        "connect_label": addon.connect_label or None,
    }
    if conn is not None and conn.account_conflict:
        # The account its service reports is held by another user's connection (whatsapp).
        out["account_conflict"] = conn.account_conflict
    problem = store.stored_value_problem(conn) if conn is not None else None
    if problem is not None:
        out["problem"] = {"field": problem[0], "message": problem[1]}
    return out


def _key_error(error: crypto.CredentialsKeyError, what: str) -> web.Response:
    logger.error(f"{what} failed: {error}")
    return web.json_response({"error": "credentials_key", "message": str(error)}, status=503)


def _target(request: web.Request) -> Union[User, web.Response]:
    """The user the request acts on: the caller, or ``?user=<id>`` for a
    caller with ``manage_users``."""
    caller = request_user(request)
    wanted = (request.query.get("user") or "").strip()
    if not wanted or wanted == caller.id:
        return caller
    if not caller.level.allows("manage_users"):
        return _forbidden("manage_users")
    target = users.get(wanted)
    if target is None:
        return web.json_response({"error": "user_not_found"}, status=404)
    return target


def _addon_for(user: User, name: str) -> Union[Addon, web.Response]:
    """Addon ``name`` when ``user`` may connect it (404 unknown / ``essentials``, 403 their level)."""
    try:
        addon = get_addon(name)
    except KeyError:
        return web.json_response({"error": "unknown_addon", "addon": name}, status=404)
    if addon.everyone:
        return web.json_response({"error": "unknown_addon", "addon": name}, status=404)
    if not addon.allowed_for(user):
        return _forbidden(str(addon.requires))
    return addon


def _resolve(request: web.Request) -> Union[Tuple[User, Addon], web.Response]:
    target = _target(request)
    if isinstance(target, web.Response):
        return target
    addon = _addon_for(target, request.match_info["name"])
    if isinstance(addon, web.Response):
        return addon
    return target, addon


def parse_changes(addon: Addon, body: Any, *, connecting: bool
                  ) -> Tuple[Dict[str, Optional[str]], Dict[str, Any]]:
    """``(credentials, settings)`` from a ``PUT`` body.

    Raises:
        Invalid: not an object, an unknown name, a credential that is not a
            string, a required credential missing (connecting) or emptied.
    """
    if not isinstance(body, dict):
        raise Invalid("body", "The body must be a JSON object with credentials and / or settings.")
    unknown = sorted(set(body) - {"credentials", "settings"})
    if unknown:
        raise Invalid(unknown[0], f"Unknown field {unknown[0]!r}: only credentials and settings can be set.")
    raw_creds = body.get("credentials") or {}
    raw_settings = body.get("settings") or {}
    for field, value in (("credentials", raw_creds), ("settings", raw_settings)):
        if not isinstance(value, dict):
            raise Invalid(field, f"{field} must be an object.")
    declared_creds = {f.name: f for f in addon.credentials}
    declared_settings = {f.name for f in addon.settings}
    credentials: Dict[str, Optional[str]] = {}
    for name, value in raw_creds.items():
        f = declared_creds.get(name)
        if f is None:
            raise Invalid(name, f"The {addon.name} addon has no credential {name!r} "
                                f"(known: {', '.join(declared_creds) or 'none'}).")
        if value is not None and not isinstance(value, str):
            raise Invalid(name, f"Credential {name!r} must be a string.")
        value = value.strip() if isinstance(value, str) else None
        if f.required and not value:
            raise Invalid(name, f"Credential {name!r} is required and cannot be empty.")
        credentials[name] = value or None
    for name in raw_settings:
        if name not in declared_settings:
            raise Invalid(name, f"The {addon.name} addon has no setting {name!r} "
                                f"(known: {', '.join(sorted(declared_settings)) or 'none'}).")
    if connecting:
        missing = [f.name for f in addon.credentials if f.required and not credentials.get(f.name)]
        if missing:
            raise Invalid(missing[0], f"Connecting {addon.name} needs the credential {missing[0]!r}.")
    return credentials, dict(raw_settings)


# --- Handlers -------------------------------------------------------------------------------------

async def me_api(request: web.Request) -> web.Response:
    """GET /api/me"""
    return web.json_response(me_payload(request_user(request)))


@requires("manage_users")
async def users_api(request: web.Request) -> web.Response:
    """GET /api/users: who an admin can pick on the Addons page (``?user=``)."""
    return web.json_response({"users": [{"id": u.id, "name": u.name, "access": u.access}
                                        for u in users.list_users()]})


async def list_addons_api(request: web.Request) -> web.Response:
    """GET /api/addons"""
    target = _target(request)
    if isinstance(target, web.Response):
        return target
    caller = request_user(request)
    try:
        items: List[Dict[str, Any]] = [addon_payload(target, k, caller) for k in all_addons()
                                       if not k.everyone and k.allowed_for(target)]
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Listing the addons of user {target.id}")
    return web.json_response({"user_id": target.id, "addons": items})


async def get_addon_api(request: web.Request) -> web.Response:
    """GET /api/addons/{name}"""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, addon = resolved
    try:
        return web.json_response(addon_payload(target, addon, request_user(request)))
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Reading {addon.name} for user {target.id}")


async def put_addon_api(request: web.Request) -> web.Response:
    """PUT /api/addons/{name}: connect it, or update it."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, addon = resolved
    try:
        body = await request.json()
    except Exception:
        return _invalid(Invalid("body", "The body must be JSON."))
    connecting = not store.is_connected(target, addon.name)
    caller = request_user(request)
    try:
        credentials, settings = parse_changes(addon, body, connecting=connecting)
        with users.acting_as(target):
            if connecting:
                store.connect(target, addon.name,
                              credentials={k: v for k, v in credentials.items() if v is not None},
                              settings={k: v for k, v in settings.items() if v is not None}, by=caller)
            else:
                store.update(target, addon.name, credentials=credentials or None, settings=settings or None,
                             by=caller)
    except (Invalid, InvalidValue) as e:
        return _invalid(Invalid(e.field, str(e)))
    except store.Forbidden as e:
        return _forbidden(e.needs)
    except PermissionError:
        return _forbidden(str(addon.requires))
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Saving {addon.name} for user {target.id}")
    logger.info(f"{'Connected' if connecting else 'Updated'} {addon.name} for user {target.id} "
                f"(by {caller.id})")
    return web.json_response(addon_payload(target, addon, caller), status=201 if connecting else 200)


async def delete_addon_api(request: web.Request) -> web.Response:
    """DELETE /api/addons/{name}"""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, addon = resolved
    caller = request_user(request)
    try:
        with users.acting_as(target):
            gone = store.disconnect(target, addon.name, by=caller)
    except store.Forbidden as e:
        return _forbidden(e.needs)
    return web.json_response({**addon_payload(target, addon, caller), "disconnected": gone})


async def test_addon_api(request: web.Request) -> web.Response:
    """POST /api/addons/{name}/test: the addon's connection check on the stored connection."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, addon = resolved
    conn = store.require(target, addon.name)  # NotConnected → 403 not_connected (middleware)
    problem = store.stored_value_problem(conn)
    if problem is not None:  # only ever against a stored, valid address (docs/access.md, "Security rules")
        return _invalid(Invalid(problem[0], f"{problem[1]} Set it again before testing."))
    if addon.test is None:
        return web.json_response({"name": addon.name, "checked": False, "ok": None,
                                  "reason": "This addon has no connection check."})
    try:
        with users.acting_as(target):
            reason = await asyncio.wait_for(addon.test(conn), CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        reason = f"No answer within {CHECK_TIMEOUT_SECONDS:.0f} seconds."
    except Exception as e:
        logger.warning(f"Connection check of {addon.name} for user {target.id} failed: {type(e).__name__}")
        reason = f"The check failed ({type(e).__name__})."
    return web.json_response({"name": addon.name, "checked": True, "ok": reason is None,
                              "reason": reason or ""})


async def addon_options_api(request: web.Request) -> web.Response:
    """GET /api/addons/{name}/options: setting candidates from the user's own service."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, addon = resolved
    if addon.options is None:
        return web.json_response({"error": "no_options", "addon": addon.name}, status=404)
    conn = store.require(target, addon.name)
    problem = store.stored_value_problem(conn)
    if problem is not None:
        return _invalid(Invalid(problem[0], f"{problem[1]} Set it again first."))
    try:
        with users.acting_as(target):
            found = await asyncio.wait_for(addon.options(conn), OPTIONS_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Setting options of {addon.name} for user {target.id} failed: {type(e).__name__}")
        return web.json_response({"error": "unavailable", "addon": addon.name,
                                  "message": f"Could not read the options ({type(e).__name__})."}, status=502)
    return web.json_response({"name": addon.name, "settings": found})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/me", me_api)
    r.add_get("/api/users", users_api)
    r.add_get("/api/addons", list_addons_api)
    r.add_get("/api/addons/{name}", get_addon_api)
    r.add_put("/api/addons/{name}", put_addon_api)
    r.add_delete("/api/addons/{name}", delete_addon_api)
    r.add_post("/api/addons/{name}/test", test_addon_api)
    r.add_get("/api/addons/{name}/options", addon_options_api)
