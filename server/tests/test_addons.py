"""Checks for the addons framework (docs/access.md, "Addon"). Run: bin/test test_addons

Discovery, schema parsing and execution of every addon's tools, listed and run
for a user; connections with encrypted credentials; the startup key check
(``CREDENTIALS_KEY``, else the generated key file) and the first admin's default
addons (nothing is read from env). Nothing calls Home Assistant, WAHA, Google or
the web: only tools that stay inside Dana are executed.
"""

import asyncio
import os
import re
import sqlite3
import sys

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
# Every addon / identity env name is unset: this test decides what the environment configures.
testenv.isolate("addons_test")
os.environ["HOMEASSISTANT_TOKEN"] = "ha-env-token"  # the old env import's names connect nothing any more
os.environ["FRIGATE_URL"] = "https://frigate.example.com"
os.environ["OWNER_NAME"] = "Robin"

from cryptography.fernet import Fernet

from core import db, users
from core.access import MEMBER
from core.paths import DATABASE_PATH
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
ensure_default_groups_for_everyone()

import addons
from addons import bootstrap as addons_bootstrap
from core.addons import Addon, Field, crypto, loader, registry, store
from core.addons.addon import MAX_HELP, MAX_LINES, forget
from core.addons.crypto import CredentialsKeyError
from core.addons.addon import all_tools, find_tool, get_addon, all_addons

ADMIN = users.first_admin()
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def raises(fn, exc, contains=""):
    try:
        fn()
    except exc as e:
        return contains in str(e)
    except Exception:
        return False
    return False


async def araises(coro, exc):
    try:
        await coro
    except exc as e:
        return str(e)
    return None


def names(schemas):
    return [(s.get("function") or s)["name"] for s in schemas]


# --- 1. Startup with nothing to protect -------------------------------------------------

DEFAULTS = ["tasks", "shopping", "knowledge", "about", "people", "phone"]


def test_startup_without_key():
    key_file = crypto.key_file()
    check("startup: no key file before the first start", not key_file.exists() and not crypto.key_configured())
    turned_on = addons_bootstrap.bootstrap()
    check("startup: no CREDENTIALS_KEY and nothing encrypted -> a key file is generated (0600) and used",
          key_file.exists() and (key_file.stat().st_mode & 0o777) == 0o600 and crypto.key_source() == "file"
          and Fernet(key_file.read_text().strip().encode()) is not None, oct(key_file.stat().st_mode))
    check("startup: the first admin gets the default addons (default_on), not ops", turned_on == DEFAULTS
          and all(get_addon(k).default_on for k in DEFAULTS) and not store.is_connected(ADMIN, "ops"), str(turned_on))
    check("startup: nothing comes from env (HOMEASSISTANT_TOKEN, FRIGATE_URL set: not connected)",
          not any(store.is_connected(ADMIN, k) for k in ("homeassistant", "whatsapp", "calendar", "cameras")))
    check("startup: the defaults' settings are the declared defaults",
          dict(store.require(ADMIN, "shopping").settings) == {"active_list": None},
          str(dict(store.require(ADMIN, "shopping").settings)))
    check("startup: second run turns nothing on, keeps the key file",
          addons_bootstrap.bootstrap() == [] and crypto.key_source() == "file")
    store.disconnect(ADMIN, "shopping")
    check("startup: a default addon the admin turned off stays off at the next start",
          addons_bootstrap.bootstrap() == [] and not store.is_connected(ADMIN, "shopping"))
    store.connect(ADMIN, "shopping")
    testenv.connect_admin("cameras", settings={"url": "https://frigate.example.com"})


# --- 2. Discovery and schemas -----------------------------------------------------------

EXPECTED_ORDER = ["essentials", "tasks", "shopping", "knowledge", "about", "people", "phone", "homeassistant", "whatsapp",
                  "calendar", "contacts", "gmail", "telegram_account", "money", "cameras", "ops"]


