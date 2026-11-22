"""The LLM provider and its key over HTTP (docs/configuration.md, "Models";
core/llm_provider.py). ``manage_server`` only: the key is the server's.

    GET /api/llm → {provider, providers: [{id, label, key_env, models, transcribe, key_url,
                    experimental, local}], key: {set, from_env, last4}, base_url, model, models}
    PUT /api/llm {provider, key?, base_url?, model?}
        → the same payload, once a tiny completion with the provider's fast model
          and the new key (or the local server's address and model) answered.
          ``key`` may be left out to keep the one stored for that provider (or
          the one set in env). 400 ``invalid`` {field, detail}; 422 ``check_failed``
          {detail}: the provider refused it (nothing stored).

The key is never in an answer or a log line: answers carry ``last4`` only, and
a failed check is described by its kind (refused, unknown model, unreachable),
never by the provider's message, which may quote the key.
"""

import json
import logging

from aiohttp import web

from core import llm, llm_provider
from core.addons import crypto
from core.auth import request_user, requires

logger = logging.getLogger(__name__)


def _failure(e: BaseException) -> str:
    """A failed check in words, without the provider's own message."""
    code = llm.status_code(e)
    name = type(e).__name__
    if code in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return "The key was refused. Check that you pasted the whole key, for this provider."
    if code == 404 or "NotFound" in name:
        return "The model was not found. For a local model, check its name (ollama list)."
    if code == 429 or "RateLimit" in name:
        return "The provider says the account is over its limit or has no credit."
    if code is None or "Connection" in name or "Timeout" in name:
        return "The provider could not be reached. For a local model, check the address."
    return f"The provider answered an error (HTTP {code})."


@requires("manage_server")
async def get_llm_api(request: web.Request) -> web.Response:
    """GET /api/llm"""
    return web.json_response(llm_provider.status())


@requires("manage_server")
async def put_llm_api(request: web.Request) -> web.Response:
    """PUT /api/llm"""
    user = request_user(request)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        body = None
    if not isinstance(body, dict) or not all(isinstance(body.get(k) or "", str)
                                             for k in ("provider", "key", "base_url", "model")):
        return web.json_response({"error": "invalid", "detail": "a JSON object with text fields is expected"},
                                 status=400)
    key = (body.get("key") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    try:
        provider = llm_provider.check(body.get("provider") or "", key, base_url, model)
    except llm_provider.Invalid as e:
        return web.json_response({"error": "invalid", "field": e.field, "detail": e.detail}, status=400)
    target = llm_provider.test_target(provider, key, base_url, model)
    try:
        await llm.check_model(target["model"], api_key=target.get("api_key", ""),
                              api_base=target.get("api_base", ""))
    except Exception as e:
        logger.warning(f"LLM check for {provider.label} failed: {type(e).__name__} "
                       f"(HTTP {llm.status_code(e) or '-'})")
        return web.json_response({"error": "check_failed", "detail": _failure(e)}, status=422)
    try:
        return web.json_response(llm_provider.save(provider, key, base_url, model, by=user.id))
    except crypto.CredentialsKeyError as e:
        return web.json_response({"error": "credentials_key", "detail": str(e)}, status=500)


def setup(app: web.Application) -> None:
    """Register the LLM provider routes."""
    app.router.add_get("/api/llm", get_llm_api)
    app.router.add_put("/api/llm", put_llm_api)
