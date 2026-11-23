"""Who the caller is, their addons, and running the addons of the server
(docs/access.md, "Managing users"; ../docs/addons.md).

    GET    /api/me                       → {id, name, access, capabilities: [..], addons: [..], languages: [..], currency}
                                           (``addons``: the ones they have; ``languages`` / ``currency``: theirs,
                                           else the server's: core/user_locale.py; changed with PUT /api/profile)
    GET    /api/users                    → {users: [{id, name, access}]} (``manage_users`` only)
    GET    /api/addons                   → {user_id, addons: [<addon>, ...], restart_needed, can_manage, welcome[, pending]}
    POST   /api/addons/welcome           {dismissed: bool} → <welcome> (the caller's own first-run welcome)
    GET    /api/addons/{name}            → <addon>
    PUT    /api/addons/{name}            {credentials?: {name: str|null}, settings?: {name: value|null}}
                                         → <addon> (connects it, or updates it)
    DELETE /api/addons/{name}            → <addon> (connected: false) + disconnected: bool
    POST   /api/addons/{name}/test       → {name, checked, ok, reason}
    GET    /api/addons/{name}/options    → {name, settings: {name: [{value, label}]}}
    GET    /addons/{name}/web/{file}     the addon's web/ files (a loaded addon enabled on the server)

  ``manage_server`` only (running the server's addons):

    POST   /api/addons/{name}/disable    → <addon> (for everyone; ``enable`` turns it back on)
    POST   /api/addons/{name}/enable     → <addon>
    GET    /api/addons/catalog[?fresh=1] → {origin, dana_version, entries: [<entry>], problems: [..], restart_needed}
    POST   /api/addons/install           {name} (a catalog entry) or {repo, ref, path?, name?}, + accept?: <touches>
                                         → 201 {name, kind, repo, path, commit, replaced, touches,
                                           restart_needed: true, message}; 409 confirm_touches {name, touches,
                                           added, message}: it may touch more than the replaced version plus
                                           ``accept`` (nothing written; send it again with accept = touches
                                           once the admin agreed)
                                         or multipart/form-data: ``archive`` (a .tar.gz of the addon folder,
                                           its files at the root or in one top folder; at most 20 MB, 2000
                                           files, 50 MB unpacked) + ``accept`` (a JSON <touches>, optional)
                                         → 201 {name, kind: "upload", replaced, touches, restart_needed: true,
                                           message}; the same 409 confirm_touches; 413 too_large
    POST   /api/addons/{name}/remove     → {name, removed: true, restart_needed: true}
    POST   /api/server/restart           → 202 {restarting: true}: the process exits cleanly, the supervisor
                                           starts it again (core/lifecycle.py)

``<addon>`` = ``{name, title, description, version, source, state, needs,
touches, everyone, web, worker, connected, requires, can_change, why_not,
credentials: [{name, label, description, help, url, placeholder, required,
set, type, secret, value?, can_change, why_not, rule}], settings: [{name,
label, description, help, url, placeholder, default, value, type,
can_change, why_not, rule}], account, check, options, connect_url,
connect_label, icon, features, pages, setup, examples,
disconnect_warning}``. The onboarding words come from the manifest
(``Field(label=, help=, url=, placeholder=)``, ``Addon(icon=, features=,
setup=, examples=, disconnect_warning=)``): ``label`` is never empty (the
name in words by default), ``url`` and ``disconnect_warning`` are null when
not given, ``features`` falls back to the tools' first docstring lines,
``pages`` are the labels of the pages it adds to the navigation. ``needs``
= ``[{kind, what, why, optional}]`` (``Addon(needs=)``: env / account /
hardware / app / region / addon; an addon need's ``what`` is its name) and
``touches`` = ``{hosts, acts_outside, stores_credentials, reads}``
(``Addon(touches=)``; null for an addon that did not load).
``<welcome>`` = ``{show, dismissed, suggested: [addon name]}``: ``show``
while the user has no optional addon on (beyond ``default_on`` / ``everyone``
ones) and has not dismissed it; ``suggested`` are addons they can turn on
now. An admin acting with ``?user=`` gets ``show: false``. ``source``: ``official`` /
``installed``; ``state`` for this user: ``on`` (they have it), ``available``
(they can turn it on), ``off`` (only an admin can: ``why_not``),
``disabled-on-server`` (an admin disabled it, or an addon it needs, for
everyone) or ``failed`` (an installed addon that did not load: only
admins get its ``error``); ``web`` (empty unless it is on for the user) =
its pages, ``[{id, panel, style, nav: {label, icon, order, primary} | null}]`` with
the URLs of their files;
``worker``: it has a sidecar. Admins also get ``installed`` (``{kind, repo, path,
commit, installed_at}``: ``kind`` catalog / git / upload, the git fields null for
an upload) for an installed addon. ``<entry>`` = the catalog entry
(core/addons/catalog.py; ``touches`` null when the entry does not say) plus
``official`` (its name is an official addon: not installable), ``present``
(the source of an addon of that name already here: ``installed``, or ``upload``
for one installed from an uploaded folder, which a catalog install does not
replace; else null), ``installed`` (``{commit, loaded, version, touches}`` for
one installed from git in ``<data dir>/addons``: what the installed version declares), ``update_available``,
``touches_added`` (an update whose entry declares touches the installed
version did not: those, else null; the page asks again before such an
update) and ``compatible`` (this Dana is at least ``min_dana``). ``restart_needed``: an addon was installed,
updated or removed since the server started (admins get the changes as
``pending: [{name, change}]``). Installed addons run with the server's full
access (the page says so before installing).

Credential values are write-only (only whether each is ``set``) except an
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
``problem`` = ``{field, message}``; only while the connection's credentials
stopped working (``store.needs_reconnect``), ``reconnect`` = ``[{part, label,
reason, since, fix}]`` (``part`` "" for the whole connection, else e.g. a
money source's id; ``fix``: where its Reconnect button goes, the addon's
``reconnect_fix``). A passing ``test`` clears the whole connection's.

Every loaded addon the user's level allows is listed (``essentials``, which
everyone has, with ``everyone: true``; ``ops`` only for a level with the
``ops`` capability: anyone else gets 403 ``forbidden`` for it), then the
addons that failed to load. ``PUT`` merges: credentials given replace the stored
ones of the same name (``null`` removes an optional one), settings given
replace theirs (``null`` = back to the default); a bad body, an unknown or
missing field answers 400 ``{"error": "invalid", "field", "message"}``; an
addon disabled on the server answers 409 ``addon_disabled``.
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
import json
import logging
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from aiohttp import BodyPartReader, web

from core import addon_dirs, cards, lifecycle, now_hidden, user_locale, users
from core.access import CAPABILITIES
from core.auth import request_user, requires
from core.users import User
from core.addons import catalog, crypto, install, loader, state, store
from core.addons.addon import Addon, Field, InvalidValue, get_addon, all_addons
from core.addons.hooks import Touches

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 20.0
OPTIONS_TIMEOUT_SECONDS = 20.0
# Who runs the server's addons: disable / enable, the catalog, install, remove, restart.
MANAGE = "manage_server"

ON, AVAILABLE, OFF, DISABLED, FAILED = "on", "available", "off", "disabled-on-server", "failed"
# The first-run welcome: dismissed is a hidden item of the user's (core/now_hidden.py), kept for good.
WELCOME_ID = "welcome:addons"
WELCOME_SUGGESTIONS = 8


class Invalid(ValueError):
    """A request body the endpoint refuses (400), naming the field."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def _invalid(error: Invalid) -> web.Response:
    return web.json_response({"error": "invalid", "field": error.field, "message": str(error)}, status=400)


