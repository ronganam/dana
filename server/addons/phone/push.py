"""Direct push to a user's phone app (replaces the Home Assistant companion route).

``send(user, type, data)`` stores the event in ``phone_events`` for ``user``
(one numbered outbox for the server) and wakes that user's registered devices
with a high-priority Firebase Cloud Messaging data message that carries
**only** the sequence number. The app then fetches ``GET
/api/phone/events?after=<seq>`` with its API token (only that user's events),
so no content goes through Google and a lost ping costs nothing: the next
ping, the app starting or its hourly sync fetches everything it missed. A
device belongs to the user whose token registered it; a user without the
``phone`` addon gets nothing (no event is stored for anyone).

Each event type has its own lifetime (``TTL``): an urgent alert from an hour
ago must not pop up when the phone comes back online, a voice transcript
still should.

Event types (``data`` fields) — handled by the app's ``PhoneEvents``:

- ``alert``: notification_id, key, package, app, title, text, priority, emoji, attempt
- ``suggestions``: ids (list), source_type, source_id, heading
- ``reminder``: task_id, task_title, priority, trigger_type
- ``reminder_clear``: task_id
- ``notification_action``: request_id (the app fetches the request itself)
- ``otp_request``: request_id (a money sync waits for an SMS sign-in code: the app
  fetches ``GET /api/money/code-requests/{id}`` and posts the code; 4 minutes)
- ``notify``: title, text, tag, channel (``messages`` | ``digest`` | ``important``)
- ``shopping``: list_id, version (silent: a shopping list the user is on changed;
  the widget / watch read it again; at most one per user per 10 seconds,
  addons/shopping/events.py)
- ``agent_sessions``: sessions (list; the user's coding-agent sessions, the
  whole state each time, so it replaces earlier ones; agent_sessions.py)

Config (the server's sender identity, env): ``FCM_SERVICE_ACCOUNT_JSON``
(service-account key, allowed only to send messages) and ``FCM_PROJECT_ID``
(defaults to the key's project). Every send marks the user's ``push`` mark
(``push:<user id>``) for the self-check.
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from core.db import get_db_connection
from core.users import User
from core.addons import store
from addons.phone.config import ADDON, mark

logger = logging.getLogger(__name__)

TTL = {
    "alert": 15 * 60,
    "notification_action": 120,
    "otp_request": 240,
    "suggestions": 6 * 3600,
    "reminder": 12 * 3600,
    "reminder_clear": 12 * 3600,
    "notify": 24 * 3600,
    "briefing": 14 * 3600,
    "context_card": 20 * 60,
    "weekly_review": 3 * 24 * 3600,
    "shopping": 10 * 60,
    "agent_sessions": 30 * 60,
}
DEFAULT_TTL = 3600
KEEP_EVENTS_DAYS = 2
# A device that hasn't fetched (or registered) for this long is gone (old phone).
STALE_DEVICE_DAYS = 60
MAX_FETCH = 50
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

_creds = None
_creds_lock = asyncio.Lock()


def _utc(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")


def configured() -> bool:
    return bool(os.getenv("FCM_SERVICE_ACCOUNT_JSON", "").strip())


def _project_id() -> str:
    pid = os.getenv("FCM_PROJECT_ID", "").strip()
    if pid:
        return pid
    try:
        return json.loads(os.getenv("FCM_SERVICE_ACCOUNT_JSON", "{}")).get("project_id", "")
    except ValueError:
        return ""


async def _access_token() -> Optional[str]:
    """OAuth token for the FCM HTTP v1 API (cached until it expires)."""
    global _creds
    if not configured():
        return None
    async with _creds_lock:
        if _creds is None:
            from google.oauth2 import service_account
            _creds = service_account.Credentials.from_service_account_info(
                json.loads(os.environ["FCM_SERVICE_ACCOUNT_JSON"]), scopes=[FCM_SCOPE])
        if not _creds.valid:
            from google.auth.transport.requests import Request
            await asyncio.to_thread(_creds.refresh, Request())
        return _creds.token


# --- Devices -----------------------------------------------------------------

def latest_seq(user: User) -> int:
    """The newest number of ``user``'s events (0: none)."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT MAX(seq) AS s FROM phone_events WHERE user_id = ?", (user.id,)).fetchone()
    return int(row["s"] or 0)


