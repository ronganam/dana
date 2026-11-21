"""Who the caller is, and their integrations (docs/access.md, "Managing users").

    GET    /api/me                               → {id, name, access, capabilities: [..], kinds: [..]}
    GET    /api/users                            → {users: [{id, name, access}]} (``manage_users`` only)
    GET    /api/integrations                     → {user_id, integrations: [<integration>, ...]}
    GET    /api/integrations/{kind}              → <integration>
    PUT    /api/integrations/{kind}              {credentials?: {name: str|null}, settings?: {name: value|null}}
                                                 → <integration> (connects it, or updates it)
    DELETE /api/integrations/{kind}              → <integration> (connected: false) + disconnected: bool
    POST   /api/integrations/{kind}/test         → {kind, checked, ok, reason}
    GET    /api/integrations/{kind}/options      → {kind, settings: {name: [{value, label}]}}

``<integration>`` = ``{kind, description, connected, requires, can_change,
why_not, credentials: [{name, description, required, set, type, secret,
value?, can_change, why_not, rule}], settings: [{name, description, default,
value, type, can_change, why_not, rule}], account, check, options, connect_url, connect_label}``:
credential values are write-only (only whether each is ``set``) except an
address (``secret: false``: its ``value`` is returned, to the connection's own
user and to an admin acting with ``?user=``; ``Field.secret``); ``check`` /
``options`` say whether the kind has a connection check / setting
candidates; ``connect_url`` (else null) is the path that connects the kind by
a sign-in at the service (the Google kinds: one consent for all of them),
only for the caller's own integrations. ``can_change`` / ``why_not`` (kind and field) say in advance
whether the caller may change it for this user and, when not, why in a short
phrase ("Set by an admin"); ``rule`` is one line on what a field's value may
be. Both come from ``store.kind_rule`` / ``store.field_rule``, which the
store's own checks go through. Only while the account the connection's
service reports is held by another user's connection (``whatsapp``),
``account_conflict`` carries that account (and ``account`` is null); only
while a stored address / name is not valid (saved before the rules),
``problem`` = ``{field, message}``.

Every kind the user may connect is listed (not ``core``, which everyone has;
``ops`` only for a level with the ``ops`` capability: anyone else gets 403
``forbidden`` for it). ``PUT`` merges: credentials given replace the stored
ones of the same name (``null`` removes an optional one), settings given
replace theirs (``null`` = back to the default); a bad body, an unknown or
missing field answers 400 ``{"error": "invalid", "field", "message"}``.
Changes take effect at once (the store's cache, the kinds' ``on_connect`` /
``on_credentials_change`` / ``on_change``).

``?user=<id>`` acts on another user: only with the ``manage_users``
capability (else 403 ``forbidden``); an unknown id answers 404. The caller is
who makes the change (``store.connect / update / disconnect(by=)``): a kind
with ``change_requires`` (``cameras``) needs it (403 ``forbidden``), address
and Telegram chat fields follow docs/access.md → "Security rules" (400
``invalid`` naming the field); ``test`` / ``options`` refuse a stored address
that is not valid (400) without sending anything. ``GET /api/users`` lists
the users such a caller can pick (id, name, access level only). 503
``credentials_key`` when stored credentials cannot be read or written.
Loaded by core/server.py (``setup(app)``). The web page: static/integrations.js.
"""

import asyncio
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from aiohttp import web

from core import users
from core.access import CAPABILITIES
from core.auth import request_user, requires
from core.users import User
from integrations.framework import crypto, store
from integrations.framework.kind import Integration, InvalidValue, get_kind, kinds

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
        "kinds": list(store.connected_kinds(user)),
    }


def _rule_fields(rule: store.ChangeRule) -> Dict[str, Any]:
    return {"can_change": rule.can_change, "why_not": rule.why_not, "rule": rule.rule}