def test_discovery():
    check("discovery: every addon registered, in the fixed order", [k.name for k in all_addons()] == EXPECTED_ORDER,
          str([k.name for k in all_addons()]))
    check("discovery: the loader's order is the order", list(loader.loaded()) == EXPECTED_ORDER)
    tools = all_tools()
    check("discovery: 102 tools", len(tools) == 102, str(len(tools)))
    check("discovery: tool names unique", len({t.name for t in tools}) == len(tools))
    check("discovery: cameras (no tools) has none, every other addon has some",
          not get_addon("cameras").tools and all(get_addon(k).tools for k in EXPECTED_ORDER if k != "cameras"))
    check("discovery: money tools", [t.name for t in get_addon("money").tools]
          == ["money_summary", "search_transactions", "money_overview", "recurring_charges", "money_forecast",
              "money_insights", "meal_card", "investment_portfolio", "categorize_transaction", "sync_money", "saving_opportunities",
              "update_saving"], [t.name for t in get_addon("money").tools])
    check("discovery: core tools", [t.name for t in get_addon("essentials").tools]
          == ["get_current_time", "request_user_selection", "web_search", "share_item"])
    check("discovery: ops is admin only, core is everyone's",
          get_addon("ops").requires == "ops" and get_addon("essentials").everyone)
    for t in tools[:3] + tools[-3:]:
        print(f"  - {t.addon}.{t.name}: {t.schema['function']['description'][:70]}")


def test_schemas():
    bad = []
    for t in all_tools():
        s = t.schema
        fn = s.get("function", {})
        params = fn.get("parameters", {})
        if (s.get("type") != "function" or fn.get("name") != t.name or not fn.get("description")
                or params.get("type") != "object" or not set(params.get("required", [])) <= set(params.get("properties", {}))):
            bad.append(t.name)
    check("schema: every tool has an OpenAI-compatible function schema", not bad, str(bad))
    with_conn = [t.name for t in all_tools() if "conn" in t.schema["function"]["parameters"]["properties"]
                 or "conn" in t.schema["function"]["parameters"]["required"]]
    check("schema: conn is in no schema", not with_conn, str(with_conn))
    add_task = find_tool("add_task").schema["function"]["parameters"]
    check("schema: add_task needs a title, priority optional",
          add_task["required"] == ["title"] and "priority" in add_task["properties"], str(add_task["required"]))
    ws = find_tool("web_search").schema["function"]
    check("schema: docstring descriptions (web_search query)",
          ws["parameters"]["properties"]["query"] == {"type": "string", "description": "The search query string."}
          and ws["description"].startswith("Search the web"))
    forget = find_tool("forget_info").schema["function"]["parameters"]["properties"]
    check("schema: **kwargs is not a parameter", "kwargs" not in forget, str(list(forget)))
    rt = registry.realtime_tool_schemas(ADMIN)
    check("schema: realtime list is flat", all("function" not in s and s["type"] == "function" and "name" in s
                                               for s in rt))

    demo = Addon("zz_schema_demo")
    try:
        @demo.tool()
        async def zz_no_conn(query: str) -> str:
            """No connection parameter."""
            return query
        no_conn_refused = False
    except TypeError:
        no_conn_refused = True
    check("schema: a tool whose first parameter is not conn is refused", no_conn_refused)
    check("schema: a tool name owned by another addon is refused",
          raises(lambda: demo.tool(name="add_task")(lambda conn: None), ValueError, "tasks"))


# --- 3. Who has what --------------------------------------------------------------------

MEMBER_USER = None


def test_per_user_lists():
    global MEMBER_USER
    MEMBER_USER = users.create("Member", MEMBER)
    check("users: a new user has only core", store.connected_addons(MEMBER_USER) == ("essentials",),
          str(store.connected_addons(MEMBER_USER)))
    member_tools = names(registry.tool_schemas(MEMBER_USER))
    check("tools: the member gets the core tools only", member_tools
          == ["get_current_time", "request_user_selection", "web_search"], str(member_tools))
    admin_tools = names(registry.tool_schemas(ADMIN))
    expected = [t.name for k in all_addons() if k.name in store.connected_addons(ADMIN) for t in k.tools]
    check("tools: the admin's list is the tools of their addons, in addon then definition order",
          # two users: core has share_item (the admin has tasks, an addon with shareable items) and shopping
          # has choose_shopping_list
          admin_tools == expected and len(admin_tools) == 4 + 7 + 5 + 15 + 4 + 4 + 11, str(len(admin_tools)))
    check("tools: no HA / WhatsApp / calendar tools without those addons",
          not {"call_ha_service", "send_whatsapp_message", "add_event"} & set(admin_tools))
    check("tools: the order is deterministic (same list every time, realtime too)",
          names(registry.tool_schemas(ADMIN)) == admin_tools and names(registry.realtime_tool_schemas(ADMIN)) == admin_tools)
    check("tools: core cannot be connected or disconnected",
          raises(lambda: store.connect(MEMBER_USER, "essentials"), ValueError)
          and raises(lambda: store.disconnect(MEMBER_USER, "essentials"), ValueError))
    check("tools: ops cannot be connected for a member", raises(lambda: store.connect(MEMBER_USER, "ops"), PermissionError)
          and not store.is_connected(MEMBER_USER, "ops"))
    check("tools: connecting twice is refused", raises(lambda: store.connect(ADMIN, "tasks"), ValueError))
    check("tools: unknown addon refused", raises(lambda: store.connect(ADMIN, "nope"), KeyError))


