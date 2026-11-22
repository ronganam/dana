"""Direct push to a user's phone app (replaces the Home Assistant companion route).

``send(user, type, data)`` stores the event in ``phone_events`` for ``user``
(one numbered outbox for the server) and wakes that user's registered devices
with a ping that carries **only** the sequence number and the type. The app
then fetches ``GET /api/phone/events?after=<seq>`` with its API token (only
that user's events), so no content goes through the push service and a lost
ping costs nothing: the next ping, the app starting or its hourly sync fetches
everything it missed. The devices are core's (core/devices.py): a paired
device registers itself (``phone_push``, keyed by the device, which belongs to
the user whose token it has); a user without the ``phone`` addon gets nothing
(no event is stored for anyone).

Each registration names its **transport**:

- ``fcm``: a Firebase Cloud Messaging token, pinged with a high-priority data
  message (only for an app built with your own Firebase project; refused
  unless ``FCM_SERVICE_ACCOUNT_JSON`` is set);
- ``unifiedpush``: a UnifiedPush endpoint the phone's distributor app (ntfy)
  gave the app; pinged with a plain POST of the same ``{"seq", "type"}`` JSON.
  The endpoint must be https (http only with ``DANA_INSECURE_DEV=1``), may not
  carry a user name or password, and is never followed through a redirect; a
  ping is the fixed body with a short timeout and its answer is not read
  beyond the status, so an endpoint on the LAN (your own ntfy) is allowed.
  Only its host is ever logged.

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

Config (FCM, the server's sender identity, env): ``FCM_SERVICE_ACCOUNT_JSON``
(service-account key, allowed only to send messages) and ``FCM_PROJECT_ID``
(defaults to the key's project). UnifiedPush needs no config. Every send marks the user's ``push`` mark
(``push:<user id>``) for the self-check.
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Any, Dict, List, Optional

from urllib.parse import urlsplit

import aiohttp
import httpx

from core.db import get_db_connection
from core.devices import Device
from core.users import INSECURE_DEV_ENV, User
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
TRANSPORTS = ("fcm", "unifiedpush")
MAX_ENDPOINT = 2048
UNIFIEDPUSH_TIMEOUT = aiohttp.ClientTimeout(total=8, sock_connect=5)
RETRY_DELAY = 1.5

_creds = None
_creds_lock = asyncio.Lock()


def _utc(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")


def configured() -> bool:
    """FCM is set up on the server (``FCM_SERVICE_ACCOUNT_JSON``)."""
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


class Refused(ValueError):
    """A push registration the server will not store: ``code`` is the API's
    error (``invalid``, ``fcm_not_configured``, ``invalid_endpoint``)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _insecure_dev() -> bool:
    return os.getenv(INSECURE_DEV_ENV, "").strip() == "1"


def endpoint_host(endpoint: str) -> str:
    """The host of a UnifiedPush endpoint: all of it that is ever logged or shown."""
    try:
        return (urlsplit(endpoint).hostname or "?").lower()
    except ValueError:
        return "?"


def check_endpoint(endpoint: str) -> str:
    """``endpoint`` cleaned, if it is one the server may POST pings to: https
    (http only with ``DANA_INSECURE_DEV=1``), a host, no user name or
    password, at most ``MAX_ENDPOINT`` characters. Needs no network.

    Raises:
        Refused: ``invalid_endpoint`` and why (never the endpoint itself).
    """
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise Refused("invalid", "endpoint is required")
    if len(endpoint) > MAX_ENDPOINT:
        raise Refused("invalid_endpoint", f"The endpoint is longer than {MAX_ENDPOINT} characters.")
    if any(c.isspace() or ord(c) < 32 for c in endpoint):
        raise Refused("invalid_endpoint", "The endpoint may not contain spaces or control characters.")
    try:
        parts = urlsplit(endpoint)
        parts.port
    except ValueError:
        raise Refused("invalid_endpoint", "The endpoint is not a valid URL.") from None
    scheme = parts.scheme.lower()
    if scheme != "https" and not (scheme == "http" and _insecure_dev()):
        raise Refused("invalid_endpoint", "The endpoint must be an https URL.")
    if not parts.hostname:
        raise Refused("invalid_endpoint", "The endpoint has no host.")
    if parts.username is not None or parts.password is not None:
        raise Refused("invalid_endpoint", "The endpoint may not carry a user name or password.")
    return endpoint