def integration_payload(user: User, integration: Integration, by: User) -> Dict[str, Any]:
    """One kind as ``user``'s, seen by ``by`` (the caller: ``user`` themselves
    or an admin acting for them): never a secret credential's value.

    Raises:
        CredentialsKeyError: the stored credentials cannot be decrypted.
    """
    conn = store.get(user, integration.kind)
    creds: Mapping[str, str] = conn.credentials if conn is not None else {}
    values: Mapping[str, Any] = conn.settings if conn is not None else integration.setting_defaults()
    credentials: List[Dict[str, Any]] = []
    for f in integration.credentials:
        item: Dict[str, Any] = {"name": f.name, "description": f.description, "required": f.required,
                                "set": bool(creds.get(f.name)), "type": f.type, "secret": f.secret}
        if not f.secret:
            item["value"] = creds.get(f.name) or None
        credentials.append({**item, **_rule_fields(store.field_rule(integration, user, f.name, by))})
    kind_rule = store.kind_rule(integration, by)
    out = {
        "kind": integration.kind,
        "description": integration.description,
        "connected": conn is not None,
        "requires": integration.requires,
        "can_change": kind_rule.can_change,
        "why_not": kind_rule.why_not,
        "credentials": credentials,
        "settings": [{"name": f.name, "description": f.description, "default": f.default,
                      "value": values.get(f.name), "type": f.type,
                      **_rule_fields(store.field_rule(integration, user, f.name, by))} for f in integration.settings],
        "account": (conn.account or None) if conn is not None else None,
        "check": integration.check is not None,
        "options": integration.options is not None,
        # The sign-in is the caller's own: not offered to an admin acting for someone else.
        "connect_url": integration.connect_url if by.id == user.id else None,
        "connect_label": integration.connect_label or None,
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


def _kind_for(user: User, name: str) -> Union[Integration, web.Response]:
    """Kind ``name`` when ``user`` may connect it (404 unknown / ``core``, 403 their level)."""
    try:
        integration = get_kind(name)
    except KeyError:
        return web.json_response({"error": "unknown_integration", "integration": name}, status=404)
    if integration.everyone:
        return web.json_response({"error": "unknown_integration", "integration": name}, status=404)
    if not integration.allowed_for(user):
        return _forbidden(str(integration.requires))
    return integration


def _resolve(request: web.Request) -> Union[Tuple[User, Integration], web.Response]:
    target = _target(request)
    if isinstance(target, web.Response):
        return target
    integration = _kind_for(target, request.match_info["kind"])
    if isinstance(integration, web.Response):
        return integration
    return target, integration


def parse_changes(integration: Integration, body: Any, *, connecting: bool
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
    declared_creds = {f.name: f for f in integration.credentials}
    declared_settings = {f.name for f in integration.settings}
    credentials: Dict[str, Optional[str]] = {}
    for name, value in raw_creds.items():
        f = declared_creds.get(name)
        if f is None:
            raise Invalid(name, f"The {integration.kind} integration has no credential {name!r} "
                                f"(known: {', '.join(declared_creds) or 'none'}).")
        if value is not None and not isinstance(value, str):
            raise Invalid(name, f"Credential {name!r} must be a string.")
        value = value.strip() if isinstance(value, str) else None
        if f.required and not value:
            raise Invalid(name, f"Credential {name!r} is required and cannot be empty.")
        credentials[name] = value or None
    for name in raw_settings:
        if name not in declared_settings:
            raise Invalid(name, f"The {integration.kind} integration has no setting {name!r} "
                                f"(known: {', '.join(sorted(declared_settings)) or 'none'}).")
    if connecting:
        missing = [f.name for f in integration.credentials if f.required and not credentials.get(f.name)]
        if missing:
            raise Invalid(missing[0], f"Connecting {integration.kind} needs the credential {missing[0]!r}.")
    return credentials, dict(raw_settings)


# --- Handlers -------------------------------------------------------------------------------------

async def me_api(request: web.Request) -> web.Response:
    """GET /api/me"""
    return web.json_response(me_payload(request_user(request)))


@requires("manage_users")
async def users_api(request: web.Request) -> web.Response:
    """GET /api/users: who an admin can pick on the Integrations page (``?user=``)."""
    return web.json_response({"users": [{"id": u.id, "name": u.name, "access": u.access}
                                        for u in users.list_users()]})


async def list_integrations_api(request: web.Request) -> web.Response:
    """GET /api/integrations"""
    target = _target(request)
    if isinstance(target, web.Response):
        return target
    caller = request_user(request)
    try:
        items: List[Dict[str, Any]] = [integration_payload(target, k, caller) for k in kinds()
                                       if not k.everyone and k.allowed_for(target)]
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Listing the integrations of user {target.id}")
    return web.json_response({"user_id": target.id, "integrations": items})


async def get_integration_api(request: web.Request) -> web.Response:
    """GET /api/integrations/{kind}"""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, integration = resolved
    try:
        return web.json_response(integration_payload(target, integration, request_user(request)))
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Reading {integration.kind} for user {target.id}")


async def put_integration_api(request: web.Request) -> web.Response:
    """PUT /api/integrations/{kind}: connect it, or update it."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, integration = resolved
    try:
        body = await request.json()
    except Exception:
        return _invalid(Invalid("body", "The body must be JSON."))
    connecting = not store.is_connected(target, integration.kind)
    caller = request_user(request)
    try:
        credentials, settings = parse_changes(integration, body, connecting=connecting)
        with users.acting_as(target):
            if connecting:
                store.connect(target, integration.kind,
                              credentials={k: v for k, v in credentials.items() if v is not None},
                              settings={k: v for k, v in settings.items() if v is not None}, by=caller)
            else:
                store.update(target, integration.kind, credentials=credentials or None, settings=settings or None,
                             by=caller)
    except (Invalid, InvalidValue) as e:
        return _invalid(Invalid(e.field, str(e)))
    except store.Forbidden as e:
        return _forbidden(e.needs)
    except PermissionError:
        return _forbidden(str(integration.requires))
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Saving {integration.kind} for user {target.id}")
    logger.info(f"{'Connected' if connecting else 'Updated'} {integration.kind} for user {target.id} "
                f"(by {caller.id})")
    return web.json_response(integration_payload(target, integration, caller), status=201 if connecting else 200)


async def delete_integration_api(request: web.Request) -> web.Response:
    """DELETE /api/integrations/{kind}"""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, integration = resolved
    caller = request_user(request)
    try:
        with users.acting_as(target):
            gone = store.disconnect(target, integration.kind, by=caller)
    except store.Forbidden as e:
        return _forbidden(e.needs)
    return web.json_response({**integration_payload(target, integration, caller), "disconnected": gone})


async def test_integration_api(request: web.Request) -> web.Response:
    """POST /api/integrations/{kind}/test: the kind's connection check on the stored connection."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, integration = resolved
    conn = store.require(target, integration.kind)  # NotConnected → 403 not_connected (middleware)
    problem = store.stored_value_problem(conn)
    if problem is not None:  # only ever against a stored, valid address (docs/access.md, "Security rules")
        return _invalid(Invalid(problem[0], f"{problem[1]} Set it again before testing."))
    if integration.check is None:
        return web.json_response({"kind": integration.kind, "checked": False, "ok": None,
                                  "reason": "This integration has no connection check."})
    try:
        with users.acting_as(target):
            reason = await asyncio.wait_for(integration.check(conn), CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        reason = f"No answer within {CHECK_TIMEOUT_SECONDS:.0f} seconds."
    except Exception as e:
        logger.warning(f"Connection check of {integration.kind} for user {target.id} failed: {type(e).__name__}")
        reason = f"The check failed ({type(e).__name__})."
    return web.json_response({"kind": integration.kind, "checked": True, "ok": reason is None,
                              "reason": reason or ""})


async def integration_options_api(request: web.Request) -> web.Response:
    """GET /api/integrations/{kind}/options: setting candidates from the user's own service."""
    resolved = _resolve(request)
    if isinstance(resolved, web.Response):
        return resolved
    target, integration = resolved
    if integration.options is None:
        return web.json_response({"error": "no_options", "integration": integration.kind}, status=404)
    conn = store.require(target, integration.kind)
    problem = store.stored_value_problem(conn)
    if problem is not None:
        return _invalid(Invalid(problem[0], f"{problem[1]} Set it again first."))
    try:
        with users.acting_as(target):
            found = await asyncio.wait_for(integration.options(conn), OPTIONS_TIMEOUT_SECONDS)
    except Exception as e:
        logger.warning(f"Setting options of {integration.kind} for user {target.id} failed: {type(e).__name__}")
        return web.json_response({"error": "unavailable", "integration": integration.kind,
                                  "message": f"Could not read the options ({type(e).__name__})."}, status=502)
    return web.json_response({"kind": integration.kind, "settings": found})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/me", me_api)
    r.add_get("/api/users", users_api)
    r.add_get("/api/integrations", list_integrations_api)
    r.add_get("/api/integrations/{kind}", get_integration_api)
    r.add_put("/api/integrations/{kind}", put_integration_api)
    r.add_delete("/api/integrations/{kind}", delete_integration_api)
    r.add_post("/api/integrations/{kind}/test", test_integration_api)
    r.add_get("/api/integrations/{kind}/options", integration_options_api)
