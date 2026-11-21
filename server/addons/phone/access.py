"""Who may use the phone routes: :func:`phone_user` (every ``api.py`` of the addon)."""

from aiohttp import web

from core.auth import request_user
from core.users import User
from core.addons import store
from addons.phone import config as phone_config


def phone_user(request: web.Request) -> User:
    """The caller, who must have the ``phone`` addon (NotConnected → 403 not_connected)."""
    user = request_user(request)
    store.require(user, phone_config.ADDON)
    return user