def register(user: User, device_id: str, token: str, name: str = "", app_version: str = "") -> Dict[str, Any]:
    """``user``'s app's FCM token (sent on start and whenever Firebase rotates
    it), on their own row for the device (``(user, device id)``; migration
    ``m0017``). Another user's row is never taken over by a device id alone
    (it is not secret): only the FCM token proves it is the same installed
    app, so rows of other users holding the *same token* are removed (the
    phone now belongs to ``user``; one row per token) and rows with another
    token are left alone (docs/access.md, "Security rules"). Returns the
    newest number of their events so a fresh install starts from now instead
    of replaying old events."""
    device_id, token = (device_id or "").strip()[:100], (token or "").strip()[:4096]
    if not device_id or not token:
        raise ValueError("device_id and token are required")
    now = _utc()
    with get_db_connection() as conn:
        # One row per token: a reinstall gets a new device id but may reuse it,
        # and a phone that signs in as someone else presents its own token.
        moved = conn.execute("DELETE FROM phone_devices WHERE token = ? AND NOT (user_id = ? AND device_id = ?)",
                             (token, user.id, device_id)).rowcount
        conn.execute(
            "INSERT INTO phone_devices (user_id, device_id, token, name, app_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, device_id) DO UPDATE SET token = excluded.token, "
            "name = excluded.name, app_version = excluded.app_version, updated_at = excluded.updated_at",
            (user.id, device_id, token, (name or "")[:100] or None, (app_version or "")[:40] or None, now, now))
        conn.commit()
    logger.info(f"Phone push: registered device {device_id[:8]} of user {user.id} ({name or '?'}, {app_version or '?'})"
                + (f"; {moved} older row(s) with its token replaced" if moved else ""))
    return {"ok": True, "latest_seq": latest_seq(user)}