async def test_execution():
    out = await registry.execute(ADMIN, "add_task", {"title": "Verify the addons", "priority": "high"})
    check("execute: add_task for the admin", "Verify the addons" in str(out) and "Error" not in str(out), str(out))
    out = await registry.execute(ADMIN, "list_tasks", {"completed": False})
    check("execute: list_tasks sees it", "Verify the addons" in str(out), str(out)[:200])
    out = await registry.execute(ADMIN, "create_project", {"title": "Addons test project", "tags": ["test"]})
    import re
    m = re.search(r"ID `([^`]+)`", str(out))
    project_id = m.group(1) if m else None
    check("execute: create_project", project_id is not None, str(out))
    out = await registry.execute(ADMIN, "save_knowledge_item", {
        "project_id": project_id, "title": "Framework note", "body": "## Hello\n\nWorld", "kind": "note",
        "fields": [{"name": "Score", "type": "number", "value": "9"}], "tags": ["test"]})
    check("execute: save_knowledge_item", "Error" not in str(out), str(out))
    out = await registry.execute(ADMIN, "search_knowledge", {"query": "framework note", "project_id": project_id})
    check("execute: search_knowledge finds it", "Framework note" in str(out), str(out)[:200])
    out = await registry.execute(ADMIN, "delete_project", {"project_id": project_id})
    check("execute: delete_project", "Error" not in str(out), str(out))
    out = await registry.execute(MEMBER_USER, "get_current_time", {})
    check("execute: a core tool for the member (sync tool)", str(out).startswith("The current time is"), str(out))
    out = await registry.execute(MEMBER_USER, "request_user_selection",
                                 {"prompt": "Which?", "options": [{"label": "A", "value": "a"}]})
    check("execute: request_user_selection for the member", "- A" in str(out), str(out))

    unconnected = await araises(registry.execute(MEMBER_USER, "add_task", {"title": "x"}), ValueError)
    missing = await araises(registry.execute(MEMBER_USER, "zz_no_such_tool", {}), ValueError)
    check("execute: a tool of an unconnected addon is refused by name", unconnected is not None, str(unconnected))
    check("execute: the refusal looks like the one for a tool that does not exist",
          unconnected is not None and missing is not None
          and unconnected.replace("add_task", "X") == missing.replace("zz_no_such_tool", "X"),
          f"{unconnected!r} vs {missing!r}")
    admin_ops = await araises(registry.execute(MEMBER_USER, "run_python", {"code": "print(1)"}), ValueError)
    check("execute: the member cannot run an ops tool", admin_ops == "Tool 'run_python' is not registered.", str(admin_ops))
    no_user = await araises(registry.execute(None, "get_current_time", {}), RuntimeError)
    check("execute: never without a user", no_user is not None)

    from core.agent import AgentOrchestrator
    with users.acting_as(MEMBER_USER):
        msg = await AgentOrchestrator(model="x")._execute_tool("c1", "add_task", {"title": "x"})
    check("execute: through the agent loop the refusal is an error result, not a crash",
          msg["content"] == "Error executing tool: Tool 'add_task' is not registered.", msg["content"])
    check("approval flags unchanged: confirm, callable confirm, acts_outside",
          registry.needs_confirmation("delete_task") and not registry.needs_confirmation("add_task", {})
          and registry.needs_confirmation("add_event") and not registry.needs_confirmation("add_event", {"summary": "x"})
          and registry.needs_confirmation("add_event", {"attendees": "a@b.c"})
          and registry.needs_confirmation("call_ha_service", {}, untrusted=True)
          and not registry.needs_confirmation("call_ha_service", {})
          and registry.is_write_tool("request_user_selection") and not registry.is_write_tool("list_tasks"))


