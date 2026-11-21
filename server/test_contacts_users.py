"""Google Contacts per user (docs/contacts.md; docs/access.md, "Kinds"; docs/people.md).
Run: python test_contacts_users.py

Google's client is faked where it is built (``integrations.contacts.client.build``):
each refresh token opens its own fake account with its own contacts, etags,
change log and sync tokens, and every request is recorded with the account it
went to and the user it ran as. An 8.1.0 database is migrated first. Robin (the
first admin) and Bob have ``contacts`` and ``people``, each with their own
account; Eve has ``contacts`` without ``people``; Carl has ``people`` without
``contacts``. Checked: the sync (full with paging, incremental with the
stored sync token, deletions, an expired sync token, after connecting, after
another refresh token, the loop's run per user with one failing), the copy
and the tools per user in both directions (search by name in both scripts,
number and email; one contact in full; add / update only as ``confirm``
tools, the update sending the stored etag and refreshing the copy; a contact
changed or deleted in Google meanwhile), the approval wording, people filled
in from contacts (and what is never overwritten, never created), the
caller's name for a new caller and for the call card, the connection check,
disconnecting, and that Carl has no tools (refused like an unknown one), no
words about contacts in his prompt and nothing filled. No network.
"""

import asyncio
import json
import os
import sqlite3
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
DB_PATH = testenv.isolate("contacts_users_test")
os.environ["API_TOKEN"] = "contacts-users-test-env-token"  # a test value
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()  # a throwaway key for the fake credentials
os.environ["GOOGLE_CLIENT_ID"] = "server-client-id"  # the server's OAuth app (fake)
os.environ["GOOGLE_CLIENT_SECRET"] = "server-client-secret"
os.environ["OWNER_NAME"] = "Robin"
os.environ["TIMEZONE"] = "Asia/Jerusalem"
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# The production database before this kind existed (8.1.0, with a person): migrated below.
_db = sqlite3.connect(DB_PATH)
with open(os.path.join(ROOT, "test_fixtures", "db_8_1_0.sql"), encoding="utf-8") as f:
    _db.executescript(f.read())
_db.close()

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from integrations import bootstrap as integrations_bootstrap  # noqa: E402
integrations_bootstrap.bootstrap()

from core import agent, approval_text  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from integrations.contacts import client as contacts_client, enrich, store as contacts_store, sync  # noqa: E402
from integrations.contacts import config as contacts_config  # noqa: E402
from integrations.framework import registry, store  # noqa: E402
from integrations.google_account import oauth  # noqa: E402
from integrations.people import detail, linking, merge, store as people_store  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# --- A fake Google People API, one account per refresh token --------------------------------------

class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"<HttpError {status} \"{message}\">")
        self.resp = SimpleNamespace(status=status)
        self.content = message.encode()


EXPIRED = ('Sync token is expired. Clear local cache and retry call without the sync token. '
           '[{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "EXPIRED_SYNC_TOKEN"}]')
ETAG_MISMATCH = ("Request person.etag is different than the current person.etag. Clear local cache and get the "
                 "latest person. Reason: failedPrecondition")


def person(rid: str, name: Optional[str] = None, phones: Tuple = (), emails: Tuple = (),
           birthday: Optional[Dict[str, int]] = None, org: Optional[str] = None, title: Optional[str] = None,
           nick: Optional[str] = None) -> Dict[str, Any]:
    """A People API person. ``phones`` / ``emails``: values or ``(value, type)`` pairs."""
    p: Dict[str, Any] = {"resourceName": f"people/{rid}"}
    if name:
        given, _, family = name.partition(" ")
        p["names"] = [{"displayName": name, "givenName": given, "familyName": family}]
    if nick:
        p["nicknames"] = [{"value": nick}]
    pairs = [(x, "") if isinstance(x, str) else x for x in phones]
    if pairs:
        p["phoneNumbers"] = [{"value": v, **({"type": t} if t else {}),
                              **({"canonicalForm": v} if v.startswith("+") else {})} for v, t in pairs]
    pairs = [(x, "") if isinstance(x, str) else x for x in emails]
    if pairs:
        p["emailAddresses"] = [{"value": v, **({"type": t} if t else {})} for v, t in pairs]
    if birthday:
        p["birthdays"] = [{"date": birthday}]
    if org or title:
        p["organizations"] = [{k: v for k, v in (("name", org), ("title", title)) if v}]
    return p