def register(user: User, device: Device, transport: str, address: str, app_version: str = "") -> Dict[str, Any]:
    """The push registration of ``device`` (``user``'s, the request's own
    device: the app sends it on start and whenever its token or endpoint
    changes): ``transport`` ``fcm`` with an FCM token as ``address``, or
    ``unifiedpush`` with the distributor's endpoint. One registration per
    device and one per address: a row of another device with the same address
    (the app reinstalled and paired again, or paired as someone else) is
    removed, since only the installed app holds it (docs/access.md, "Security
    rules"). Returns the newest number of the user's events so a fresh install
    starts from now instead of replaying old events.

    Raises:
        Refused: an unknown transport, no address, FCM not configured, an
            endpoint that is not allowed.
        ValueError: ``device`` is not ``user``'s.
    """
    if device.user_id != user.id:
        raise ValueError("not this user's device")
    if transport not in TRANSPORTS:
        raise Refused("invalid", f"transport must be one of: {', '.join(TRANSPORTS)}")
    if transport == "fcm":
        address = (address or "").strip()[:4096]
        if not address:
            raise Refused("invalid", "token is required")
        if not configured():
            raise Refused("fcm_not_configured", "This server has no Firebase key (FCM_SERVICE_ACCOUNT_JSON), so it "
                          "cannot push through FCM. Use UnifiedPush (the ntfy app) instead.")
    else:
        address = check_endpoint(address)
    now = _utc()
    with get_db_connection() as conn:
        # scoped: one row per push address, whoever's: only the installed app holds it
        moved = conn.execute("DELETE FROM phone_push WHERE transport = ? AND address = ? AND device_id != ?",
                             (transport, address, device.id)).rowcount
        conn.execute(
            "INSERT INTO phone_push (device_id, user_id, transport, address, app_version, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(device_id) DO UPDATE SET transport = excluded.transport, "
            "address = excluded.address, app_version = excluded.app_version, updated_at = excluded.updated_at",
            (device.id, user.id, transport, address, (app_version or "")[:40] or None, now))
        conn.commit()
    via = f"UnifiedPush via {endpoint_host(address)}" if transport == "unifiedpush" else "FCM"
    logger.info(f"Phone push: registered device {device.id} of user {user.id} ({device.kind}, {via}, "
                f"{app_version or '?'})" + (f"; {moved} older registration(s) with its address replaced" if moved else ""))
    return {"ok": True, "transport": transport, "latest_seq": latest_seq(user)}