async def test_needs():
    zz_a = Addon("zz_a")
    calls = []

    @zz_a.tool(needs=("tasks",))
    async def zz_needs_tasks(conn, value: str) -> str:
        """A tool that also needs the tasks addon.

        Args:
            value: Anything.
        """
        calls.append((conn.user.id, conn.addon, value))
        return "ok"

    user = users.create("Needs", MEMBER)
    store.connect(user, "zz_a")
    check("needs: not offered with only its own addon", "zz_needs_tasks" not in names(registry.tool_schemas(user)))
    refused = await araises(registry.execute(user, "zz_needs_tasks", {"value": "1"}), ValueError)
    check("needs: refused with only its own addon", refused == "Tool 'zz_needs_tasks' is not registered.", str(refused))
    store.connect(user, "tasks")
    tools = names(registry.tool_schemas(user))
    check("needs: offered once every addon is connected (after the official addons: test addons come last)",
          tools[-1] == "zz_needs_tasks" and "add_task" in tools, str(tools))
    out = await registry.execute(user, "zz_needs_tasks", {"value": "2"})
    check("needs: runs with the tool's own addon as conn", out == "ok" and calls == [(user.id, "zz_a", "2")], str(calls))
    check("needs: link_task_to_person needs tasks",
          find_tool("link_task_to_person").needs == ("tasks",) and find_tool("accept_call_suggestion").needs == ("tasks",))
    store.disconnect(user, "tasks")
    store.connect(user, "people")
    check("needs: people without tasks has no link_task_to_person",
          "get_person" in names(registry.tool_schemas(user))
          and "link_task_to_person" not in names(registry.tool_schemas(user)))


# --- 4. Credentials and the key ----------------------------------------------------

def raw_db_bytes() -> bytes:
    data = b""
    for suffix in ("", "-wal", "-journal"):
        path = str(DATABASE_PATH) + suffix
        if os.path.exists(path):
            with open(path, "rb") as f:
                data += f.read()
    return data


def test_credentials():
    secret = "waha-secret-" + Fernet.generate_key().decode()[:16]
    conn = store.connect(MEMBER_USER, "whatsapp", credentials={"url": "http://waha.test", "api_key": secret,
                                                               "session": "s1"},
                         settings={"voice_telegram_chat_id": 42}, account="972500000000@c.us")
    check("credentials: round trip", dict(conn.credentials) == {"url": "http://waha.test", "api_key": secret,
                                                                 "session": "s1"}, str(dict(conn.credentials)))
    check("credentials: settings and account kept",
          conn.settings["voice_telegram_chat_id"] == 42 and conn.account == "972500000000@c.us")
    with sqlite3.connect(str(DATABASE_PATH)) as raw:
        stored = raw.execute("SELECT credentials, settings FROM connections WHERE user_id = ? AND addon = 'whatsapp'",
                             (MEMBER_USER.id,)).fetchone()
    check("credentials: stored as a Fernet token, not plaintext",
          stored[0].startswith("gAAAA") and secret not in stored[0] and "waha.test" not in stored[0])
    check("credentials: settings are plain JSON", stored[1] == '{"voice_telegram_chat_id": 42}', stored[1])
    check("credentials: the plaintext is nowhere in the database file", secret.encode() not in raw_db_bytes())
    check("credentials: a required credential is checked",
          raises(lambda: store.connect(users.create("NoCreds", MEMBER), "calendar"), ValueError, "refresh_token"))
    check("credentials: unknown names refused",
          raises(lambda: store.update(MEMBER_USER, "whatsapp", credentials={"password": "x"}), ValueError, "password"))
    info = store.list_connections(MEMBER_USER)
    check("credentials: listing never shows them",
          [i.addon for i in info] == ["essentials", "whatsapp"] and info[1].has_credentials
          and not hasattr(info[1], "credentials"))