class FakeAccount:
    def __init__(self, name: str, people: List[Dict[str, Any]]) -> None:
        self.name = name
        self.seq = 0
        self.people: Dict[str, Dict[str, Any]] = {}
        self.log: List[Tuple[int, str]] = []  # every change: (seq, resource name)
        self.expired: set = set()  # sync tokens Google no longer accepts
        self.fail = False
        self.created = 0
        for p in people:
            self.put(p)

    def put(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Add or change a contact as Google would: a new etag, a line in the change log."""
        self.seq += 1
        p = {**p, "etag": f"{self.name}-etag-{self.seq}"}
        self.people[p["resourceName"]] = p
        self.log.append((self.seq, p["resourceName"]))
        return p

    def delete(self, resource: str) -> None:
        self.seq += 1
        del self.people[resource]
        self.log.append((self.seq, resource))

    def token(self) -> str:
        return f"{self.name}-sync-{self.seq}"


ROBIN_PEOPLE = [
    person("r1", "Gal Cohen", phones=(("+972501110001", "mobile"), ("03-555-0001", "work")),
           emails=(("gal@example.com", "home"),), birthday={"year": 1990, "month": 3, "day": 14}, org="Wix",
           title="Engineer", nick="Gali"),
    person("r2", "Rachel Levi", phones=("+972501110002",), emails=("rachel@example.com", "r.levi@work.example"),
           birthday={"year": 1961, "month": 2, "day": 2}),
    person("r3", "נועה כהן", phones=("050-1110003",), birthday={"month": 7, "day": 9}),
    person("r4", org="Pizza Roma", phones=("03-555-0004",)),
    person("r5", "Dan Ambiguous", phones=("+972501110005",)),
    person("r6", "Dana Ambiguous", phones=("+972501110006",)),
    person("r7", "Shared Home A", phones=("+97235550007",)),
    person("r8", "Shared Home B", phones=("+97235550007",)),
    person("r9", "Omer Typed", phones=("+972501110009", "+972501110019"), emails=("omer@google.example",),
           birthday={"year": 1985, "month": 5, "day": 5}),
    person("r10", "Yael New-Caller", phones=("+972501110010",)),
    person("r11", "Sam Example", phones=("+972501110011",)),
    person("r12", "Short Number", phones=("0501110012",)),
]
BOB_PEOPLE = [
    person("b1", "Gali Bob", phones=("+972501110001",), emails=("gali@bob.example",)),
    person("b2", "Bob Only", phones=("+972502220002",), emails=("only@bob.example",)),
]
ACCOUNTS = {
    "robin-refresh": FakeAccount("robin", ROBIN_PEOPLE),
    "bob-refresh": FakeAccount("bob", BOB_PEOPLE),
    "bob2-refresh": FakeAccount("bob2", [person("x1", "Second Account", phones=("+972503330001",))]),
    "eve-refresh": FakeAccount("eve", [person("e1", "Eve Friend", phones=("+972504440001",))]),
}
REQUESTS: List[Dict[str, Any]] = []


class _Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeConnections:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def list(self, **kw: Any) -> _Call:
        def run():
            a = self.a
            REQUESTS.append({"account": a.name, "method": "connections.list", "user": users.current_id(), **kw})
            if a.fail:
                raise HttpError(401, "Request had invalid authentication credentials.")
            token = kw.get("syncToken")
            if token:
                if token in a.expired or not token.startswith(f"{a.name}-sync-"):
                    raise HttpError(400, EXPIRED)
                since = int(token.rsplit("-", 1)[1])
                changed: List[str] = []
                for seq, resource in a.log:
                    if seq > since and resource not in changed:
                        changed.append(resource)
                items = [dict(a.people[r]) if r in a.people else {"resourceName": r, "metadata": {"deleted": True}}
                         for r in changed]
            else:
                items = [dict(p) for p in a.people.values()]
            size, start = int(kw.get("pageSize") or 100), int(kw.get("pageToken") or 0)
            out: Dict[str, Any] = {"connections": items[start:start + size]}
            if start + size < len(items):
                out["nextPageToken"] = str(start + size)
            elif kw.get("requestSyncToken"):
                out["nextSyncToken"] = a.token()
            return out
        return _Call(run)


class FakePeople:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def connections(self) -> FakeConnections:
        return FakeConnections(self.a)

    def _log(self, method: str, **kw: Any) -> None:
        REQUESTS.append({"account": self.a.name, "method": method, "user": users.current_id(), **kw})
        if self.a.fail:
            raise HttpError(401, "Request had invalid authentication credentials.")

    def get(self, resourceName: str, personFields: str) -> _Call:
        def run():
            self._log("get", resourceName=resourceName, personFields=personFields)
            if resourceName not in self.a.people:
                raise HttpError(404, "Requested entity was not found.")
            return dict(self.a.people[resourceName])
        return _Call(run)

    def createContact(self, body: Dict[str, Any], personFields: str) -> _Call:
        def run():
            self._log("createContact", body=body, personFields=personFields)
            self.a.created += 1
            return dict(self.a.put(_as_google({"resourceName": f"people/{self.a.name}-new{self.a.created}"}, body,
                                              list(body))))
        return _Call(run)

    def updateContact(self, resourceName: str, updatePersonFields: str, personFields: str,
                      body: Dict[str, Any]) -> _Call:
        def run():
            self._log("updateContact", resourceName=resourceName, updatePersonFields=updatePersonFields,
                      personFields=personFields, body=body)
            current = self.a.people.get(resourceName)
            if current is None:
                raise HttpError(404, "Requested entity was not found.")
            if body.get("etag") != current["etag"]:
                raise HttpError(400, ETAG_MISMATCH)
            return dict(self.a.put(_as_google(current, body, updatePersonFields.split(","))))
        return _Call(run)


def _as_google(current: Dict[str, Any], body: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """``current`` with ``fields`` replaced from ``body``, as Google stores them (each named field in full)."""
    out = {k: v for k, v in current.items() if k not in fields}
    for field in fields:
        value = body.get(field) or []
        if field == "names":
            value = [{"displayName": n.get("unstructuredName") or n.get("displayName")} for n in value]
        if value:
            out[field] = value
    return out


class FakeService:
    def __init__(self, acct: FakeAccount) -> None:
        self.a = acct

    def people(self) -> FakePeople:
        return FakePeople(self.a)


def fake_build(name: str, version: str, credentials: Any = None, **kw: Any) -> FakeService:
    assert (name, version) == ("people", "v1")
    if credentials.client_id != "server-client-id" or credentials.client_secret != "server-client-secret":
        raise AssertionError("not the server's OAuth client")
    return FakeService(ACCOUNTS[credentials.refresh_token])


contacts_client.build = fake_build
contacts_client.PAGE_SIZE = 5  # so a full read of Robin's account takes several pages


def requests() -> List[Dict[str, Any]]:
    out = list(REQUESTS)
    REQUESTS.clear()
    return out


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Levi", "member")
CARL = users.create("Carl Cohen", "member")
EVE = users.create("Eve Mizrahi", "member")
store.connect(BOB, "people")
store.connect(CARL, "people")
ROBIN, BOBS = "people/r", "people/b"
CONTACT_TOOLS = {"search_contacts", "get_contact", "update_contact", "add_contact"}


def copy_ids(user) -> List[str]:
    return sorted(c["id"] for c in contacts_store.all_contacts(user))


def rows(sql: str, *args: Any) -> List[Dict[str, Any]]:
    with get_db_connection() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def person_row(person_id: str) -> Dict[str, Any]:
    return rows("SELECT * FROM people WHERE id = ?", person_id)[0]


def phones_of(person_id: str) -> List[str]:
    return sorted(r["phone"] for r in rows("SELECT phone FROM person_phones WHERE person_id = ?", person_id))


async def settle() -> None:
    """Wait for the syncs started in the background (connect, another token)."""
    while sync._tasks:
        await asyncio.gather(*list(sync._tasks), return_exceptions=True)


async def tool(user, tool_name: str, /, **args: Any) -> str:
    with users.acting_as(user):
        return str(await registry.execute(user, tool_name, args))


# --- 1. The kind and the migration ------------------------------------------------------------------

def test_kind() -> None:
    kind = registry.get_kind("contacts")
    check("kind: Google Contacts, one credential, the Google sign-in, exactly the contacts scope",
          kind.description == "Google Contacts" and [f.name for f in kind.credentials] == ["refresh_token"]
          and kind.connect_url == oauth.CONNECT_PATH and not kind.settings
          and oauth.scopes_of("contacts") == ("https://www.googleapis.com/auth/contacts",)
          and oauth.google_kinds() == ["calendar", "contacts", "gmail"], (oauth.scopes_of("contacts"), oauth.google_kinds()))
    check("kind: its tools, add / update ask first and nothing deletes",
          {t.name for t in kind.tools} == CONTACT_TOOLS
          and registry.needs_confirmation("update_contact", {"contact_id": "people/r1", "name": "X"})
          and registry.needs_confirmation("add_contact", {"name": "X"})
          and registry.is_write_tool("update_contact") and registry.is_write_tool("add_contact")
          and not registry.needs_confirmation("search_contacts", {"query": "x"})
          and not registry.is_write_tool("search_contacts") and not registry.is_write_tool("get_contact"))
    check("config: synced every 60 minutes unless CONTACTS_SYNC_MINUTES says otherwise, the decided field mask",
          contacts_config.SYNC_MINUTES == 60
          and contacts_config.PERSON_FIELDS == "names,nicknames,phoneNumbers,emailAddresses,birthdays,organizations")
    old = person_row("pe1")
    check("migration: the 8.1.0 person has no emails and no contact; nobody has a copy",
          old["emails"] == "[]" and old["contact_resource"] == "" and old["name"] == "Sam Example"
          and not rows("SELECT 1 FROM google_contacts") and not rows("SELECT 1 FROM google_contacts_sync"))
    check("the env import never connects contacts", not store.is_connected(ADMIN, "contacts"))


# --- 2. Sync ----------------------------------------------------------------------------------------

async def test_sync() -> None:
    # Outside a request's event loop nothing is scheduled; here it is: connect returns at once.
    store.connect(ADMIN, "contacts", credentials={"refresh_token": "robin-refresh"})
    before = (copy_ids(ADMIN), contacts_store.synced_at(ADMIN))
    out = await tool(ADMIN, "search_contacts", query="gal")
    check("connect: returns before anything is read (the sync runs in the background)",
          before == ([], None) and "not been read from Google yet" in out, (before, out))
    await settle()
    reqs = requests()
    check("connect: the first sync reads everything, page by page, as the user, asking for a sync token",
          [r.get("pageToken") for r in reqs] == [None, "5", "10"] and all(r["account"] == "robin" for r in reqs)
          and all(r["user"] == ADMIN.id and r["requestSyncToken"] is True and "syncToken" not in r
                  and r["resourceName"] == "people/me" and r["personFields"] == contacts_config.PERSON_FIELDS
                  for r in reqs), reqs)
    check("full sync: every contact of the account is in the user's copy, the sync token and time stored",
          copy_ids(ADMIN) == sorted(f"people/r{i}" for i in range(1, 13))
          and contacts_store.sync_token(ADMIN) == "robin-sync-12" and contacts_store.synced_at(ADMIN))
    gal = contacts_store.get(ADMIN, "people/r1")
    check("copy: name, nickname, numbers normalised like people's (with the type), emails, birthday, organisation",
          gal and gal["name"] == "Gal Cohen" and gal["other_names"] == ["Gali"] and gal["etag"] == "robin-etag-1"
          and gal["phones"] == [{"number": "972501110001", "value": "+972501110001", "type": "mobile"},
                                {"number": "97235550001", "value": "03-555-0001", "type": "work"}]
          and gal["emails"] == [{"value": "gal@example.com", "type": "home"}] and gal["birthday"] == "1990-03-14"
          and gal["organisation"] == "Wix" and gal["job_title"] == "Engineer", gal)
    noa, pizza = contacts_store.get(ADMIN, "r3"), contacts_store.get(ADMIN, "people/r4")
    check("copy: a birthday without a year is MM-DD; a contact without a name is called by its organisation",
          noa and noa["birthday"] == "07-09" and noa["name"] == "נועה כהן" and pizza and pizza["name"] == "Pizza Roma"
          and pizza["organisation"] == "Pizza Roma", (noa, pizza))

    # Bob: his own account; Eve: contacts without people.
    store.connect(BOB, "contacts", credentials={"refresh_token": "bob-refresh"})
    store.connect(EVE, "contacts", credentials={"refresh_token": "eve-refresh"})
    await settle()
    reqs = requests()
    check("isolation: each user's sync asked only their own account, as themselves",
          {(r["account"], r["user"]) for r in reqs} == {("bob", BOB.id), ("eve", EVE.id)}, reqs)
    check("isolation: each copy holds only its user's contacts (the same number on both sides included)",
          copy_ids(BOB) == ["people/b1", "people/b2"] and copy_ids(EVE) == ["people/e1"]
          and len(copy_ids(ADMIN)) == 12 and copy_ids(CARL) == []
          and contacts_store.get(BOB, "people/r1") is None and contacts_store.get(ADMIN, "people/b1") is None)

    # Incremental: one changed, one added, one deleted in Google.
    robin = ACCOUNTS["robin-refresh"]
    robin.put(person("r2", "Rachel Levi-Cohen", phones=("+972501110002",), emails=("rachel@example.com",),
                   birthday={"year": 1961, "month": 2, "day": 2}))
    robin.put(person("r13", "Tal Added", phones=("+972501110013",)))
    robin.delete("people/r12")
    untouched = contacts_store.get(ADMIN, "people/r1")
    with users.acting_as(ADMIN):
        result = await sync.sync_user(ADMIN)
    reqs = requests()
    rachel = contacts_store.get(ADMIN, "people/r2")
    check("incremental: one request with the stored sync token",
          len(reqs) == 1 and reqs[0]["syncToken"] == "robin-sync-12" and reqs[0]["account"] == "robin", reqs)
    check("incremental: the changed contact is updated, the new one added, the deleted one removed, the rest left",
          result == {"changed": 2, "removed": 1, "contacts": 12, "full": 0, "people": result["people"]}
          and rachel["name"] == "Rachel Levi-Cohen" and rachel["etag"] == "robin-etag-13"
          and [e["value"] for e in rachel["emails"]] == ["rachel@example.com"]
          and contacts_store.get(ADMIN, "people/r13")["name"] == "Tal Added"
          and contacts_store.get(ADMIN, "people/r12") is None
          and contacts_store.get(ADMIN, "people/r1") == untouched
          and not rows("SELECT 1 FROM google_contact_phones WHERE user_id = ? AND resource_name = 'people/r12'",
                       ADMIN.id)
          and contacts_store.sync_token(ADMIN) == "robin-sync-15", (result, rachel))
    check("incremental: Bob's copy did not move", copy_ids(BOB) == ["people/b1", "people/b2"]
          and contacts_store.sync_token(BOB) == "bob-sync-2")

    # The sync token expires: one full read, which also drops what Google no longer has.
    robin.expired.add("robin-sync-15")
    robin.delete("people/r13")
    with users.acting_as(ADMIN):
        result = await sync.sync_user(ADMIN)
    reqs = requests()
    check("expired sync token: refused once, then everything is read again without a token",
          reqs[0].get("syncToken") == "robin-sync-15" and all("syncToken" not in r for r in reqs[1:])
          and len(reqs) == 4 and result["full"] == 1, reqs)
    check("expired sync token: the copy is what Google has now, with a fresh token",
          copy_ids(ADMIN) == sorted(f"people/r{i}" for i in range(1, 12)) and result["removed"] == 1
          and result["contacts"] == 11 and contacts_store.sync_token(ADMIN) == "robin-sync-16", (copy_ids(ADMIN), result))

    # The loop's run: every user with the kind, each as themselves; one failing does not stop the next.
    ACCOUNTS["bob-refresh"].fail = True
    ACCOUNTS["bob-refresh"].people.pop("people/b2")  # must not reach the copy while the account fails
    out = await sync.sync_all()
    reqs = requests()
    ACCOUNTS["bob-refresh"].fail = False
    order = [u for u in store.users_with("contacts")]
    check("loop: every user with contacts is synced as themselves; Carl (no contacts) is not",
          sorted({r["user"] for r in reqs}) == sorted([ADMIN.id, BOB.id, EVE.id]) == sorted(order)
          and all((r["account"], r["user"]) in {("robin", ADMIN.id), ("bob", BOB.id), ("eve", EVE.id)} for r in reqs)
          and CARL.id not in out, reqs)
    check("loop: Bob's failure is his alone: the others are synced, his copy is left as it was",
          BOB.id not in out and out[ADMIN.id]["full"] == 0 and out[EVE.id]["contacts"] == 1
          and copy_ids(BOB) == ["people/b1", "people/b2"] and contacts_store.sync_token(BOB) == "bob-sync-2", out)
    ACCOUNTS["bob-refresh"].put(person("b2", "Bob Only", phones=("+972502220002",), emails=("only@bob.example",)))
    with users.acting_as(BOB):
        await sync.sync_user(BOB)
    requests()
    with users.acting_as(CARL):
        check("sync: nothing for a user without the kind (no Google call)",
              await sync.sync_user(CARL) is None and not requests())


# --- 3. Tools ---------------------------------------------------------------------------------------

def found(out: str) -> List[str]:
    """The contact ids of a search result, in order."""
    return [line.split("]")[0][3:] for line in out.splitlines() if line.startswith("- [")]


async def test_tools() -> None:
    requests()
    out = await tool(ADMIN, "search_contacts", query="GAL  cohen!")
    check("search: a name whatever the case and punctuation, with numbers, emails and the organisation",
          out == "- [people/r1] Gal Cohen · +972501110001 (mobile), +97235550001 (work) · gal@example.com · Wix", out)
    by_word = found(await tool(ADMIN, "search_contacts", query="cohen"))
    by_start = found(await tool(ADMIN, "search_contacts", query="rach"))
    by_nick = found(await tool(ADMIN, "search_contacts", query="gali"))
    check("search: a word of the name, the start of a name, a nickname",
          by_word[0] == "people/r1" and by_start == ["people/r2"] and by_nick == ["people/r1"], (by_word, by_start, by_nick))
    hebrew = found(await tool(ADMIN, "search_contacts", query="נועה"))
    cross = found(await tool(ADMIN, "search_contacts", query="כהן"))
    latin_for_hebrew = found(await tool(ADMIN, "search_contacts", query="cohen", limit=10))
    check("search: Hebrew names in Hebrew; a name written in the other script matches by its consonants",
          hebrew == ["people/r3"] and cross[0] == "people/r3" and "people/r1" in cross
          and latin_for_hebrew[0] == "people/r1" and "people/r3" in latin_for_hebrew, (hebrew, cross, latin_for_hebrew))
    by_local = found(await tool(ADMIN, "search_contacts", query="050-111-0002"))
    by_intl = found(await tool(ADMIN, "search_contacts", query="+972 50 111 0003"))
    by_tail = found(await tool(ADMIN, "search_contacts", query="5550004"))
    by_email = found(await tool(ADMIN, "search_contacts", query="RACHEL@example.com"))
    by_domain = found(await tool(ADMIN, "search_contacts", query="google.example"))
    check("search: a phone number in any format, the end of a number, an email address, part of one",
          by_local == ["people/r2"] and by_intl == ["people/r3"] and by_tail == ["people/r4"]
          and by_email == ["people/r2"] and by_domain == ["people/r9"], (by_local, by_intl, by_tail, by_email, by_domain))
    limited = found(await tool(ADMIN, "search_contacts", query="ambiguous", limit=1))
    typo = found(await tool(ADMIN, "search_contacts", query="Rachl Levi-Cohn"))
    nothing = await tool(ADMIN, "search_contacts", query="zzzz")
    check("search: the limit, a near-miss spelling when nothing else matches, nothing found",
          limited == ["people/r5"] and typo == ["people/r2"] and nothing == "No contacts matching 'zzzz'.",
          (limited, typo, nothing))
    bob_gal = await tool(BOB, "search_contacts", query="gal")
    bob_number = await tool(BOB, "search_contacts", query="0501110002")
    robin_bob = await tool(ADMIN, "search_contacts", query="Bob Only")
    check("search: each user searches only their own copy",
          found(bob_gal) == ["people/b1"] and "Gal Cohen" not in bob_gal and bob_number.startswith("No contacts")
          and robin_bob.startswith("No contacts"), (bob_gal, bob_number, robin_bob))

    full = await tool(ADMIN, "get_contact", contact_id="people/r1")
    check("get_contact: one contact in full",
          full == ("Gal Cohen [people/r1]\nAlso known as: Gali\nPhones: +972501110001 (mobile), +97235550001 (work)\n"
                   "Emails: gal@example.com (home)\nBirthday: 1990-03-14\nOrganisation: Wix — Engineer"), full)
    check("get_contact: another user's contact id answers like an unknown one",
          (await tool(BOB, "get_contact", contact_id="people/r1")).startswith("No contact with id")
          and (await tool(ADMIN, "get_contact", contact_id="people/b1")).startswith("No contact with id")
          and (await tool(ADMIN, "get_contact", contact_id="people/nope")).startswith("No contact with id"))
    check("reads: no Google call (the local copy)", not requests())

    # update_contact: the stored etag goes to Google, the copy is refreshed from the answer.
    out = await tool(ADMIN, "update_contact", contact_id="people/r1", add_phone="052-999-0001", birthday="1990-03-15",
                     add_email="gal@wix.example")
    reqs = requests()
    gal = contacts_store.get(ADMIN, "people/r1")
    google = ACCOUNTS["robin-refresh"].people["people/r1"]
    check("update: one request for the user's own account with the stored etag and only the changed fields",
          len(reqs) == 1 and reqs[0]["method"] == "updateContact" and reqs[0]["account"] == "robin"
          and reqs[0]["user"] == ADMIN.id and reqs[0]["resourceName"] == "people/r1"
          and reqs[0]["body"]["etag"] == "robin-etag-1"
          and reqs[0]["updatePersonFields"] == "birthdays,emailAddresses,phoneNumbers"
          and set(reqs[0]["body"]) == {"resourceName", "etag", "birthdays", "emailAddresses", "phoneNumbers"}, reqs)
    check("update: the numbers and emails already there go back as they were (value and type), the new ones added",
          reqs[0]["body"]["phoneNumbers"] == [{"value": "+972501110001", "type": "mobile"},
                                             {"value": "03-555-0001", "type": "work"}, {"value": "052-999-0001"}]
          and reqs[0]["body"]["emailAddresses"] == [{"value": "gal@example.com", "type": "home"},
                                                    {"value": "gal@wix.example"}]
          and reqs[0]["body"]["birthdays"] == [{"date": {"year": 1990, "month": 3, "day": 15}}]
          and google["names"][0]["displayName"] == "Gal Cohen" and google["nicknames"] == [{"value": "Gali"}],
          reqs[0]["body"])
    check("update: the copy is refreshed at once (no sync): the new etag, number, email and birthday",
          gal["etag"] == google["etag"] != "robin-etag-1" and "972529990001" in contacts_store.numbers_of(gal)
          and [e["value"] for e in gal["emails"]] == ["gal@example.com", "gal@wix.example"]
          and gal["birthday"] == "1990-03-15" and gal["name"] == "Gal Cohen" and gal["other_names"] == ["Gali"]
          and out.startswith("Updated in Google Contacts:\nGal Cohen [people/r1]")
          and found(await tool(ADMIN, "search_contacts", query="0529990001")) == ["people/r1"], (gal, out))
    out = await tool(ADMIN, "update_contact", contact_id="people/r1", name="Gal Cohen-Levi", remove_phone="03-5550001",
                     remove_email="GAL@example.com", organisation="", birthday="")
    reqs = requests()
    gal = contacts_store.get(ADMIN, "people/r1")
    check("update: a new name, a number and an email removed, the birthday and the organisation cleared",
          reqs[0]["body"]["etag"] == google["etag"] and reqs[0]["body"]["names"] == [{"unstructuredName": "Gal Cohen-Levi"}]
          and reqs[0]["body"]["organizations"] == [{"title": "Engineer"}] and reqs[0]["body"]["birthdays"] == []
          and gal["name"] == "Gal Cohen-Levi" and contacts_store.numbers_of(gal) == ["972501110001", "972529990001"]
          and [e["value"] for e in gal["emails"]] == ["gal@wix.example"] and gal["birthday"] == ""
          and gal["organisation"] == "" and gal["job_title"] == "Engineer", (reqs[0]["body"], gal))
    same = await tool(ADMIN, "update_contact", contact_id="people/r1", name="Gal Cohen-Levi", add_phone="+972501110001")
    bad = [await tool(ADMIN, "update_contact", contact_id="people/r1", remove_phone="0500000000"),
           await tool(ADMIN, "update_contact", contact_id="people/r1", add_email="not-an-email"),
           await tool(ADMIN, "update_contact", contact_id="people/r1", birthday="14/03")]
    check("update: nothing to change and unusable values never reach Google",
          same.startswith("Nothing to change") and all(b.startswith("Error:") for b in bad) and not requests(),
          (same, bad))
    theirs = await tool(BOB, "update_contact", contact_id="people/r2", name="Hacked")
    check("update: another user's contact is unknown to the caller (no Google call, nothing changed)",
          theirs.startswith("No contact with id") and not requests()
          and ACCOUNTS["robin-refresh"].people["people/r2"]["names"][0]["displayName"] == "Rachel Levi-Cohen", theirs)

    # Changed in Google since the last sync: refused, read again, nothing overwritten.
    robin = ACCOUNTS["robin-refresh"]
    robin.put(person("r5", "Dan Renamed-In-Google", phones=("+972501110005", "+972501110055")))
    out = await tool(ADMIN, "update_contact", contact_id="people/r5", add_email="dan@example.com")
    reqs = requests()
    dan = contacts_store.get(ADMIN, "people/r5")
    check("update: a contact changed in Google meanwhile is not overwritten; the copy is read again",
          out.startswith("Error: this contact was changed in Google Contacts")
          and [r["method"] for r in reqs] == ["updateContact", "get"] and "emailAddresses" not in robin.people["people/r5"]
          and dan["name"] == "Dan Renamed-In-Google" and dan["etag"] == robin.people["people/r5"]["etag"]
          and "972501110055" in contacts_store.numbers_of(dan), (out, reqs, dan))
    out = await tool(ADMIN, "update_contact", contact_id="people/r5", add_email="dan@example.com")
    check("update: repeated with the fresh etag it goes through",
          out.startswith("Updated") and robin.people["people/r5"]["emailAddresses"] == [{"value": "dan@example.com"}]
          and requests()[0]["body"]["etag"] == dan["etag"], out)
    robin.put(person("r14", "Soon Gone", phones=("+972501110014",)))
    with users.acting_as(ADMIN):
        await sync.sync_user(ADMIN)
    robin.delete("people/r14")
    out = await tool(ADMIN, "update_contact", contact_id="people/r14", name="Still Here")
    check("update: a contact deleted in Google is dropped from the copy",
          out == "Error: this contact no longer exists in Google Contacts."
          and contacts_store.get(ADMIN, "people/r14") is None, out)
    robin.fail = True
    out = await tool(ADMIN, "update_contact", contact_id="people/r2", name="Unreachable")
    robin.fail = False
    check("update: a failure is a short reason and leaves the copy as it was",
          out == "Error: Google Contacts answered HTTP 401."
          and contacts_store.get(ADMIN, "people/r2")["name"] == "Rachel Levi-Cohen", out)
    requests()

    # add_contact.
    out = await tool(BOB, "add_contact", name="New Friend", phone="054-777-0001", email="friend@example.com",
                     birthday="03-21", organisation="Acme")
    reqs = requests()
    new = contacts_store.get(BOB, "people/bob-new1")
    check("add: created in the user's own account with what was given",
          len(reqs) == 1 and reqs[0]["method"] == "createContact" and reqs[0]["account"] == "bob"
          and reqs[0]["user"] == BOB.id
          and reqs[0]["body"] == {"names": [{"unstructuredName": "New Friend"}], "phoneNumbers": [{"value": "054-777-0001"}],
                                  "emailAddresses": [{"value": "friend@example.com"}],
                                  "birthdays": [{"date": {"month": 3, "day": 21}}], "organizations": [{"name": "Acme"}]}
          and "people/bob-new1" in ACCOUNTS["bob-refresh"].people and "people/bob-new1" not in robin.people, reqs)
    check("add: the new contact is in the copy at once and can be searched; not in anyone else's",
          new and new["name"] == "New Friend" and contacts_store.numbers_of(new) == ["972547770001"]
          and new["birthday"] == "03-21" and new["organisation"] == "Acme" and new["etag"]
          and out.startswith("Added to Google Contacts:\nNew Friend [people/bob-new1]")
          and found(await tool(BOB, "search_contacts", query="0547770001")) == ["people/bob-new1"]
          and (await tool(ADMIN, "search_contacts", query="New Friend")).startswith("No contacts"), (new, out))
    dup = await tool(BOB, "add_contact", name="Friend Again", phone="+972547770001")
    bad = [await tool(BOB, "add_contact", name="  "), await tool(BOB, "add_contact", name="X", email="nope"),
           await tool(BOB, "add_contact", name="X", birthday="soon")]
    check("add: a number a contact already has, a missing name and unusable values never reach Google",
          dup.startswith("Error: New Friend [people/bob-new1] already has that number")
          and all(b.startswith("Error:") for b in bad) and not requests(), (dup, bad))

    # The approval cards.
    with users.acting_as(ADMIN):
        add = approval_text.describe("add_contact", {"name": "Dr. Yael", "phone": "050-1", "email": "y@x.example"})
        mine = approval_text.describe("update_contact", {"contact_id": "people/r2", "add_phone": "050-2",
                                                         "birthday": ""})
    with users.acting_as(BOB):
        not_mine = approval_text.describe("update_contact", {"contact_id": "people/r2", "name": "X"})
    check("approval card: adding a contact in words",
          add["title"] == "Add Dr. Yael to Google Contacts" and add["question"] == "Add this contact?"
          and add["facts"] == [("Phone", "050-1"), ("Email", "y@x.example")], add)
    check("approval card: a change names the contact only from the user's own copy",
          mine["title"] == "Change Rachel Levi-Cohen in Google Contacts"
          and mine["facts"] == [("Add number", "050-2"), ("Birthday", "remove")]
          and not_mine["title"] == "Change a Google contact" and "Rachel" not in json.dumps(not_mine)
          and ("Contact", "people/r2") in not_mine["facts"], (mine, not_mine))

    # The connection check.
    requests()
    ok = await contacts_client.check(store.require(BOB, "contacts"))
    reqs = requests()
    robin.fail = True
    failing = await contacts_client.check(store.require(ADMIN, "contacts"))
    robin.fail = False
    check("check: one cheap request to the user's own account; a failure is a short reason",
          ok is None and len(reqs) == 1 and reqs[0]["account"] == "bob" and reqs[0]["pageSize"] == 1
          and "requestSyncToken" not in reqs[0] and failing == "Google Contacts answered HTTP 401.", (ok, reqs, failing))
    requests()

    # Without the kind.
    names_carl = {s["function"]["name"] for s in registry.tool_schemas(CARL)}
    names_robin = {s["function"]["name"] for s in registry.tool_schemas(ADMIN)}
    refused = []
    for name in sorted(CONTACT_TOOLS) + ["no_such_tool"]:
        try:
            await tool(CARL, name, query="gal", contact_id="people/r1", name="x")
            refused.append("ran")
        except registry.ToolNotAvailable as e:
            refused.append(str(e).replace(name, "X"))
    check("without contacts: the tools are not offered, and refused exactly like a made-up tool",
          not (CONTACT_TOOLS & names_carl) and CONTACT_TOOLS <= names_robin
          and refused == ["Tool 'X' is not registered."] * 5 and not requests()
          and not any(registry.has_tool(CARL, t) for t in CONTACT_TOOLS), (refused, names_carl & CONTACT_TOOLS))
    prompt_robin, prompt_carl = agent.instructions(ADMIN), agent.instructions(CARL)
    check("prompt: look a number or email up with search_contacts — only for a user with the kind",
          "look them up with search_contacts instead of asking" in prompt_robin and "contact lookup (Google Contacts)"
          in prompt_robin and "search_contacts" not in prompt_carl and "Google Contacts" not in prompt_carl)


# --- 4. People filled in from contacts ---------------------------------------------------------------

def people_count(user) -> int:
    return rows("SELECT COUNT(*) AS n FROM people WHERE user_id = ?", user.id)[0]["n"]


def add_call(cid: str, user, caller: str, number: str, person_id: Optional[str] = None) -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, transcript, summary, "
            "title, status, phone_number, direction, person_id) VALUES (?, ?, ?, ?, 'Me', '2026-11-10T10:00:00', "
            "'hello', 'We talked.', ?, 'ready', ?, 'incoming', ?)",
            (cid, user.id, f"{cid}.m4a", caller, f"Call {cid}", number, person_id))
        conn.commit()


def call_person(cid: str) -> Optional[str]:
    return rows("SELECT person_id FROM phone_calls WHERE id = ?", cid)[0]["person_id"]


async def test_people() -> None:
    def make(user, name: str, phones: List[str], **kw: Any) -> str:
        return people_store.create_person(user, name, phones=phones, **kw)["id"]

    # Robin's people before the fill.
    number_only = make(ADMIN, "+972501110002", ["+972501110002"], source="auto", hidden=True)  # Rachel (r2)
    typed = make(ADMIN, "Omer My Brother", ["0501110009"], birthday="1986-06-06", notes="typed note")  # Omer (r9)
    with get_db_connection() as conn:
        conn.execute("UPDATE people SET emails = ? WHERE id = ?", (json.dumps(["omer@typed.example"]), typed))
        conn.commit()
    other_holder = make(ADMIN, "Omer Work Phone", ["+972501110019"])  # holds Omer's second number
    auto_named = make(ADMIN, "Gal From Call", ["+972501110001"], source="auto")  # Gal (r1), named by the call
    ambiguous = make(ADMIN, "Two Contacts", ["+972501110005", "+972501110006"])  # r5 and r6
    shared = make(ADMIN, "+97235550007", ["+97235550007"], source="auto", hidden=True)  # r7 and r8 share it
    no_contact = make(ADMIN, "+972509999999", ["+972509999999"], source="auto", hidden=True)
    hidden_named = make(ADMIN, "Noa Hidden By Robin", ["0501110003"], hidden=True)  # r3
    add_call("k1", ADMIN, "+972529990001", "+972529990001", person_id="")  # Gal's other number, checked: no match
    # Bob: the number Robin's contacts call Gal is Gali Bob in his; one number only Robin's contacts have.
    bob_same = make(BOB, "+972501110001", ["+972501110001"], source="auto", hidden=True)
    bob_foreign = make(BOB, "+972501110002", ["+972501110002"], source="auto", hidden=True)
    carl_person = make(CARL, "+972501110001", ["+972501110001"], source="auto", hidden=True)
    counts = {u.id: people_count(u) for u in (ADMIN, BOB, CARL, EVE)}

    with users.acting_as(ADMIN):
        result = await sync.sync_user(ADMIN)
    with users.acting_as(BOB):
        await sync.sync_user(BOB)
    with users.acting_as(EVE):
        eve_result = await sync.sync_user(EVE)

    p = person_row(number_only)
    check("fill: an auto-created number-only person gets the contact's name and is shown",
          p["name"] == "Rachel Levi-Cohen" and p["hidden"] == 0 and p["source"] == "auto" and p["aliases"] == "[]", p)
    check("fill: the birthday and the emails are filled when empty, the contact remembered",
          p["birthday"] == "1961-02-02" and json.loads(p["emails"]) == ["rachel@example.com"]
          and p["contact_resource"] == "people/r2", p)
    p = person_row(typed)
    check("fill: a name, birthday, email or note the user typed is never overwritten",
          p["name"] == "Omer My Brother" and p["birthday"] == "1986-06-06" and p["notes"] == "typed note"
          and json.loads(p["emails"]) == ["omer@typed.example"] and p["contact_resource"] == "people/r9", p)
    check("fill: a contact's other number is not taken from another person who has it",
          phones_of(typed) == ["972501110009"] and phones_of(other_holder) == ["972501110019"]
          and person_row(other_holder)["contact_resource"] == "people/r9", (phones_of(typed), phones_of(other_holder)))
    p = person_row(auto_named)
    check("fill: a person the call already named keeps the name; the contact's other number is added",
          p["name"] == "Gal From Call" and p["contact_resource"] == "people/r1"
          and phones_of(auto_named) == ["972501110001", "972529990001"]
          and json.loads(p["emails"]) == ["gal@wix.example"] and p["birthday"] == "", (p, phones_of(auto_named)))
    linking.link_pending(ADMIN)
    check("fill: a call from the added number, unmatched before, now links to that person",
          call_person("k1") == auto_named, call_person("k1"))
    check("fill: numbers on two different contacts, or one number on two contacts: nothing is guessed",
          person_row(ambiguous)["contact_resource"] == "" and person_row(ambiguous)["name"] == "Two Contacts"
          and person_row(shared)["name"] == "+97235550007" and person_row(shared)["hidden"] == 1
          and person_row(shared)["contact_resource"] == "")
    check("fill: a number no contact has leaves the person alone; a person the user hid stays hidden",
          person_row(no_contact)["name"] == "+972509999999" and person_row(no_contact)["hidden"] == 1
          and person_row(hidden_named)["hidden"] == 1 and person_row(hidden_named)["name"] == "Noa Hidden By Robin"
          and person_row(hidden_named)["birthday"] == "07-09" and person_row(hidden_named)["contact_resource"] == "people/r3")
    check("fill: the 8.1.0 person (no number in the contacts) is untouched",
          person_row("pe1")["contact_resource"] == "" and person_row("pe1")["emails"] == "[]")
    check("fill: no person is ever created from a contact",
          {u.id: people_count(u) for u in (ADMIN, BOB, CARL, EVE)} == counts and counts[EVE.id] == 0
          and eve_result["people"] == 0, counts)
    b = person_row(bob_same)
    check("isolation: Bob's person is filled from Bob's contact for that number, never from Robin's",
          b["name"] == "Gali Bob" and b["hidden"] == 0 and json.loads(b["emails"]) == ["gali@bob.example"]
          and b["contact_resource"] == "people/b1" and b["birthday"] == "", b)
    check("isolation: a number only Robin's contacts have does nothing for Bob's person; Carl (no contacts) gets nothing",
          person_row(bob_foreign)["name"] == "+972501110002" and person_row(bob_foreign)["hidden"] == 1
          and person_row(bob_foreign)["contact_resource"] == ""
          and person_row(carl_person)["name"] == "+972501110001" and person_row(carl_person)["hidden"] == 1
          and enrich.fill_people(CARL) == 0)
    with users.acting_as(ADMIN):
        payload = detail.get_person(ADMIN, number_only, schedule_brief=False)
    check("people payload: the emails are there", payload["emails"] == ["rachel@example.com"], payload)
    out = await tool(ADMIN, "get_person", person="Rachel Levi-Cohen")
    check("get_person: Dana sees the filled emails and birthday",
          "Emails: rachel@example.com" in out and "Birthday: 1961-02-02" in out, out)

    # A second fill changes nothing; the user's later edits win.
    people_store.update_person(ADMIN, number_only, {"name": "Rachel (aunt)", "birthday": "1961-02-03",
                                                    "emails": ["aunt@typed.example"]})
    ACCOUNTS["robin-refresh"].put(person("r2", "Rachel Renamed", phones=("+972501110002",),
                                       emails=("new@google.example",), birthday={"year": 1970, "month": 1, "day": 1}))
    with users.acting_as(ADMIN):
        await sync.sync_user(ADMIN)
    p = person_row(number_only)
    check("fill: once named, a later change of the contact (or of the person by the user) is not written over",
          p["name"] == "Rachel (aunt)" and p["birthday"] == "1961-02-03"
          and json.loads(p["emails"]) == ["aunt@typed.example"] and p["contact_resource"] == "people/r2"
          and contacts_store.get(ADMIN, "people/r2")["name"] == "Rachel Renamed", p)

    # The linked contact is deleted in Google.
    ACCOUNTS["robin-refresh"].delete("people/r9")
    with users.acting_as(ADMIN):
        await sync.sync_user(ADMIN)
    check("fill: a contact deleted in Google is forgotten, what the person has stays",
          person_row(typed)["contact_resource"] == "" and person_row(other_holder)["contact_resource"] == ""
          and person_row(typed)["name"] == "Omer My Brother" and phones_of(typed) == ["972501110009"])

    # A merge keeps the emails and the contact.
    merged = merge.merge_people(ADMIN, auto_named, number_only)
    check("merge: emails of both, the contact of the kept person",
          merged["emails"] == ["gal@wix.example", "aunt@typed.example"]
          and person_row(auto_named)["contact_resource"] == "people/r1", merged)


# --- 5. The caller's name -----------------------------------------------------------------------------

async def test_caller_name() -> None:
    check("caller name: from the user's own contacts, in any format of the number",
          contacts_store.caller_name(ADMIN, "050-111-0010") == "Yael New-Caller"
          and contacts_store.caller_name(ADMIN, "+972501110010") == "Yael New-Caller"
          and contacts_store.caller_name(ADMIN, "972501110010@c.us") == "Yael New-Caller")
    check("caller name: the last 9 digits when the prefix differs; nothing for an unknown number",
          contacts_store.caller_name(ADMIN, "+1501110010") == "Yael New-Caller"
          and contacts_store.caller_name(ADMIN, "+972508888888") is None
          and contacts_store.caller_name(ADMIN, "") is None)
    check("caller name: a number two contacts with different names share is nobody's",
          contacts_store.caller_name(ADMIN, "03-5550007") is None)
    check("caller name: per user — Bob's contacts for Bob, none of Robin's; nothing without the kind",
          contacts_store.caller_name(BOB, "0501110001") == "Gali Bob"
          and contacts_store.caller_name(ADMIN, "0501110001") == "Gal Cohen-Levi"
          and contacts_store.caller_name(BOB, "0501110010") is None
          and contacts_store.caller_name(CARL, "0501110010") is None
          and contacts_store.caller_name(None, "0501110010") is None)
    # The guard itself: rows left behind for a user without the kind are not read.
    with get_db_connection() as conn:
        conn.execute("INSERT INTO google_contacts (user_id, resource_name, name, updated_at) VALUES (?, 'people/z', "
                     "'Leftover', '2026-11-01')", (CARL.id,))
        conn.execute("INSERT INTO google_contact_phones (user_id, phone, resource_name) VALUES (?, '972501110010', "
                     "'people/z')", (CARL.id,))
        conn.commit()
    check("caller name: asked only for a user who has contacts connected",
          contacts_store.caller_name(CARL, "0501110010") is None)
    contacts_store.clear(CARL)

    # The call card for a number no person has.
    with users.acting_as(ADMIN):
        robin_card = detail.lookup(ADMIN, "050-1110010")
    with users.acting_as(BOB):
        bob_card = detail.lookup(BOB, "050-1110010")
    with users.acting_as(CARL):
        carl_card = detail.lookup(CARL, "050-1110010")
    check("call card: a number no person has gets the contact's name (no person is made)",
          robin_card == {"found": True, "card": {
              "person_id": "", "name": "Yael New-Caller", "initials": "YN", "relation": "", "pinned": False,
              "headline": "In your contacts", "points": [], "open": [], "last_call": None, "pending_suggestions": 0,
              "open_tasks": 0, "birthday": None, "drifting": False, "typical_gap_days": None}}
          and not rows("SELECT 1 FROM people WHERE name = 'Yael New-Caller'"), robin_card)
    check("call card: only from the caller's own contacts; unknown without the kind",
          bob_card == {"found": False} and carl_card == {"found": False}, (bob_card, carl_card))
    hidden = people_store.create_person(ADMIN, "+972501110011", phones=["+972501110011"], source="auto", hidden=True)
    with get_db_connection() as conn:  # as it is before the next sync fills it
        conn.execute("UPDATE people SET hidden = 1, name = '+972501110011' WHERE id = ?", (hidden["id"],))
        conn.commit()
    with users.acting_as(ADMIN):
        card = detail.lookup(ADMIN, "+972501110011")
        known = detail.lookup(ADMIN, "+972501110001")
    check("call card: a hidden number-only person without history is shown by the contact's name",
          card["found"] and card["card"]["name"] == "Sam Example" and card["card"]["person_id"] == "", card)
    check("call card: a person the user has keeps their own card",
          known["found"] and known["card"]["name"] == "Gal From Call" and known["card"]["person_id"], known)

    # A new caller the call does not name.
    before = {u.id: people_count(u) for u in (ADMIN, CARL)}
    add_call("k2", ADMIN, "+972501110010", "+972501110010")
    add_call("k3", ADMIN, "Unknown", "050-111-0006")  # r6, Dana Ambiguous
    add_call("k4", CARL, "+972501110010", "+972501110010")
    made_robin, made_carl = linking.link_pending(ADMIN), linking.link_pending(CARL)
    yael = rows("SELECT * FROM people WHERE user_id = ? AND name = 'Yael New-Caller'", ADMIN.id)
    carl_new = person_row(call_person("k4"))
    check("new caller: the person is named from the user's contacts and shown; the number is theirs",
          len(yael) == 1 and yael[0]["hidden"] == 0 and yael[0]["source"] == "auto"
          and call_person("k2") == yael[0]["id"] and phones_of(yael[0]["id"]) == ["972501110010"]
          and made_robin["created"] == 1, (yael, made_robin))
    check("new caller: a number of someone the user already has links to that person (no second one)",
          call_person("k3") == rows("SELECT id FROM people WHERE name = 'Two Contacts'")[0]["id"]
          and people_count(ADMIN) == before[ADMIN.id] + 1)
    check("new caller: without contacts the person is the number, hidden, as before",
          carl_new["name"] == "+972501110010" and carl_new["hidden"] == 1 and made_carl["created"] == 1
          and people_count(CARL) == before[CARL.id] + 1, carl_new)
    add_call("k5", ADMIN, "", "+972501110055")  # Dan Renamed-In-Google's second number; no person has it
    dan = people_store.create_person(ADMIN, "Dan Renamed-In-Google")
    linking.link_pending(ADMIN)
    check("new caller: the contact's name matches an existing person: the number is added to them",
          call_person("k5") == dan["id"] and "972501110055" in phones_of(dan["id"])
          and people_count(ADMIN) == before[ADMIN.id] + 2)


# --- 6. Another refresh token, disconnecting -----------------------------------------------------------

async def test_connection_changes() -> None:
    requests()
    bob_named = rows("SELECT id FROM people WHERE user_id = ? AND name = 'Gali Bob'", BOB.id)[0]["id"]
    store.update(BOB, "contacts", credentials={"refresh_token": "bob2-refresh"})
    await settle()
    reqs = requests()
    check("another refresh token: everything is read again from that account, without the old sync token",
          [(r["account"], r.get("syncToken")) for r in reqs] == [("bob2", None)]
          and copy_ids(BOB) == ["people/x1"] and contacts_store.sync_token(BOB) == "bob2-sync-1"
          and contacts_store.caller_name(BOB, "0501110001") is None, (reqs, copy_ids(BOB)))
    check("another refresh token: a person filled from the old account keeps the name, not the link",
          person_row(bob_named)["name"] == "Gali Bob" and person_row(bob_named)["contact_resource"] == "")

    robin_before = copy_ids(ADMIN)
    check("disconnect: Bob's copy, numbers and sync token are deleted; the others keep theirs",
          store.disconnect(BOB, "contacts") and copy_ids(BOB) == []
          and not rows("SELECT 1 FROM google_contacts WHERE user_id = ?", BOB.id)
          and not rows("SELECT 1 FROM google_contact_phones WHERE user_id = ?", BOB.id)
          and not rows("SELECT 1 FROM google_contacts_sync WHERE user_id = ?", BOB.id)
          and copy_ids(ADMIN) == robin_before and copy_ids(EVE) == ["people/e1"]
          and contacts_store.sync_token(ADMIN) and contacts_store.sync_token(BOB) == "")
    linked_before = rows("SELECT COUNT(*) AS n FROM people WHERE user_id = ? AND contact_resource != ''", ADMIN.id)[0]["n"]
    store.disconnect(ADMIN, "contacts")
    kept = person_row(rows("SELECT id FROM people WHERE user_id = ? AND name = 'Yael New-Caller'", ADMIN.id)[0]["id"])
    check("disconnect: people keep what was filled in, the links to contacts are forgotten",
          linked_before > 0 and kept["name"] == "Yael New-Caller" and kept["hidden"] == 0
          and not rows("SELECT 1 FROM people WHERE user_id = ? AND contact_resource != ''", ADMIN.id)
          and copy_ids(ADMIN) == [])
    refused = []
    for user in (ADMIN, BOB):
        try:
            await tool(user, "search_contacts", query="gal")
            refused.append("ran")
        except registry.ToolNotAvailable as e:
            refused.append(str(e))
    with users.acting_as(ADMIN):
        card = detail.lookup(ADMIN, "+972504440001")
        none_synced = await sync.sync_user(ADMIN)
    check("disconnect: the tools are refused like unknown ones, no caller names, nothing synced, no Google call",
          refused == ["Tool 'search_contacts' is not registered."] * 2 and card == {"found": False}
          and none_synced is None and contacts_store.caller_name(ADMIN, "0501110010") is None
          and "search_contacts" not in agent.instructions(ADMIN) and not requests(), refused)
    out = await sync.sync_all()
    check("loop: only Eve is left to sync", list(out) == [EVE.id] and {r["account"] for r in requests()} == {"eve"}, out)

    store.connect(BOB, "contacts", credentials={"refresh_token": "bob-refresh"})
    await settle()
    reqs = requests()
    check("connect again: a full read into a fresh copy",
          [r.get("syncToken") for r in reqs] == [None] and copy_ids(BOB) == ["people/b1", "people/b2", "people/bob-new1"]
          and person_row(bob_named)["contact_resource"] == "people/b1", (reqs, copy_ids(BOB)))


async def main() -> None:
    test_kind()
    await test_sync()
    await test_tools()
    await test_people()
    await test_caller_name()
    await test_connection_changes()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