def _forbidden(capability: str) -> web.Response:
    return web.json_response({"error": "forbidden", "needs": capability}, status=403)


def _disabled(name: str) -> web.Response:
    return web.json_response({"error": "addon_disabled", "addon": name,
                              "message": f"The {name} addon is disabled on this server; an admin can enable it."},
                             status=409)


def me_payload(user: User) -> Dict[str, Any]:
    """``GET /api/me``. ``kinds`` repeats ``addons`` for the Android app built
    before the addons rename (package ``app.ganam.assistant``), so its Projects
    tab and money signals keep working until every device runs the new app;
    remove it then."""
    addons_on = list(store.connected_addons(user))
    return {
        "id": user.id,
        "name": user.name,
        "access": user.access,
        "capabilities": [c for c in CAPABILITIES if user.level.allows(c)],
        "addons": addons_on,
        "kinds": addons_on,
        "languages": list(user_locale.languages(user)),
        "currency": user_locale.currency(user),
    }


def _rule_fields(rule: store.ChangeRule) -> Dict[str, Any]:
    return {"can_change": rule.can_change, "why_not": rule.why_not, "rule": rule.rule}


def _web(addon: Addon) -> List[Dict[str, Any]]:
    """``addon``'s pages: each one's id, the URLs of its files and its navigation entry."""
    base = f"/addons/{addon.name}/web/"
    return [{"id": p.id, "panel": base + p.panel, "style": base + p.style if p.style else None,
             "nav": {"label": p.nav.label, "icon": p.nav.icon, "order": p.nav.order, "primary": p.nav.primary}
             if p.nav is not None else None}
            for p in addon.web]