def devices(user: User) -> List[Dict[str, Any]]:
    """``user``'s devices registered for push, most recently updated first:
    the transport and, for UnifiedPush, the endpoint's host (never a token or
    an endpoint)."""
    with get_db_connection() as conn:
        rows = conn.execute("SELECT p.device_id, d.name, d.kind, p.transport, p.address, p.app_version, d.created_at, "
                            "p.updated_at, p.last_fetch_at, p.last_seq FROM phone_push p "
                            "JOIN devices d ON d.id = p.device_id WHERE p.user_id = ? ORDER BY p.updated_at DESC",
                            (user.id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        address = d.pop("address")
        d["endpoint_host"] = endpoint_host(address) if d["transport"] == "unifiedpush" else None
        out.append(d)
    return out


def _drop(transport: str, address: str) -> None:
    with get_db_connection() as conn:
        # scoped: an address its push service reported gone: the registration holding it goes, whoever's
        conn.execute("DELETE FROM phone_push WHERE transport = ? AND address = ?", (transport, address))
        conn.commit()


# --- Events ------------------------------------------------------------------

def events_after(user: User, after: int, device: Optional[Device] = None) -> Dict[str, Any]:
    """``user``'s unexpired events newer than ``after`` (oldest first) + the
    newest number of theirs.

    With ``device`` (the request's own): ``registered`` says whether its push
    registration is still there (its FCM token was dropped, the DB was
    restored, …), so the app can register again instead of silently getting
    no pings."""
    now = _utc()
    with get_db_connection() as conn:
        rows = conn.execute("SELECT seq, type, data, created_at FROM phone_events WHERE user_id = ? AND seq > ? "
                            "AND expires_at > ? ORDER BY seq LIMIT ?",
                            (user.id, max(0, int(after)), now, MAX_FETCH)).fetchall()
        latest = conn.execute("SELECT MAX(seq) AS s FROM phone_events WHERE user_id = ?",
                              (user.id,)).fetchone()["s"] or 0
        registered = None
        if device is not None and device.user_id == user.id:
            seen = max([after] + [r["seq"] for r in rows])
            cur = conn.execute("UPDATE phone_push SET last_fetch_at = ?, last_seq = MAX(COALESCE(last_seq, 0), ?) "
                               "WHERE device_id = ? AND user_id = ?", (now, seen, device.id, user.id))
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
        # Keep the numbering (AUTOINCREMENT), drop old rows and the push registrations of long-silent devices.
        # scoped: server-wide retention of old push events (KEEP_EVENTS_DAYS)
        conn.execute("DELETE FROM phone_events WHERE created_at < ?",
                     (_utc(now - datetime.timedelta(days=KEEP_EVENTS_DAYS)),))
        # scoped: server-wide cleanup of push registrations silent for STALE_DEVICE_DAYS
        conn.execute("DELETE FROM phone_push WHERE COALESCE(last_fetch_at, updated_at) < ?",
                     (_utc(now - datetime.timedelta(days=STALE_DEVICE_DAYS)),))
        conn.commit()
        return int(cur.lastrowid)


def ping_data(seq: int, type_: str) -> Dict[str, str]:
    """What every ping carries, on every transport: the event's number and type (no content)."""
    return {"seq": str(seq), "type": type_}


async def _ping(client: httpx.AsyncClient, token: str, seq: int, type_: str, auth: str) -> bool:
    """One FCM data message. Drops tokens FCM says are gone."""
    body = {"message": {"token": token, "data": ping_data(seq, type_),
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
            _drop("fcm", token)
            return False
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            await asyncio.sleep(RETRY_DELAY)
            continue
        logger.error(f"Phone push: FCM {r.status_code} {err.get('status')}: {(err.get('message') or '')[:200]}")
        return False
    return False


async def _ping_unifiedpush(session: aiohttp.ClientSession, endpoint: str, seq: int, type_: str) -> bool:
    """One UnifiedPush ping: a POST of :func:`ping_data` as JSON to the
    endpoint (the distributor's server, e.g. ntfy, hands the body to the app).
    The endpoint is checked again first (``DANA_INSECURE_DEV`` may have been
    turned off); redirects are not followed; 404 / 410 drop the
    registration; 429 / 5xx are retried once."""
    host = endpoint_host(endpoint)
    try:
        check_endpoint(endpoint)
    except Refused as e:
        logger.warning(f"Phone push: UnifiedPush endpoint on {host} refused: {e}")
        return False
    body = json.dumps(ping_data(seq, type_), separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "TTL": str(TTL.get(type_, DEFAULT_TTL)), "Urgency": "high"}
    for attempt in range(2):
        try:
            async with session.post(endpoint, data=body, headers=headers, allow_redirects=False) as r:
                status = r.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Phone push: UnifiedPush request to {host} failed ({type(e).__name__})")
            continue
        if status < 300:
            return True
        if status in (404, 410):
            logger.warning(f"Phone push: UnifiedPush endpoint on {host} is gone ({status}), removing the device")
            _drop("unifiedpush", endpoint)
            return False
        if status in (429, 500, 502, 503, 504) and attempt == 0:
            await asyncio.sleep(RETRY_DELAY)
            continue
        logger.error(f"Phone push: UnifiedPush {status} from {host}"
                     + (" (redirects are not followed)" if 300 <= status < 400 else ""))
        return False
    return False


async def send(user: User, type_: str, data: Dict[str, Any], replace: bool = False) -> bool:
    """Queue an event for ``user``'s phone and wake their devices (only
    theirs). True when at least one device's ping was accepted (by FCM or the
    UnifiedPush endpoint; the event stays fetchable either way until it
    expires). Without the ``phone`` addon nothing is stored and False is
    returned. ``replace``: the event carries the whole state of its type, so
    the user's earlier events of the type are deleted (a phone catching up
    gets only the newest)."""
    if not store.is_connected(user, ADDON):
        logger.debug(f"Phone push: {type_} not sent, user {user.id} has no phone addon")
        return False
    seq = _store(user, type_, {k: v for k, v in data.items() if v is not None}, replace=replace)
    with get_db_connection() as conn:
        rows = [(r["transport"], r["address"]) for r in conn.execute(
            "SELECT transport, address FROM phone_push WHERE user_id = ?", (user.id,)).fetchall()]
    if not rows:
        logger.warning(f"Phone push: {type_} #{seq} queued, but user {user.id} has no phone registered")
        mark("push", user.id, False, "no phone registered")
        return False
    tokens = [a for t, a in rows if t == "fcm"]
    endpoints = [a for t, a in rows if t == "unifiedpush"]
    problems: List[str] = []
    auth = None
    if tokens:
        try:
            auth = await _access_token()
        except Exception as e:
            logger.error(f"Phone push: couldn't get an FCM access token: {e}")
            problems.append("no FCM access token")
        else:
            if not auth:
                logger.warning(f"Phone push: {type_} #{seq} not sent to FCM devices, FCM isn't configured")
                problems.append("FCM isn't configured")
    pings = []
    async with httpx.AsyncClient(timeout=10.0) as client, aiohttp.ClientSession(timeout=UNIFIEDPUSH_TIMEOUT) as session:
        if auth:
            pings += [_ping(client, t, seq, type_, auth) for t in tokens]
        pings += [_ping_unifiedpush(session, e, seq, type_) for e in endpoints]
        results = list(await asyncio.gather(*pings))
    ok = any(results)
    logger.info(f"Phone push: {type_} #{seq} → {sum(results)}/{len(rows)} device(s) of user {user.id}")
    if not results:
        mark("push", user.id, False, problems[0] if problems else "no device reached")
        return False
    mark("push", user.id, ok, f"{type_}: {sum(results)}/{len(rows)} device(s)"
         + (f" ({', '.join(problems)})" if problems else ""))
    return ok


async def notify(user: User, title: str, text: str, tag: str = "", channel: str = "messages") -> bool:
    """A plain notification on ``user``'s phone (posted by the app)."""
    return await send(user, "notify", {"title": title[:200], "text": text[:4000], "tag": tag[:120] or None,
                                       "channel": channel})