def test_settings_and_cache():
    before = store.require(ADMIN, "cameras")
    again = store.require(ADMIN, "cameras")
    check("cache: a second lookup reuses the decoded values", before.settings is again.settings)
    after = store.update(ADMIN, "cameras", settings={"url": "https://frigate.example"})
    check("settings: update applies at once (cache invalidated)",
          after.settings["url"] == "https://frigate.example"
          and store.require(ADMIN, "cameras").settings["url"] == "https://frigate.example")
    check("settings: None goes back to the declared default",
          store.update(ADMIN, "cameras", settings={"url": None}).settings["url"] is None)
    check("settings: unknown setting refused",
          raises(lambda: store.update(ADMIN, "cameras", settings={"colour": "red"}), ValueError, "colour"))
    check("settings: updating an addon that is not connected is refused",
          raises(lambda: store.update(MEMBER_USER, "cameras", settings={"url": "x"}), store.NotConnected))
    secret2 = "rotated-" + Fernet.generate_key().decode()[:12]
    conn = store.update(MEMBER_USER, "whatsapp", credentials={"api_key": secret2})
    check("credentials: update merges into the stored ones",
          conn.credentials["api_key"] == secret2 and conn.credentials["url"] == "http://waha.test")

    # A change made outside this process (bin/dana) is seen by the next lookup; the decrypted
    # credentials of the rows it did not touch are kept.
    kept = store.require(MEMBER_USER, "whatsapp").credentials
    check("cache: an unchanged lookup reuses the decrypted copy", store.require(MEMBER_USER, "whatsapp").credentials is kept)
    with sqlite3.connect(str(DATABASE_PATH)) as raw:
        raw.execute("UPDATE connections SET settings = '{\"url\": \"https://elsewhere\"}' "
                    "WHERE user_id = ? AND addon = 'cameras'", (ADMIN.id,))
    check("cache: an outside change is seen at once", store.require(ADMIN, "cameras").settings["url"] == "https://elsewhere")
    check("cache: ... and the other rows' decrypted credentials were kept",
          store.require(MEMBER_USER, "whatsapp").credentials is kept)
    with sqlite3.connect(str(DATABASE_PATH)) as raw:
        raw.execute("INSERT INTO connections (user_id, addon, credentials, settings, account, created_at, updated_at) "
                    "VALUES (?, 'about', NULL, '{}', NULL, '2026-11-22T00:00:00+00:00', '2026-11-22T00:00:00+00:00')",
                    (MEMBER_USER.id,))
    check("cache: an addon connected by another process is listed by the next lookup",
          "about" in store.connected_addons(MEMBER_USER))
    with sqlite3.connect(str(DATABASE_PATH)) as raw:
        raw.execute("DELETE FROM connections WHERE user_id = ? AND addon = 'about'", (MEMBER_USER.id,))
    check("cache: ... and gone after another process disconnected it", "about" not in store.connected_addons(MEMBER_USER))
    check("cache: disconnect removes the tools at once",
          store.disconnect(MEMBER_USER, "whatsapp") and not store.is_connected(MEMBER_USER, "whatsapp")
          and not store.disconnect(MEMBER_USER, "whatsapp"))