def _page_labels(addon: Addon) -> List[str]:
    """The names of the pages ``addon`` adds to the web app's navigation."""
    return [p.nav.label for p in addon.web if p.nav is not None]


def _field_words(f: Field) -> Dict[str, Any]:
    """What the Addons page shows beside a field's input (core/addons/addon.py, ``Field``)."""
    return {"label": f.title, "description": f.description, "help": f.help, "url": f.url or None,
            "placeholder": f.placeholder}


def _onboarding(addon: Addon) -> Dict[str, Any]:
    """What the Addons page tells someone about to turn ``addon`` on: what it adds, the steps, what to try."""
    return {"icon": addon.icon, "features": list(addon.feature_lines()), "pages": _page_labels(addon),
            "setup": list(addon.setup), "examples": list(addon.examples),
            "disconnect_warning": addon.disconnect_warning or None}


def welcome_payload(user: User) -> Dict[str, Any]:
    """The first-run welcome for ``user``: shown while they have no optional addon on (only the ones
    every user or every new user gets) and have not dismissed it; ``suggested``: the addons they could
    turn on now, in the addon order (at most :data:`WELCOME_SUGGESTIONS`; none that needs a capability, ``ops``)."""
    connected = set(store.connected_addons(user))
    optional = [a for a in all_addons() if a.allowed_for(user) and not a.everyone and not a.default_on]
    suggested = [a.name for a in optional
                 if a.name not in connected and a.requires is None and state.is_enabled(a.name)
                 and store.addon_rule(a, user).can_change
                 and all(n in connected for n in a.depends)][:WELCOME_SUGGESTIONS]
    dismissed = now_hidden.row(user, WELCOME_ID) is not None
    has_optional = any(a.name in connected for a in optional)
    return {"show": not has_optional and not dismissed and bool(suggested), "dismissed": dismissed,
            "suggested": suggested}


def _state(user: User, addon: Addon, connected: bool, can_change: bool) -> str:
    if not state.is_enabled(addon.name):
        return DISABLED
    if connected:
        return ON
    return AVAILABLE if can_change else OFF