def devices(user: User) -> List[Dict[str, Any]]:
    """``user``'s registered phones (no tokens), most recently updated first."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT device_id, name, app_version, created_at, updated_at, last_fetch_at, last_seq "
                            "FROM phone_devices WHERE user_id = ? ORDER BY updated_at DESC", (user.id,)).fetchall()
    return [dict(r) for r in rows]


def _drop_token(token: str) -> None:
    with get_db_connection() as conn:
        # scoped: a token FCM reported dead: the device row holding it goes, whoever's
        conn.execute("DELETE FROM phone_devices WHERE token = ?", (token,))
        conn.commit()


# --- Events ------------------------------------------------------------------

def events_after(user: User, after: int, device_id: str = "") -> Dict[str, Any]:
    """``user``'s unexpired events newer than ``after`` (oldest first) + the
    newest number of theirs.

    With ``device_id``: ``registered`` says whether the server still knows the
    device as ``user``'s (its token was dropped, the DB was restored, it
    registered for someone else, …), so the phone can register again instead
    of silently getting no pings."""
    now = _utc()
    with get_db_connection() as conn:
        rows = conn.execute("SELECT seq, type, data, created_at FROM phone_events WHERE user_id = ? AND seq > ? "
                            "AND expires_at > ? ORDER BY seq LIMIT ?",
                            (user.id, max(0, int(after)), now, MAX_FETCH)).fetchall()
        latest = conn.execute("SELECT MAX(seq) AS s FROM phone_events WHERE user_id = ?",
                              (user.id,)).fetchone()["s"] or 0
        registered = None
        if device_id:
            seen = max([after] + [r["seq"] for r in rows])
            cur = conn.execute("UPDATE phone_devices SET last_fetch_at = ?, last_seq = MAX(COALESCE(last_seq, 0), ?) "
                               "WHERE device_id = ? AND user_id = ?", (now, seen, device_id[:100], user.id))
            registered = cur.rowcount > 0
            conn.commit()
    res = {"events": [{"seq": r["seq"], "type": r["type"], "data": json.loads(r["data"]),
                       "created_at": r["created_at"]} for r in rows],
           "latest": int(latest)}
    if registered is not None:
        res["registered"] = registered
    return res


def _store(user: User, type_: str, data: Dict[str, Any], replace: bool = False) -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(seconds=TTL.get(type_, DEFAULT_TTL))
    with get_db_connection() as conn:
        if replace:  # the new event carries the whole state: earlier ones of the type are superseded
            conn.execute("DELETE FROM phone_events WHERE user_id = ? AND type = ?", (user.id, type_))
        cur = conn.execute("INSERT INTO phone_events (user_id, type, data, created_at, expires_at) "
                           "VALUES (?, ?, ?, ?, ?)",
                           (user.id, type_, json.dumps(data, ensure_ascii=False), _utc(now), _utc(expires)))
        # Keep the numbering (AUTOINCREMENT), drop old rows and long-silent devices.
        # scoped: server-wide retention of old push events (KEEP_EVENTS_DAYS)
        conn.execute("DELETE FROM phone_events WHERE created_at < ?",
                     (_utc(now - datetime.timedelta(days=KEEP_EVENTS_DAYS)),))
        # scoped: server-wide cleanup of devices silent for STALE_DEVICE_DAYS
        conn.execute("DELETE FROM phone_devices WHERE COALESCE(last_fetch_at, updated_at) < ?",
                     (_utc(now - datetime.timedelta(days=STALE_DEVICE_DAYS)),))
        conn.commit()
        return int(cur.lastrowid)


async def _ping(client: httpx.AsyncClient, token: str, seq: int, type_: str, auth: str) -> bool:
    """One FCM data message. Drops tokens FCM says are gone."""
    body = {"message": {"token": token, "data": {"seq": str(seq), "type": type_},
                        "android": {"priority": "HIGH", "ttl": f"{TTL.get(type_, DEFAULT_TTL)}s"}}}
    url = f"https://fcm.googleapis.com/v1/projects/{_project_id()}/messages:send"
    for attempt in range(2):
        try:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {auth}"})
        except httpx.HTTPError as e:
            logger.warning(f"Phone push: FCM request failed ({type(e).__name__})")
            continue
        if r.status_code < 300:
            return True
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        codes = {d.get("errorCode") for d in err.get("details") or [] if isinstance(d, dict)}
        if r.status_code == 404 or "UNREGISTERED" in codes or (
                r.status_code == 400 and "registration token" in (err.get("message") or "")):
            logger.warning("Phone push: FCM token no longer valid, removing the device")
            _drop_token(token)
            return False
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            await asyncio.sleep(1.5)
            continue
        logger.error(f"Phone push: FCM {r.status_code} {err.get('status')}: {(err.get('message') or '')[:200]}")
        return False
    return False


async def send(user: User, type_: str, data: Dict[str, Any], replace: bool = False) -> bool:
    """Queue an event for ``user``'s phone and wake their devices (only
    theirs). True when at least one device's ping was accepted by FCM (the
    event stays fetchable either way until it expires). Without the ``phone``
    kind nothing is stored and False is returned. ``replace``: the event
    carries the whole state of its type, so the user's earlier events of the
    type are deleted (a phone catching up gets only the newest)."""
    if not store.is_connected(user, ADDON):
        logger.debug(f"Phone push: {type_} not sent, user {user.id} has no phone addon")
        return False
    seq = _store(user, type_, {k: v for k, v in data.items() if v is not None}, replace=replace)
    with get_db_connection() as conn:
        tokens = [r["token"] for r in conn.execute("SELECT token FROM phone_devices WHERE user_id = ?",
                                                   (user.id,)).fetchall()]
    if not tokens:
        logger.warning(f"Phone push: {type_} #{seq} queued, but user {user.id} has no phone registered")
        mark("push", user.id, False, "no phone registered")
        return False
    try:
        auth = await _access_token()
    except Exception as e:
        logger.error(f"Phone push: couldn't get an FCM access token: {e}")
        mark("push", user.id, False, "no FCM access token")
        return False
    if not auth:
        logger.warning(f"Phone push: {type_} #{seq} queued, FCM isn't configured")
        mark("push", user.id, False, "FCM isn't configured")
        return False
    async with httpx.AsyncClient(timeout=10.0) as client:
        results = await asyncio.gather(*(_ping(client, t, seq, type_, auth) for t in tokens))
    ok = any(results)
    logger.info(f"Phone push: {type_} #{seq} → {sum(results)}/{len(tokens)} device(s) of user {user.id}")
    mark("push", user.id, ok, f"{type_}: {sum(results)}/{len(tokens)} device(s)")
    return ok


async def notify(user: User, title: str, text: str, tag: str = "", channel: str = "messages") -> bool:
    """A plain notification on ``user``'s phone (posted by the app)."""
    return await send(user, "notify", {"title": title[:200], "text": text[:4000], "tag": tag[:120] or None,
                                       "channel": channel})
