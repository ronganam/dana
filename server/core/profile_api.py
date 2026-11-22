"""The caller's own profile: name, pronoun, languages and currency (docs/access.md
→ User; core/user_locale.py). The web UI's Settings → Profile card
(static/profile.js) and the wizard's You step (static/setup.js).

    GET /api/profile → {name, pronoun, languages: [..], currency, own: {languages: [..], currency},
                        defaults: {languages: [..], currency},
                        wake_window: {start: "HH:MM", end: "HH:MM", learned: bool}}
        ``languages`` / ``currency``: what counts for the caller (their own, else
        the server's ``DEFAULT_LANGUAGES`` / ``CURRENCY``); ``own``: what they
        chose (``[]`` / ``""``: none, the server's apply); ``wake_window``: the
        hours their wake is looked for (core/wake_window.py).
    PUT /api/profile {name?, pronoun?: "he" | "she" | "they",
                      languages?: "he, en" | ["he", "en"] | [] , currency?: "ILS" | "",
                      wake_window?: {start: "HH:MM", end: "HH:MM"} | null}
        → the same payload. ``[]`` / ``""`` go back to the server's, a null
        wake window to the learned one. An empty name, an unknown pronoun, a
        malformed code or wake window → 400 ``{"error": "invalid", "field":
        <name>, "detail": <why>}`` (nothing is changed).

Each user changes only their own; an admin changes someone else's with
``bin/dana users update USER --languages / --currency``.
"""

import json
from typing import Any, Dict

from aiohttp import web

from core import user_locale, user_rows, users, wake_window
from core.auth import request_user
from core.users import User


NAME_MAX = 80
PRONOUNS = user_rows.PRONOUNS


def profile_payload(user: User) -> Dict[str, Any]:
    """What GET /api/profile answers for ``user``."""
    try:
        own_languages = list(user_locale.parse_languages(user.languages))
    except ValueError:
        own_languages = []
    return {
        "name": user.name,
        "pronoun": user.pronoun,
        "languages": list(user_locale.languages(user)),
        "currency": user_locale.currency(user),
        "own": {"languages": own_languages, "currency": user.currency},
        "defaults": {"languages": list(user_locale.server_languages()), "currency": user_locale.server_currency()},
        "wake_window": wake_window.get(user).to_json(),
    }


def _invalid(field: str, detail: str) -> web.Response:
    return web.json_response({"error": "invalid", "field": field, "detail": detail}, status=400)


async def get_profile_api(request: web.Request) -> web.Response:
    """GET /api/profile"""
    return web.json_response(profile_payload(request_user(request)))


async def put_profile_api(request: web.Request) -> web.Response:
    """PUT /api/profile"""
    user = request_user(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid", "detail": "a JSON object is expected"}, status=400)
    changes: Dict[str, str] = {}
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > NAME_MAX:
            return _invalid("name", f"A name of 1 to {NAME_MAX} characters")
        changes["name"] = name.strip()
    if "pronoun" in body:
        if body["pronoun"] not in PRONOUNS:
            return _invalid("pronoun", f"One of {', '.join(PRONOUNS)}")
        changes["pronoun"] = body["pronoun"]
    if "languages" in body:
        raw = body["languages"]
        if raw is not None and not isinstance(raw, (str, list)):
            return _invalid("languages", "a comma list or a list of language codes")
        try:
            changes["languages"] = ",".join(user_locale.parse_languages(raw or ""))
        except ValueError as e:
            return _invalid("languages", str(e))
    if "currency" in body:
        raw = body["currency"]
        if raw is not None and not isinstance(raw, str):
            return _invalid("currency", "a three-letter currency code")
        try:
            changes["currency"] = user_locale.check_currency(raw or "")
        except ValueError as e:
            return _invalid("currency", str(e))
    if "wake_window" in body and body["wake_window"] is not None:
        try:
            wake_window.check(body["wake_window"])
        except ValueError as e:
            return _invalid("wake_window", str(e))
    if changes:
        user = users.update(user.id, **changes)
    if "wake_window" in body:
        wake_window.set_window(user, body["wake_window"])
    return web.json_response(profile_payload(user))


def setup(app: web.Application) -> None:
    """Register the profile routes."""
    app.router.add_get("/api/profile", get_profile_api)
    app.router.add_put("/api/profile", put_profile_api)