def addon_payload(user: User, addon: Addon, by: User) -> Dict[str, Any]:
    """One addon as ``user``'s, seen by ``by`` (the caller: ``user`` themselves
    or an admin acting for them): never a secret credential's value.

    Raises:
        CredentialsKeyError: the stored credentials cannot be decrypted.
    """
    conn = store.get(user, addon.name, disabled_too=True)
    creds: Mapping[str, str] = conn.credentials if conn is not None else {}
    values: Mapping[str, Any] = conn.settings if conn is not None else addon.setting_defaults()
    credentials: List[Dict[str, Any]] = []
    for f in addon.credentials:
        item: Dict[str, Any] = {"name": f.name, **_field_words(f), "required": f.required,
                                "set": bool(creds.get(f.name)), "type": f.type, "secret": f.secret}
        if not f.secret:
            item["value"] = creds.get(f.name) or None
        credentials.append({**item, **_rule_fields(store.field_rule(addon, user, f.name, by))})
    addon_rule = store.addon_rule(addon, by)
    can_change, why_not = addon_rule.can_change, addon_rule.why_not
    enabled = state.is_enabled(addon.name)
    if addon.everyone:
        can_change, why_not = False, "Every user has it"
    elif not enabled:
        can_change, why_not = False, "Disabled on this server"
    try:
        folder: Optional[addon_dirs.AddonFolder] = loader.folder(addon.name)
    except KeyError:  # declared outside the addon folders (a test's own addon)
        folder = None
    out = {
        "name": addon.name,
        "title": addon.title,
        "description": addon.description,
        "version": addon.version,
        "source": folder.source.label if folder is not None else addon_dirs.INSTALLED,
        "state": _state(user, addon, conn is not None, can_change),
        "needs": [n.to_json() for n in addon.needs],
        "touches": addon.touches.to_json(),
        "everyone": addon.everyone,
        "web": _web(addon) if enabled and conn is not None else [],
        "worker": addon.worker is not None,
        "connected": conn is not None,
        "requires": addon.requires,
        "can_change": can_change,
        "why_not": why_not,
        "credentials": credentials,
        "settings": [{"name": f.name, **_field_words(f), "default": f.default,
                      "value": values.get(f.name), "type": f.type,
                      **_rule_fields(store.field_rule(addon, user, f.name, by))} for f in addon.settings],
        "account": (conn.account or None) if conn is not None else None,
        "check": addon.test is not None,
        "options": addon.options is not None,
        # The sign-in is the caller's own: not offered to an admin acting for someone else.
        "connect_url": addon.connect_url if by.id == user.id else None,
        "connect_label": addon.connect_label or None,
        **_onboarding(addon),
    }
    if conn is not None and conn.account_conflict:
        # The account its service reports is held by another user's connection (whatsapp).
        out["account_conflict"] = conn.account_conflict
    pending = store.reconnects(user, addon.name) if conn is not None else []
    if pending:
        out["reconnect"] = [{"part": r.part, "label": r.label, "reason": r.reason, "since": r.since,
                             "fix": addon.reconnect_fix.to_json()} for r in pending]
    problem = store.stored_value_problem(conn) if conn is not None else None
    if problem is not None:
        out["problem"] = {"field": problem[0], "message": problem[1]}
    if folder is not None and folder.source.kind == addon_dirs.INSTALLED and by.level.allows(MANAGE):
        info = addon_dirs.installed_info(folder.path) or {}
        out["installed"] = {"kind": install.source_kind(info) or None, "repo": info.get("repo"),
                            "path": info.get("path"), "commit": info.get("commit"),
                            "installed_at": info.get("installed_at")}
    return out


def failed_payload(failed: loader.Failed, by: User) -> Dict[str, Any]:
    """An addon that was found but did not load (its error only for an admin)."""
    out: Dict[str, Any] = {
        "name": failed.name, "title": failed.name.replace("_", " ").capitalize(), "description": "",
        "version": "", "source": addon_dirs.INSTALLED if failed.source == addon_dirs.EXTRA else failed.source,
        "state": FAILED, "needs": [], "touches": None, "everyone": False, "web": [], "worker": False, "connected": False,
        "requires": None, "can_change": False, "why_not": "It did not load", "credentials": [], "settings": [],
        "account": None, "check": False, "options": False, "connect_url": None, "connect_label": None,
        "icon": "", "features": [], "pages": [], "setup": [], "examples": [], "disconnect_warning": None,
    }
    if by.level.allows(MANAGE):
        out["error"] = failed.error
    return out


