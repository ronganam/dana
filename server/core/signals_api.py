"""``GET /api/signals``: the caller's own signals overview (docs/signals.md):
every signal (wake, screen, car, location, sleep, weather, always in that
order) with the sources this user can have, highest rank first, which one is
active, when each last delivered, and their wake window. ``POST
/api/signals`` (what the phone sends) is the phone addon's route
(addons/phone/api.py)."""

from aiohttp import web

from core import signals, wake_window
from core.auth import request_user


async def get_signals_api(request: web.Request) -> web.Response:
    """GET /api/signals → {signals: [{signal, sources: [{source, label, active, last_at, detail}]}],
    wake_window: {start, end, learned}}"""
    user = request_user(request)
    return web.json_response({"signals": signals.overview(user), "wake_window": wake_window.get(user).to_json()})


def setup(app: web.Application) -> None:
    """Register the signals overview."""
    app.router.add_get("/api/signals", get_signals_api)