def test_onboarding_words():
    """The manifest's words for the Addons page: typed, checked when declared, filled for every official addon."""
    good = Field("token", "Access token", label="Access token", help="Profile → Security → Create token.",
                 url="https://example.com/tokens", placeholder="abc123")
    check("field: label, help, url and placeholder kept; the title falls back to the name in words",
          (good.title, good.help, good.url, good.placeholder) == ("Access token", "Profile → Security → Create token.",
                                                                  "https://example.com/tokens", "abc123")
          and Field("api_key").title == "Api key")
    check("field: a help link must be https",
          raises(lambda: Field("x", url="http://example.com"), ValueError, "https")
          and raises(lambda: Field("x", url="javascript:alert(1)"), ValueError, "https")
          and raises(lambda: Field("x", url="https://"), ValueError, "https")
          and raises(lambda: Field("x", url="https://exa mple.com"), ValueError, "https"))
    check("field: help is one short sentence", raises(lambda: Field("x", help="a" * (MAX_HELP + 1)), ValueError, "help")
          and Field("x", help="a" * MAX_HELP).help)
    check("field: label and placeholder are short", raises(lambda: Field("x", label="a" * 41), ValueError, "label")
          and raises(lambda: Field("x", placeholder="a" * 81), ValueError, "placeholder"))
    demo = Addon("zz_onboard", icon="cloud-sun", features=("Tells the weather",), setup=("Get a key",),
                 examples=("Will it rain?",), disconnect_warning="Your places are deleted.")
    check("addon: icon, features, setup, examples and the disconnect warning kept",
          (demo.icon, demo.features, demo.setup, demo.examples, demo.disconnect_warning)
          == ("cloud-sun", ("Tells the weather",), ("Get a key",), ("Will it rain?",), "Your places are deleted."))
    forget("zz_onboard")
    check("addon: an icon must be a Lucide name", raises(lambda: Addon("zz_bad", icon="Cloud Sun"), ValueError, "icon"))
    check("addon: lines are a tuple of short strings",
          raises(lambda: Addon("zz_bad", examples="Will it rain?"), ValueError, "tuple")
          and raises(lambda: Addon("zz_bad", setup=("",)), ValueError, "words")
          and raises(lambda: Addon("zz_bad", features=("x",) * (MAX_LINES + 1)), ValueError, "features")
          and raises(lambda: Addon("zz_bad", features=("a" * 201,)), ValueError, "features"))
    plain = Addon("zz_plain")

    @plain.tool()
    def zz_plain_hello(conn) -> str:
        """Say hello to someone.

        More for the model.
        """
        return "hi"
    check("addon: without features= the tools' first docstring lines say what it adds",
          plain.feature_lines() == ("Say hello to someone.",), plain.feature_lines())
    forget("zz_plain")
    official = [a for a in all_addons() if a.name in loader.loaded()]
    lacking = [f"{a.name}: {what}" for a in official if not a.everyone
               for what, missing in (("icon", not a.icon), ("features", not a.features)) if missing]
    lacking += [f"{a.name}.{f.name}: label/help" for a in official for f in (*a.credentials, *a.settings)
                if not (f.label and f.help)]
    check("official addons: each has an icon, features and every field a label and help", not lacking, lacking)
    check("official addons: whatever is set up outside Dana is said in steps (WAHA, Google, Telegram, statements)",
          all(get_addon(n).setup for n in ("whatsapp", "calendar", "contacts", "gmail", "telegram_account", "money")))


def test_key():
    source = open(addons_bootstrap.__file__).read()
    check("no env import: bootstrap reads no env name", "getenv(" not in source and "os.environ" not in source)
    store.connect(ADMIN, "calendar", credentials={"refresh_token": "google-refresh-" + Fernet.generate_key().decode()[:8]})
    file_key = crypto.key_file().read_text().strip()
    os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
    msg = ""
    try:
        addons_bootstrap.bootstrap()
    except CredentialsKeyError as e:
        msg = str(e)
    check("key: CREDENTIALS_KEY wins over the file: another key -> startup refused, saying what to do",
          "CREDENTIALS_KEY is not the key" in msg and "Fernet.generate_key()" in msg, msg)
    os.environ["CREDENTIALS_KEY"] = "not-a-key"
    check("key: a malformed key is refused", raises(addons_bootstrap.bootstrap, CredentialsKeyError, "not a valid"))
    os.environ["CREDENTIALS_KEY"] = file_key
    check("key: the file's key given in env starts", addons_bootstrap.bootstrap() == [] and crypto.key_source() == "env")
    os.environ["CREDENTIALS_KEY"] = ""
    saved = crypto.key_file().read_bytes()
    crypto.key_file().unlink()
    msg = ""
    try:
        addons_bootstrap.bootstrap()
    except CredentialsKeyError as e:
        msg = str(e)
    check("key: stored credentials, no env key and no file -> refused, and no new key is generated",
          "stored credentials" in msg and not crypto.key_file().exists(), msg)
    crypto.key_file().write_bytes(saved)
    check("key: the key file put back starts again", addons_bootstrap.bootstrap() == [])
    check("crypto: generate_key makes a usable key", Fernet(crypto.generate_key().encode()) is not None)


async def main():
    test_startup_without_key()
    test_discovery()
    test_schemas()
    test_per_user_lists()
    await test_execution()
    await test_needs()
    test_credentials()
    test_settings_and_cache()
    test_onboarding_words()
    test_key()


asyncio.run(main())
passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    for name, ok in results:
        if not ok:
            print(f"  FAILED: {name}")
    sys.exit(1)