def pending_changes() -> List[Dict[str, str]]:
    """What changed in ``<data dir>/addons`` since this process loaded its addons
    (installed, updated or removed: a restart loads it)."""
    found = loader.folders()
    out: List[Dict[str, str]] = []
    for name, record in install.installed(addon_dirs.installed_dir()).items():
        folder = found.get(name)
        if folder is None:
            out.append({"name": name, "change": "install"})
        elif folder.source.kind == addon_dirs.INSTALLED and (
                record.get("commit") != loader.loaded_commit(name)
                or record.get("installed_at") != loader.loaded_record(name).get("installed_at")):
            out.append({"name": name, "change": "update"})
    for name, folder in found.items():
        if folder.source.kind == addon_dirs.INSTALLED and not (folder.path / "addon.py").is_file():
            out.append({"name": name, "change": "remove"})
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
                                       if k.allowed_for(target)]
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Listing the addons of user {target.id}")
    items += [failed_payload(f, caller) for f in loader.failed().values()]
    pending = pending_changes()
    body: Dict[str, Any] = {"user_id": target.id, "addons": items, "restart_needed": bool(pending),
                            "can_manage": caller.level.allows(MANAGE),
                            # Only for the caller's own addons: an admin acting for someone gets no welcome.
                            "welcome": welcome_payload(target) if target.id == caller.id
                            else {"show": False, "dismissed": False, "suggested": []}}
    if caller.level.allows(MANAGE):
        body["pending"] = pending
    return web.json_response(body)


async def welcome_api(request: web.Request) -> web.Response:
    """POST /api/addons/welcome {dismissed: bool}: hide the caller's first-run welcome for good, or bring it back."""
    user = request_user(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("dismissed"), bool):
        return _invalid(Invalid("dismissed", "The body must be {\"dismissed\": true} or {\"dismissed\": false}."))
    if body["dismissed"]:
        now_hidden.hide(user, WELCOME_ID, cards.FAR, "dismissed")
    else:
        now_hidden.unhide(user, WELCOME_ID)
    return web.json_response(welcome_payload(user))


async def get_addon_api(request: web.Request) -> web.Response:
    """GET /api/addons/{name}"""
    target = _target(request)
    if isinstance(target, web.Response):
        return target
    try:
        everyone = get_addon(request.match_info["name"]).everyone
    except KeyError:
        everyone = False
    if everyone:  # listed with the others, though nobody connects it
        return web.json_response(addon_payload(target, get_addon(request.match_info["name"]), request_user(request)))
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
    except state.AddonDisabled as e:
        return _disabled(e.addon)
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
    if reason is None:
        store.reconnected(target, addon.name, None)
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


# --- Running the server's addons (manage_server) -----------------------------------------------

def _loaded_addon(name: str) -> Union[Addon, web.Response]:
    try:
        return get_addon(name)
    except KeyError:
        return web.json_response({"error": "unknown_addon", "addon": name}, status=404)


async def _set_state(request: web.Request, off: bool) -> web.Response:
    caller = request_user(request)
    addon = _loaded_addon(request.match_info["name"])
    if isinstance(addon, web.Response):
        return addon
    try:
        state.set_disabled(addon.name, off, by=caller)
    except state.CannotDisable as e:
        return web.json_response({"error": "cannot_disable", "addon": addon.name, "message": str(e)}, status=400)
    try:
        return web.json_response(addon_payload(caller, addon, caller))
    except crypto.CredentialsKeyError as e:
        return _key_error(e, f"Reading {addon.name}")


@requires(MANAGE)
async def disable_addon_api(request: web.Request) -> web.Response:
    """POST /api/addons/{name}/disable: for everyone on this server."""
    return await _set_state(request, True)


