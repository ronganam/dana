"""The Google sign-in: one consent connects every Google addon (docs/access.md, "Google account").
Run: bin/test test_google_account

Google's token endpoint is faked (``oauth._post_token``): each code answers
with its own refresh token, scopes and id token. Checked: the consent URL
(scopes of the addons the user may connect, offline + consent, the redirect
address from ``PUBLIC_URL`` or the request), the one-time state bound to its
user, the callback's outcomes (connected, updated, denied, expired, failed),
that only addons whose scopes were granted get the token and an addon already
connected keeps its settings, that one user's consent never touches another
user's connections, the status endpoint, no token in any answer, and the
credentials cache per connection. No network.
"""

import asyncio
import base64
import json
import os
import sys
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlsplit

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("google_account_test")
os.environ["API_TOKEN"] = "google-account-test-env-token"  # a test value: the first admin's token (a headless install)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["GOOGLE_CLIENT_ID"] = "server-client-id"  # the server's OAuth app (fake)
os.environ["GOOGLE_CLIENT_SECRET"] = "server-client-secret"
os.environ.pop("PUBLIC_URL", None)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()

from core.auth import setup_auth  # noqa: E402
from core.addons import Field, Addon, store  # noqa: E402
from addons.google_account import api as google_api, oauth  # noqa: E402
import addons  # noqa: E402,F401 — registers every addon

results: List[Tuple[str, bool]] = []
bodies: List[str] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# A second Google addon, as contacts / gmail are: its own scope, admins only (so a member is not asked for it).
MAIL_SCOPE = "https://example.test/auth/mail"
CAL_SCOPE = "https://www.googleapis.com/auth/calendar"
CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"
testmail = Addon("testmail", credentials=(Field("refresh_token"),), settings=(Field("label", default="inbox"),),
                       requires="manage_server", connect_url=oauth.CONNECT_PATH)
oauth.register("testmail", (MAIL_SCOPE,))

ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Levi", "member")
CARL = users.create("Carl Cohen", "member")
AUTH = {ADMIN.id: {"Authorization": f"Bearer {os.environ['API_TOKEN']}"},
        BOB.id: {"Authorization": f"Bearer {users.issue_token(BOB.id, 'test')[0]}"},
        CARL.id: {"Authorization": f"Bearer {users.issue_token(CARL.id, 'test')[0]}"}}


def id_token(email: str) -> str:
    part = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"header.{part}.signature"


# code → (HTTP status, body) of Google's token endpoint; every request is recorded.
CODES: Dict[str, Tuple[int, Dict[str, Any]]] = {
    "robin-all": (200, {"refresh_token": "robin-refresh-1", "scope": f"openid {CAL_SCOPE} {MAIL_SCOPE}",
                      "id_token": id_token("robin@example.test")}),
    "robin-again": (200, {"refresh_token": "robin-refresh-2", "scope": f"{CAL_SCOPE} openid",
                        "id_token": id_token("robin@example.test")}),
    "robin-nothing": (200, {"refresh_token": "robin-refresh-3", "scope": "openid", "id_token": id_token("robin@example.test")}),
    "bob-cal": (200, {"refresh_token": "bob-refresh-1", "scope": f"{CAL_SCOPE} {MAIL_SCOPE} openid",
                      "id_token": id_token("bob@example.test")}),
    "no-refresh": (200, {"access_token": "at", "scope": CAL_SCOPE}),
    "refused": (400, {"error": "invalid_grant"}),
}
POSTED: List[Dict[str, str]] = []


