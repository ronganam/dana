"""Fakes for this addon's tests: the users every test needs, the model, and an outside HTTP service.

- :func:`three_users`: Robin (the first admin) and Gal with the addon turned on, Maya without it.
- :class:`FakeLLM`: answers every ``core.llm`` call offline with your reply; keeps what was asked.
- :class:`FakeHTTP`: a web service on this machine (an aiohttp server) with the routes you give;
  point the addon at :attr:`FakeHTTP.url` through the env value it declares in ``needs``.

Every name and value here is made up; never put a real person, number or key in a test.
"""

import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from aiohttp import web
from aiohttp.test_utils import TestServer


def three_users(addon: str, *, settings: Optional[Mapping[str, Any]] = None,
                credentials: Optional[Mapping[str, Any]] = None,
                gal_settings: Optional[Mapping[str, Any]] = None,
                gal_credentials: Optional[Mapping[str, Any]] = None) -> Tuple[Any, Any, Any]:
    """(Robin, Gal, Maya): Robin and Gal have ``addon`` on with ``settings`` / ``credentials`` (Gal with
    ``gal_settings`` / ``gal_credentials`` when given: ``{}`` for none), Maya does not.
    Call it after ``db.migrate()`` and ``users.bootstrap()``."""
    from core import users
    from core.addons import loader, store
    loader.load()  # every addon, this one included
    robin = users.first_admin()
    assert robin is not None, "three_users needs users.bootstrap() first"
    gal, maya = users.create("Gal"), users.create("Maya")
    store.connect(robin, addon, credentials=dict(credentials or {}) or None,
                  settings=dict(settings or {}) or None)
    gal_credentials = credentials if gal_credentials is None else gal_credentials
    gal_settings = settings if gal_settings is None else gal_settings
    store.connect(gal, addon, credentials=dict(gal_credentials or {}) or None,
                  settings=dict(gal_settings or {}) or None)
    return robin, gal, maya


class FakeLLM:
    """Every model call answered offline: ``reply(messages, purpose)`` gives the text (a JSON string for
    ``complete_json``). :meth:`install` puts it in place; :attr:`calls` keeps ``{purpose, messages}``."""

    def __init__(self, reply: Callable[[List[Dict[str, Any]], str], str]) -> None:
        self.reply = reply
        self.calls: List[Dict[str, Any]] = []

    def install(self) -> "FakeLLM":
        import litellm
        real = litellm.acompletion

        async def acompletion(**kwargs: Any) -> Any:
            purpose = str(((kwargs.get("litellm_metadata") or {}).get("usage") or {}).get("purpose") or "")
            self.calls.append({"purpose": purpose, "messages": kwargs.get("messages") or []})
            kwargs["mock_response"] = self.reply(kwargs.get("messages") or [], purpose)
            return await real(**kwargs)

        litellm.acompletion = acompletion
        litellm.suppress_debug_info = True
        return self


class FakeHTTP:
    """A web service on 127.0.0.1: ``routes`` maps ``"GET /path"`` to ``handler(request) -> (status, body)``
    (a dict body is sent as JSON). Use ``async with FakeHTTP({...}) as svc:`` and :attr:`url`;
    :attr:`requests` keeps ``{method, path, query, headers}`` of each request."""

    def __init__(self, routes: Mapping[str, Callable[[web.Request], Tuple[int, Any]]]) -> None:
        self.routes = dict(routes)
        self.requests: List[Dict[str, Any]] = []
        self._server: Optional[TestServer] = None

    async def __aenter__(self) -> "FakeHTTP":
        app = web.Application()
        for key, handler in self.routes.items():
            method, path = key.split(" ", 1)
            app.router.add_route(method, path, self._wrap(handler))
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._server is not None:
            await self._server.close()

    @property
    def url(self) -> str:
        """The service's base address (no trailing slash)."""
        assert self._server is not None, "use it inside async with"
        return str(self._server.make_url("")).rstrip("/")

    def _wrap(self, handler: Callable[[web.Request], Tuple[int, Any]]) -> Callable[[web.Request], Any]:
        async def handle(request: web.Request) -> web.Response:
            self.requests.append({"method": request.method, "path": request.path, "query": dict(request.query),
                                  "headers": dict(request.headers)})
            status, body = handler(request)
            if isinstance(body, (dict, list)):
                return web.Response(status=status, text=json.dumps(body), content_type="application/json")
            return web.Response(status=status, text=str(body))
        return handle