@requires(MANAGE)
async def enable_addon_api(request: web.Request) -> web.Response:
    """POST /api/addons/{name}/enable"""
    return await _set_state(request, False)


def _present() -> Dict[str, str]:
    """The source of every addon folder found at start or in ``<data dir>/addons`` now, by name
    (``upload`` for one installed from an uploaded folder: a catalog install does not replace it)."""
    out = {name: f.source.label for name, f in loader.folders().items()}
    for name, record in install.installed(addon_dirs.installed_dir()).items():
        out[name] = install.UPLOAD if install.source_kind(record) == install.UPLOAD else out.get(name, addon_dirs.INSTALLED)
    return out


def _git_installed() -> Dict[str, Dict[str, Any]]:
    """The addons in ``<data dir>/addons`` installed from git (the catalog or a repository), by name, with their records."""
    return {name: record for name, record in install.installed(addon_dirs.installed_dir()).items()
            if install.source_kind(record) != install.UPLOAD}


def catalog_entry_payload(entry: catalog.Entry, present: Mapping[str, str],
                          installed: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """A catalog entry merged with what this server has."""
    record = installed.get(entry.name)
    loaded_names = loader.loaded()
    version = get_addon(entry.name).version if entry.name in loaded_names else ""
    commit = record.get("commit") if record is not None else None
    update = record is not None and commit != entry.source.ref
    have = install.installed_touches(addon_dirs.installed_dir() / entry.name) if record is not None else None
    added = entry.touches.added_since(have) if update and have is not None and entry.touches is not None else None
    return {**entry.to_json(),
            "official": (addon_dirs.OFFICIAL_DIR / entry.name).is_dir(),
            "present": present.get(entry.name),
            "installed": {"commit": commit, "loaded": entry.name in loaded_names
                          and loader.loaded_commit(entry.name) == commit, "version": version,
                          "touches": have.to_json() if have is not None else None}
            if record is not None else None,
            "update_available": update,
            "touches_added": added.to_json() if added is not None and not added.empty else None,
            "compatible": catalog.compatible(entry)}


@requires(MANAGE)
async def catalog_api(request: web.Request) -> web.Response:
    """GET /api/addons/catalog: the catalog merged with what is installed."""
    try:
        cat = await asyncio.to_thread(catalog.load, fresh=request.query.get("fresh") == "1")
    except catalog.CatalogError as e:
        logger.warning(str(e))
        return web.json_response({"error": "catalog_unavailable", "message": str(e)}, status=502)
    present = _present()
    installed = _git_installed()
    return web.json_response({"origin": cat.origin, "dana_version": catalog.dana_version(),
                              "entries": [catalog_entry_payload(e, present, installed) for e in cat.entries],
                              "problems": cat.problems, "notice": cat.notice, "restart_needed": bool(pending_changes())})


def _install_refused(message: str, status: int = 400, error: str = "install_refused") -> web.Response:
    return web.json_response({"error": error, "message": message}, status=status)


class _TooLarge(Exception):
    """The uploaded archive is over :data:`install.MAX_ARCHIVE_BYTES`."""


async def _read_upload(request: web.Request, into: Path) -> Optional[str]:
    """Stream the multipart ``archive`` part into the file ``into``; returns the ``accept``
    part's text (None without one).

    Raises:
        _TooLarge: the archive is over the cap.
        ValueError: no ``archive`` part.
    """
    reader = await request.multipart()
    accept: Optional[str] = None
    got = False
    while True:
        part = await reader.next()
        if part is None:
            break
        if not isinstance(part, BodyPartReader):
            continue
        if part.name == "archive":
            size = 0
            with into.open("wb") as fh:
                while True:
                    chunk = await part.read_chunk(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > install.MAX_ARCHIVE_BYTES:
                        raise _TooLarge()
                    fh.write(chunk)
            got = True
        elif part.name == "accept":
            accept = (await part.text())[:65536]
    if not got:
        raise ValueError("no archive part")
    return accept


def _confirm_touches(e: install.ConfirmTouches) -> web.Response:
    return web.json_response({"error": "confirm_touches", "name": e.name, "touches": e.touches.to_json(),
                              "added": e.added.to_json(), "message": str(e)}, status=409)


async def _upload_install(request: web.Request, caller: User) -> web.Response:
    """POST /api/addons/install as multipart: the ``archive`` of an addon folder (+ ``accept``)."""
    with tempfile.TemporaryDirectory(prefix="dana-upload-api-") as tmp:
        archive = Path(tmp) / "addon.tar.gz"
        try:
            raw_accept = await _read_upload(request, archive)
        except _TooLarge:
            return _install_refused(f"The archive is larger than {install.MAX_ARCHIVE_BYTES // (1024 * 1024)} MB.",
                                    413, "too_large")
        except (ValueError, AssertionError) as e:
            return _install_refused(f"Send the addon folder as a multipart part named archive (.tar.gz): {e}")
        try:
            accept = Touches.from_json(json.loads(raw_accept)) if raw_accept else None
        except ValueError as e:
            return _install_refused(f"accept: {e}")
        try:
            done = await asyncio.to_thread(install.install_archive, archive, addon_dirs.installed_dir(),
                                           by=caller.id, accept=accept)
        except install.ConfirmTouches as e:
            return _confirm_touches(e)
        except install.InstallError as e:
            logger.info(f"Upload install refused for user {caller.id}: {e}")
            return _install_refused(str(e))
    logger.warning(f"Addon {done.name} {'updated' if done.replaced else 'installed'} from an uploaded folder "
                   f"by user {caller.id}; a restart loads it")
    return web.json_response({"name": done.name, "kind": done.kind, "replaced": done.replaced,
                              "touches": done.touches.to_json(), "restart_needed": True,
                              "message": f"{'Updated' if done.replaced else 'Installed'} {done.name} from the "
                                         f"uploaded folder. Restart the server to load it."}, status=201)


@requires(MANAGE)
async def install_api(request: web.Request) -> web.Response:
    """POST /api/addons/install: a catalog entry by ``name``, any ``{repo, ref, path}``, or an
    uploaded folder (multipart ``archive``); admins only."""
    caller = request_user(request)
    if request.content_type == "multipart/form-data":
        return await _upload_install(request, caller)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return _install_refused("The body must be a JSON object: {name} or {repo, ref, path}.")
    name = body.get("name") if isinstance(body.get("name"), str) else None
    kind = install.GIT
    if body.get("repo") is not None or body.get("ref") is not None:
        source = install.Source(str(body.get("repo") or ""), str(body.get("path") or ""), str(body.get("ref") or ""))
    elif name:
        try:
            cat = await asyncio.to_thread(catalog.load)
        except catalog.CatalogError as e:
            return _install_refused(str(e), 502, "catalog_unavailable")
        entry = cat.get(name)
        if entry is None:
            return _install_refused(f"The catalog has no addon {name!r}.", 404, "not_in_catalog")
        if not catalog.compatible(entry):
            return _install_refused(f"{name} needs Dana {entry.min_dana} or later; this server is "
                                    f"{catalog.dana_version()}.", 409, "incompatible")
        source, kind = entry.source, install.CATALOG
    else:
        return _install_refused("Give the name of a catalog entry, or repo and ref (and path).")
    try:
        accept = Touches.from_json(body["accept"]) if body.get("accept") is not None else None
    except ValueError as e:
        return _install_refused(f"accept: {e}")
    try:
        done = await asyncio.to_thread(install.install, source, addon_dirs.installed_dir(), name=name,
                                       by=caller.id, accept=accept, kind=kind)
    except install.ConfirmTouches as e:
        return _confirm_touches(e)
    except install.InstallError as e:
        logger.info(f"Install refused for user {caller.id}: {e}")
        return _install_refused(str(e))
    src = done.source or source
    logger.warning(f"Addon {done.name} {'updated' if done.replaced else 'installed'} from {src.repo} "
                   f"at {src.ref} by user {caller.id}; a restart loads it")
    return web.json_response({"name": done.name, "kind": done.kind, "repo": src.repo, "path": src.path,
                              "commit": src.ref, "replaced": done.replaced,
                              "touches": done.touches.to_json(), "restart_needed": True,
                              "message": f"{'Updated' if done.replaced else 'Installed'} {done.name} at "
                                         f"{src.ref[:12]}. Restart the server to load it."}, status=201)


@requires(MANAGE)
async def remove_addon_api(request: web.Request) -> web.Response:
    """POST /api/addons/{name}/remove: delete an installed addon's folder (its tables stay)."""
    name = request.match_info["name"]
    if name not in install.installed(addon_dirs.installed_dir()):
        if (addon_dirs.OFFICIAL_DIR / name).is_dir() or name in loader.folders():
            return _install_refused(f"{name} is not an installed addon; it cannot be removed here.",
                                    400, "not_installed")
        return web.json_response({"error": "unknown_addon", "addon": name}, status=404)
    try:
        await asyncio.to_thread(install.remove, name, addon_dirs.installed_dir())
    except install.InstallError as e:
        return _install_refused(str(e), 400, "not_installed")
    logger.warning(f"Addon {name} removed by user {request_user(request).id}; a restart unloads it")
    return web.json_response({"name": name, "removed": True, "restart_needed": True})


RESTART_DELAY_SECONDS = 0.5


@requires(MANAGE)
async def restart_api(request: web.Request) -> web.Response:
    """POST /api/server/restart: answer, then exit cleanly for the supervisor to start the server again."""
    caller = request_user(request)
    asyncio.get_running_loop().call_later(RESTART_DELAY_SECONDS, lifecycle.request_restart,
                                          f"asked by user {caller.id}")
    return web.json_response({"restarting": True}, status=202)


# --- An addon's web files ---------------------------------------------------------------------

async def addon_web_file(request: web.Request) -> web.StreamResponse:
    """GET /addons/{name}/web/{file}: a file of a loaded addon's ``web/`` folder, while it is enabled on the server."""
    name = request.match_info["name"]
    missing = web.json_response({"error": "not_found"}, status=404)
    if name not in loader.loaded() or not state.is_enabled(name):
        return missing
    root = (loader.folder(name).path / "web").resolve()
    rel = request.match_info["file"]
    if not rel or "\\" in rel or "\0" in rel or any(part in ("", ".", "..") or part.startswith(".")
                                                     for part in rel.split("/")):
        return missing
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return missing
    kind, _ = mimetypes.guess_type(target.name)
    return web.FileResponse(target, headers={"Cache-Control": "no-cache",
                                             "Content-Type": kind or "application/octet-stream",
                                             "X-Content-Type-Options": "nosniff"})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/me", me_api)
    r.add_get("/api/users", users_api)
    r.add_get("/api/addons", list_addons_api)
    # Before /api/addons/{name}: these names are reserved (core/addons/install.py).
    r.add_get("/api/addons/catalog", catalog_api)
    r.add_post("/api/addons/install", install_api)
    r.add_post("/api/addons/welcome", welcome_api)
    r.add_post("/api/server/restart", restart_api)
    r.add_post("/api/addons/{name}/disable", disable_addon_api)
    r.add_post("/api/addons/{name}/enable", enable_addon_api)
    r.add_post("/api/addons/{name}/remove", remove_addon_api)
    r.add_get("/addons/{name}/web/{file:.+}", addon_web_file)
    r.add_get("/api/addons/{name}", get_addon_api)
    r.add_put("/api/addons/{name}", put_addon_api)
    r.add_delete("/api/addons/{name}", delete_addon_api)
    r.add_post("/api/addons/{name}/test", test_addon_api)
    r.add_get("/api/addons/{name}/options", addon_options_api)
