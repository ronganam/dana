"""Signing a user's Telegram account in with a QR code (docs/telegram-account.md).

One sign-in at a time per user, in memory: :func:`start` connects a fresh
client (with updates: the QR login completes on an update), asks Telegram for
a login token and waits for it to be scanned from the phone (Settings →
Devices → Link Desktop Device). A token lives about 30 seconds and is renewed
until :data:`LOGIN_SECONDS` have passed. An account with two-step
verification then needs its password (:func:`submit_password`). On success
the session string becomes the credential of the user's ``telegram_account``
connection (connected, or updated when it already was) and the account's
name its ``account``.

:func:`state` is what the connect page polls; it never carries the session."""

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core import users
from core.users import User
from core.addons import store
from addons.telegram_account import client as tg, config

logger = logging.getLogger(__name__)

LOGIN_SECONDS = 300


@dataclass
class Login:
    user: User
    client: Any
    status: str = "starting"  # starting | qr | password | connected | expired | failed
    url: str = ""
    expires: float = 0.0
    started: float = field(default_factory=time.monotonic)
    account: str = ""
    error: str = ""
    task: Optional[asyncio.Task] = None
    qr: Any = None


_logins: Dict[str, Login] = {}


def qr_svg(url: str) -> str:
    """The login URL as an SVG QR code."""
    import qrcode
    import qrcode.image.svg
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    out = io.BytesIO()
    image.save(out)
    return out.getvalue().decode().split("?>", 1)[-1].strip()


def state(user: User) -> Dict[str, Any]:
    """The user's sign-in as the connect page shows it."""
    login = _logins.get(user.id)
    if login is None:
        return {"status": "none", "configured": config.configured()}
    out: Dict[str, Any] = {"status": login.status, "configured": True}
    if login.status == "qr" and login.url:
        out["qr_svg"] = qr_svg(login.url)
        out["expires_in"] = max(0, int(login.expires - time.time()))
    if login.account:
        out["account"] = login.account
    if login.error:
        out["error"] = login.error
    return out


async def _close(login: Login) -> None:
    if login.task is not None and login.task is not asyncio.current_task() and not login.task.done():
        login.task.cancel()
    await tg._disconnect(login.client)


async def cancel(user: User) -> None:
    login = _logins.pop(user.id, None)
    if login is not None:
        await _close(login)


def _expiry(qr: Any) -> float:
    expires = getattr(qr, "expires", None)
    try:
        return expires.timestamp()
    except AttributeError:
        return time.time() + 30


async def start(user: User) -> Dict[str, Any]:
    """Start (or restart) the user's QR sign-in; returns :func:`state`.

    Raises:
        TelegramUnavailable: no server app, or Telegram cannot be reached.
    """
    await cancel(user)
    client = tg.make_client("", receive_updates=True)
    login = Login(user=user, client=client)
    _logins[user.id] = login
    try:
        await client.connect()
        qr = await client.qr_login()
    except Exception as e:
        _logins.pop(user.id, None)
        await tg._disconnect(client)
        logger.warning(f"Telegram sign-in of user {user.id} could not start: {type(e).__name__}: {e}")
        raise tg.TelegramUnavailable(tg.explain(e))
    login.qr, login.url, login.expires, login.status = qr, qr.url, _expiry(qr), "qr"
    login.task = asyncio.create_task(_wait(login))
    return state(user)


async def _wait(login: Login) -> None:
    from telethon.errors import SessionPasswordNeededError
    try:
        while True:
            try:
                await login.qr.wait(timeout=max(1.0, login.expires - time.time()))
                await _finish(login)
                return
            except asyncio.TimeoutError:
                if time.monotonic() - login.started > LOGIN_SECONDS:
                    login.status, login.url = "expired", ""
                    await tg._disconnect(login.client)
                    return
                await login.qr.recreate()
                login.url, login.expires = login.qr.url, _expiry(login.qr)
    except SessionPasswordNeededError:
        login.status, login.url = "password", ""
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Telegram sign-in of user {login.user.id} failed: {type(e).__name__}: {e}")
        login.status, login.url, login.error = "failed", "", tg.explain(e)
        await tg._disconnect(login.client)


async def submit_password(user: User, password: str) -> Dict[str, Any]:
    """The two-step verification password of a sign-in waiting for it.
    Returns :func:`state` (``error: "bad_password"`` and still ``password``
    when it is wrong)."""
    from telethon.errors import PasswordHashInvalidError
    login = _logins.get(user.id)
    if login is None or login.status != "password":
        return {**state(user), "error": "not_waiting"}
    try:
        await login.client.sign_in(password=password)
    except PasswordHashInvalidError:
        return {**state(user), "error": "bad_password"}
    except Exception as e:
        logger.warning(f"Telegram password step of user {user.id} failed: {type(e).__name__}: {e}")
        login.status, login.error = "failed", tg.explain(e)
        await tg._disconnect(login.client)
        return state(user)
    await _finish(login)
    return state(user)


async def _finish(login: Login) -> None:
    """Store the signed-in session on the user's connection."""
    user = login.user
    me = await login.client.get_me()
    session = login.client.session.save()
    account = tg.display_name(me) or str(getattr(me, "id", ""))
    username = getattr(me, "username", None)
    if username:
        account = f"{account} (@{username})"
    with users.acting_as(user):
        if store.is_connected(user, config.ADDON):
            store.update(user, config.ADDON, credentials={config.SESSION: session}, account=account, by=user)
        else:
            store.connect(user, config.ADDON, credentials={config.SESSION: session}, account=account, by=user)
    tg.forget(user)
    login.status, login.account, login.url = "connected", account, ""
    await tg._disconnect(login.client)
    logger.info(f"Telegram account connected for user {user.id}")