async def fake_post_token(data: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    POSTED.append(dict(data))
    return CODES.get(data["code"], (400, {"error": "invalid_grant"}))


oauth._post_token = fake_post_token


def build_app() -> web.Application:
    app = web.Application()
    setup_auth(app)
    google_api.setup(app)
    return app


async def get(client: TestClient, path: str, user_id: str = "", **params: str) -> Tuple[int, str, Any]:
    """``(status, Location, body)`` without following the redirect."""
    r = await client.get(path, params=params, headers=AUTH.get(user_id, {}), allow_redirects=False)
    text = await r.text()
    bodies.append(text + " " + r.headers.get("Location", ""))
    try:
        body = json.loads(text)
    except ValueError:
        body = text
    return r.status, r.headers.get("Location", ""), body


async def start(client: TestClient, user_id: str) -> Dict[str, str]:
    """Start a sign-in for the user: the query of Google's consent URL."""
    status, location, _ = await get(client, "/api/google/connect", user_id)
    assert status == 302 and location.startswith(oauth.AUTH_URI + "?"), (status, location)
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


async def finish(client: TestClient, user_id: str, code: str, state: str) -> str:
    status, location, _ = await get(client, "/api/google/callback", user_id, code=code, state=state)
    assert status == 302, status
    return location


def token_of(user: users.User, addon: str) -> str:
    conn = store.get(user, addon)
    return conn.credentials.get("refresh_token", "") if conn is not None else ""


async def test_consent_url(client: TestClient) -> None:
    status, _, body = await get(client, "/api/google/connect")
    check("connect: needs a signed-in user", status == 401, (status, body))
    q = await start(client, ADMIN.id)
    check("connect: the server's client, a code for offline access, consent asked every time",
          q["client_id"] == "server-client-id" and q["response_type"] == "code" and q["access_type"] == "offline"
          and q["prompt"] == "consent" and len(q["state"]) >= 32, q)
    check("connect: the admin is asked for every Google addon's scopes and who they are",
          q["scope"].split() == [CAL_SCOPE, CONTACTS_SCOPE, GMAIL_SCOPE, GMAIL_SETTINGS_SCOPE, MAIL_SCOPE, "openid", "email"], q["scope"])
    check("connect: without PUBLIC_URL the redirect address is the one the request came to",
          q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/api/google/callback"),
          q["redirect_uri"])
    r = await client.get("/api/google/connect", headers={**AUTH[ADMIN.id], "X-Forwarded-Proto": "https"},
                         allow_redirects=False)
    forwarded = parse_qs(urlsplit(r.headers["Location"]).query)["redirect_uri"][0]
    check("connect: behind the ingress the forwarded scheme is used", forwarded.startswith("https://127.0.0.1:"),
          forwarded)
    os.environ["PUBLIC_URL"] = "https://dana.example.test/"
    q_public = await start(client, ADMIN.id)
    os.environ.pop("PUBLIC_URL")
    check("connect: PUBLIC_URL decides the redirect address",
          q_public["redirect_uri"] == "https://dana.example.test/api/google/callback", q_public["redirect_uri"])
    q_bob = await start(client, BOB.id)
    check("connect: a member is not asked for an addon their level cannot connect",
          q_bob["scope"].split() == [CAL_SCOPE, CONTACTS_SCOPE, GMAIL_SCOPE, GMAIL_SETTINGS_SCOPE, "openid", "email"], q_bob["scope"])

    os.environ["GOOGLE_CLIENT_ID"] = ""
    status, location, body = await get(client, "/api/google/connect", ADMIN.id)
    os.environ["GOOGLE_CLIENT_ID"] = "server-client-id"
    check("connect: 503 without the server's OAuth client, nothing sent to Google",
          status == 503 and not location and body["error"] == "google_not_configured", (status, body))


async def test_callback(client: TestClient) -> None:
    POSTED.clear()
    q = await start(client, ADMIN.id)
    location = await finish(client, ADMIN.id, "robin-all", q["state"])
    check("callback: back to the Addons page as connected", location == "/?google=connected#addons", location)
    check("callback: the code went to Google with the server's client and the same redirect address",
          len(POSTED) == 1 and POSTED[0]["code"] == "robin-all" and POSTED[0]["grant_type"] == "authorization_code"
          and POSTED[0]["client_secret"] == "server-client-secret" and POSTED[0]["redirect_uri"] == q["redirect_uri"],
          POSTED)
    cal, mail = store.get(ADMIN, "calendar"), store.get(ADMIN, "testmail")
    check("callback: every addon whose scopes were granted is connected with the token and the account's email",
          cal is not None and mail is not None and token_of(ADMIN, "calendar") == "robin-refresh-1"
          and token_of(ADMIN, "testmail") == "robin-refresh-1" and cal.account == "robin@example.test"
          and mail.account == "robin@example.test", (cal, mail))

    location = await finish(client, ADMIN.id, "robin-again", q["state"])
    check("callback: a state works once", location == "/?google=expired#addons"
          and token_of(ADMIN, "calendar") == "robin-refresh-1" and len(POSTED) == 1, location)

    store.update(ADMIN, "calendar", settings={"calendar_id": "robin-cal"})
    q = await start(client, ADMIN.id)
    location = await finish(client, ADMIN.id, "robin-again", q["state"])
    check("callback: signing in again replaces the token of a connected addon and keeps its settings",
          location == "/?google=connected#addons" and token_of(ADMIN, "calendar") == "robin-refresh-2"
          and store.require(ADMIN, "calendar").settings["calendar_id"] == "robin-cal", location)
    check("callback: an addon whose scopes were not granted this time keeps what it had",
          token_of(ADMIN, "testmail") == "robin-refresh-1")

    q = await start(client, ADMIN.id)
    location = await finish(client, ADMIN.id, "robin-nothing", q["state"])
    check("callback: no addon's scopes granted → denied, nothing changed",
          location == "/?google=denied#addons" and token_of(ADMIN, "calendar") == "robin-refresh-2"
          and token_of(ADMIN, "testmail") == "robin-refresh-1", location)

    q = await start(client, ADMIN.id)
    before = len(POSTED)
    status, location, _ = await get(client, "/api/google/callback", ADMIN.id, error="access_denied", state=q["state"])
    check("callback: the user cancelled at Google → denied, Google not asked",
          status == 302 and location == "/?google=denied#addons" and len(POSTED) == before, location)

    for code, what in (("refused", "Google refused the code"), ("no-refresh", "Google returned no refresh token")):
        q = await start(client, ADMIN.id)
        location = await finish(client, ADMIN.id, code, q["state"])
        check(f"callback: {what} → failed, nothing changed",
              location == "/?google=failed#addons" and token_of(ADMIN, "calendar") == "robin-refresh-2", location)

    status, location, _ = await get(client, "/api/google/callback", ADMIN.id, code="robin-all", state="made-up")
    check("callback: an unknown state → expired", status == 302 and location == "/?google=expired#addons")
    status, _, _ = await get(client, "/api/google/callback", code="robin-all", state="made-up")
    check("callback: needs a signed-in user", status == 401, status)


async def test_isolation(client: TestClient) -> None:
    # A state belongs to the user who started the sign-in.
    q = await start(client, ADMIN.id)
    before = len(POSTED)
    location = await finish(client, BOB.id, "bob-cal", q["state"])
    check("isolation: another user cannot finish someone's sign-in (and it is used up)",
          location == "/?google=expired#addons" and len(POSTED) == before and store.get(BOB, "calendar") is None
          and (await finish(client, ADMIN.id, "robin-all", q["state"])) == "/?google=expired#addons", location)

    q = await start(client, BOB.id)
    location = await finish(client, BOB.id, "bob-cal", q["state"])
    check("isolation: Bob's consent connects Bob's calendar with Bob's token",
          location == "/?google=connected#addons" and token_of(BOB, "calendar") == "bob-refresh-1"
          and store.require(BOB, "calendar").account == "bob@example.test", location)
    check("isolation: an addon Bob's level cannot connect stays unconnected even when Google granted its scope",
          store.get(BOB, "testmail") is None)
    check("isolation: the admin's connections are untouched, Carl has none",
          token_of(ADMIN, "calendar") == "robin-refresh-2" and token_of(ADMIN, "testmail") == "robin-refresh-1"
          and store.require(ADMIN, "calendar").account == "robin@example.test"
          and store.get(CARL, "calendar") is None)

    status, _, body = await get(client, "/api/google/status", BOB.id)
    check("status: the caller's own Google addons",
          status == 200 and body["configured"] is True and body["redirect_uri"].endswith("/api/google/callback")
          and body["addons"] == [{"name": "calendar", "connected": True, "account": "bob@example.test"},
                                {"name": "contacts", "connected": False, "account": None},
                                {"name": "gmail", "connected": False, "account": None},
                                {"name": "testmail", "connected": False, "account": None}], body)
    status, _, body = await get(client, "/api/google/status", CARL.id)
    check("status: nothing connected for Carl", status == 200 and not any(k["connected"] for k in body["addons"]), body)
    status, _, _ = await get(client, "/api/google/status")
    check("status: needs a signed-in user", status == 401, status)

    secrets = ("robin-refresh", "bob-refresh", "server-client-secret")
    check("no refresh token or client secret in any answer or redirect",
          not any(s in b for b in bodies for s in secrets))


def test_credentials() -> None:
    cal, mail = store.require(ADMIN, "calendar"), store.require(ADMIN, "testmail")
    a, a2, m, b = (oauth.credentials(cal), oauth.credentials(cal), oauth.credentials(mail),
                   oauth.credentials(store.require(BOB, "calendar")))
    check("credentials: cached per connection (user and addon), each with its own token and the server's client",
          a is a2 and a is not m and a is not b and a.refresh_token == "robin-refresh-2"
          and m.refresh_token == "robin-refresh-1" and b.refresh_token == "bob-refresh-1"
          and a.client_id == "server-client-id" and a.token_uri == oauth.TOKEN_URI)
    store.update(ADMIN, "calendar", credentials={"refresh_token": "robin-refresh-9"})
    a3 = oauth.credentials(store.require(ADMIN, "calendar"))
    check("credentials: a new token starts over for that connection only",
          a3 is not a and (ADMIN.id, "calendar", "robin-refresh-2") not in oauth._credentials
          and oauth.credentials(mail) is m and oauth.credentials(store.require(BOB, "calendar")) is b)
    os.environ["GOOGLE_CLIENT_SECRET"] = ""
    try:
        oauth.credentials(cal)
        raised = ""
    except ValueError as e:
        raised = str(e)
    os.environ["GOOGLE_CLIENT_SECRET"] = "server-client-secret"
    check("credentials: refused without the server's OAuth client, naming no secret",
          "GOOGLE_CLIENT_ID" in raised and "robin-refresh" not in raised, raised)

    class Http(Exception):
        def __init__(self, status: int) -> None:
            self.resp = type("R", (), {"status": status})()

    check("failure reasons: missing access says to connect Google again; no token in any",
          "connect Google again" in oauth.failure_reason(Http(403), "Gmail")
          and oauth.failure_reason(Http(500), "Gmail") == "Gmail answered HTTP 500."
          and "ConnectionError" in oauth.failure_reason(ConnectionError("robin-refresh-9"), "Gmail")
          and "robin-refresh" not in oauth.failure_reason(ConnectionError("robin-refresh-9"), "Gmail"))
    check("id token: the email is read, anything else is no account",
          oauth._email_of(id_token("x@example.test")) == "x@example.test" and oauth._email_of("garbage") is None
          and oauth._email_of(None) is None)


async def main() -> None:
    async with TestClient(TestServer(build_app())) as client:
        await test_consent_url(client)
        await test_callback(client)
        await test_isolation(client)
    test_credentials()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
