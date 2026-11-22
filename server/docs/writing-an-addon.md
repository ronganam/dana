# Writing an addon

An addon is a folder with an `addon.py`; everything it adds to Dana is declared on the `addon` object there, as a field or with `@addon.<hook>`. Official addons and yours are written the same way. The design: [`../../docs/addons.md`](../../docs/addons.md).

## Make one in five steps

Commands run from `server/`. The addon goes in your site (`site/addons/`, or `$DANA_SITE/addons`; `../../docs/site.md`).

**1. New.** `bin/dana addons new tides` writes `site/addons/tides/`:

```
tides/                its own git repository (one commit)
  addon.py            addon = Addon("tides", ...): needs, touches, a setting, a line of the agent's instructions
  tools.py            tides_hello, tides_fact (an HTTP call), tides_rhyme (a model call)
  hooks.py            a Now card and a day item
  tests/              test_tides.py, with dana_harness.py (finds Dana) and fakes.py (users, model, HTTP)
  AGENTS.md           the author's guide: the loop, the rules, where each capability is documented
  REFERENCE.md        the add-on reference (addon-reference.md), copied in
  README.md           what it does (the catalog shows it)
```

`--title "Tide times"` names it for people; `--dir DIR` writes somewhere else. `--web` and `--worker` add a page and a sidecar (step 5, "Workers").

**2. Tool.** Change the tool in `tools.py` into what your addon does:

```python
from core.addons import Connection
from addons.tides.addon import addon

@addon.tool()
def tides_next(conn: Connection, place: str) -> str:
    """The next high and low tide at a place.

    Args:
        place: A beach or harbour.
    """
    return f"High tide at {place}: 14:05"            # conn.user, conn.settings, conn.credentials
```

The model sees the name, the docstring and the typed parameters; `conn` (the user's connection) is left out. Say when to use it in the manifest's `prompt_parts` (`PromptPart("guides", "For tide times, use tides_next.")`).

**3. Test.** Change `tests/test_tides.py` to call your tool, then `bin/dana addons check site/addons/tides` and `bin/dana addons test site/addons/tides` (or `bin/test tides`). The scaffold's test already has the users every addon test needs: Robin and Gal have the addon (each with their own setting), Maya does not, and it checks that she is refused.

**4. Turn it on.** Restart Dana (addons load at start). Turn it on in Settings → Addons, or `bin/dana addons connect <user> tides`. Ask Dana about the tides.

**5. Page.** A page in the web app is a script in the addon's `web/` folder and a line in the manifest:

```python
web=(Page("tides", panel="tides.js", style="tides.css", nav=Nav("Tides", icon="waves", order=60)),),
```

```js
// web/tides.js
(() => {
  "use strict";
  Dana.panel({
    async mount(el, api) {
      if (!el.firstChild) el.innerHTML = `<header class="top-bar"><span class="top-bar-title">Tides</span></header><div data-next></div>`;
      el.querySelector("[data-next]").textContent = (await api.call("/api/tides")).next;
    },
  });
})();
```

and the route it reads in `api.py` ("Routes"). `bin/dana addons new tides --web` writes all three. Restart, and the page has a button in the navigation of every user who has the addon: among the places by its `order`, in the main bar when the user pins it (`Nav(..., primary=True)` puts it there by default, for a page most users open daily; `web-design.md`, "Navigation").

From here: [`examples/addons/hello/`](../../examples/addons/hello/) is the same shape grown by a table, a job, an event handler and a Now card; `addons/tasks/` uses most hooks. [`examples/addons/showcase/`](../../examples/addons/showcase/) uses every capability once; the [reference](addon-reference.md) quotes it. To share an addon, `bin/dana addons publish` ("The add-on kit").

## The add-on kit

Commands for building an addon outside Dana's tree (`server/cli/kit/`; each takes the addon's folder):

| Command | What it does |
|---|---|
| `bin/dana addons new NAME [--dir PATH]` | writes the addon from `server/cli/template/` into its own git repository |
| `bin/dana addons check PATH [--json]` | the manifest against its JSON schema, imports (loaded in a clean process), migration names and order, a documented `Args:` line for every tool parameter, and what the code does against what it declares: hosts it calls (static scan of string literals in calls) against `touches.hosts`, environment values it reads against `needs` (`Need("env", …)`, a worker's `url_env`), imports of other addons against `needs` (`Need("addon", …)`). Each problem names the file and line and says the fix; exit 1 on an error |
| `bin/dana addons test PATH [-v]` | runs the addon's `tests/test_*.py` and `test_*.mjs`, each in a clean process with `DANA_HOME` set to this checkout, Dana's `.env` values removed |
| `bin/dana addons dev PATH [--once]` | checks it, starts the local test environment on your working tree with the addon mounted (`DANA_LOCAL_ADDONS`, `local-testing.md`), connects it for the first user and prints its log lines, tools, Now cards and day items; then watches the folder and, on a save, restarts the app and prints them again |
| `bin/dana addons publish PATH [--catalog-repo OWNER/dana-catalog] [--dry-run]` | runs check and test, pushes the addon to `OWNER/dana-addon-NAME` with `gh` (created if missing), tags `v<version>` with a release, and opens a pull request to the catalog repository adding or replacing its entry pinned to that full commit; `--dry-run` prints every `git` / `gh` command and the entry instead |
| `bin/dana addons reference [--check]` | writes [`addon-reference.md`](addon-reference.md) from the code (manifest fields, hooks with signatures and examples from the showcase, the manifest JSON schema); `test_addon_kit.py` fails when it is out of date |

The scaffold's tests run without Dana's tree inside the addon: `tests/dana_harness.py` finds a Dana checkout from `DANA_HOME`, an importable Dana (when run by `bin/dana addons test` or `bin/test`), or a `dana` folder next to the addon, and says how to set `DANA_HOME` when none is found. `tests/fakes.py` has `three_users` (Robin and Gal with the addon, Maya without), `FakeLLM` (answers model calls, records each call's `purpose`) and `FakeHTTP` (a local server for the hosts the addon calls; point the addon's env override at its `url`).

## Reference

### The folder

```
tides/
  addon.py          addon = Addon("tides", ...)          the manifest (required)
  tools.py          @addon.tool() functions              imported when present
  hooks.py          jobs, handlers, Now cards…           imported when present
  demo.py           @addon.demo: sample data for the demo household (docs/household.md)   imported when present
  api.py            setup(app): its HTTP routes          registered automatically
  web/              its pages: scripts, styles           served at /addons/<name>/web/<file>
  worker/           a sidecar's Dockerfile and code      built by bin/deploy as <image>-<name>
  migrations/       m0001_baseline.py, m0002_…           its own tables
  requirements.txt  extra Python packages                checked at load; installed into the image
  tests/            test_*.py, test_*.mjs                run by bin/test
  README.md         what it does, settings, events       shown in the catalog
```

The loader imports `addon.py`, then `tools.py`, `hooks.py`, `demo.py`, the modules named in `modules=(...)`, then `api.py`, as `addons.<name>.*`; another module of the addon imports the manifest as `from addons.tides.addon import addon`. Sources: `server/addons/` (official), the site's `addons/`, `/app/addons-site` in an image built by `bin/deploy`, `<data dir>/addons/` (installed); `DANA_ADDON_DIRS` (folders joined by `:`) replaces all but the official one. A non-official addon that fails to load is skipped with the reason (`loader.failed()`, the Addons page); the server runs without it.

### The manifest

```python
from core.addons import Addon, Field, Nav, Need, Page, PromptPart, Touches, Worker

addon = Addon("tides", title="Tides", description="Tide times for your beaches", version="1.0.0",
              settings=(Field("beach", "Your usual beach", default="Main beach"),),   # per user, plain JSON
              credentials=(Field("api_key", "Tide service key"),),                    # stored encrypted
              prompt_parts=(PromptPart("guides", "For tide times, use tides_next."),),
              llm_jobs={"tides_summary": "fast"},
              web=(Page("tides", panel="tides.js", style="tides.css", nav=Nav("Tides", icon="waves")),),
              worker=Worker(port=8091, url_env="TIDES_WORKER_URL"),
              needs=(Need("account", "a tide service account", "Its API key gives the tide tables"),
                     Need("addon", "homeassistant", "Your home's location, for the nearest beach", optional=True)),
              touches=Touches(hosts=("api.tides.example",), stores_credentials=True))
```

Every field and its meaning: the `Addon` docstring in `core/addons/addon.py`; the table in `../../docs/addons.md`, "The manifest".

- **Prompt slots**: `PromptPart(slot, text, rank)`; the slots are `SLOTS` in `core/agent.py`, each with its joiner (`tools` lists "a, b, and c", `guides` are paragraphs). `{user}` becomes the user's name, `{currency}` their currency. An unknown slot fails the load.
- **Needs and touches**: say what running it takes and what it may touch, so people see it before turning it on (the Addons page, `bin/dana addons table`, the README for official addons; `../../docs/addons.md`, "Needs and touches"). `Need(kind, what, why, optional=False)`: `env` (the variable's name), `account`, `hardware` (a device or a server you run), `app` (the Dana Android app), `region`, `addon` (another addon's name; required ones are loaded first and must be on). `Touches(hosts, acts_outside, stores_credentials, reads)`: the hosts it contacts, whether it sends or changes things for the user outside Dana, whether it keeps credentials (required when it declares `credentials`), other addons whose data it reads. Write them with plain values (strings, tuples, True / False): the server reads `touches=` from `addon.py` without running it before installing, and an update whose touches grew asks the admin again. They are a disclosure, not a sandbox.
- **LLM jobs**: each purpose passed to `core.llm.complete(purpose=...)` is declared in `llm_jobs` with its default profile (`fast` / `balanced` / `deep`, or `LlmJob("fast", "Label")`); `MODEL_<PURPOSE>` in env overrides it.

### Make it easy to set up

The Addons page is a step-by-step setup built only from your manifest; it knows no addon by name. Give it the words once, in plain language for someone who is not technical:

```python
addon = Addon(
    "weather", title="Weather", icon="cloud-sun",                     # a Lucide icon name
    description="The weather where you are, in chat and the briefing",  # one line in the list
    features=("Today's weather and the week ahead", "Rain alerts before you leave"),  # "What it adds"
    setup=("Make a free account at openweathermap.org.",),            # steps outside Dana, before connecting
    examples=("Will it rain tomorrow?",),                              # "Try asking Dana", once it's on
    disconnect_warning="",                                             # what turning it off deletes ("" = nothing)
    credentials=(Field("api_key", "OpenWeather API key", label="API key",
                       help="On openweathermap.org: your name → My API keys → copy the Default key.",
                       url="https://home.openweathermap.org/api_keys", placeholder="0123456789abcdef"),),
    test=check,                                                        # run by itself right after Connect
)
```

- `Field(label=, help=, url=, placeholder=)`: the field's short name, where to find the value (one sentence, at most 200 characters), an `https://` page where it is found or made (shown as "Where to find it"; anything else is refused when the addon loads), and an example inside the empty input. `description` stays what the value is for (shown when there is no `help`).
- `features`, `setup`, `examples`: tuples of short sentences (at most 8, 200 characters each). Without `features`, the page lists the first docstring line of your tools. Pages your `web=` adds are named under "What it adds" by themselves.
- The steps the page shows: your `setup` lines, then the sign-in button (`connect_url`, `connect_label`) or the fields (credentials, and settings that are addresses), then Connect. Connect runs your `test=` and shows its reason in plain words when it fails, so return a reason a person can act on ("The API key was refused") rather than an exception name. Other settings appear once the addon is on, with suggestions from `options=`.
- A new user with no optional addon on sees a welcome suggesting the addons they can turn on (not `default_on` / `everyone` ones, nor ones that need a capability); yours is among them automatically.
- `tests/test_addons.py` checks that every official addon has an icon and features and every field a label and help.

### Tools

`@addon.tool(...)` on `def f(conn, ...)` or `async def`. A write runs at once and registers an undo (`is_write=True`, `core/undo.py`); `confirm=True` asks the user first, and `describe=` gives the Approve card's words:

```python
def describe_delete(user: User, a: dict) -> Approval:
    return Approval(title=f"Delete “{a['title']}”", heading="Delete", emoji="🗑", question="Delete it?")

@addon.tool(is_write=True, confirm=True, describe=describe_delete)
```

Other flags: `acts_outside`, `untrusted_result` (`agent-actions.md`), `needs`, `available`, `shortcut` / `shortcut_prep` / `shortcut_describe` (learned phrases); each is described in `Addon.tool`'s docstring. An unknown flag is a `TypeError`.

### Background work

```python
@addon.job(every=minutes(5))            # or daily="07:30", or at_start=True; delay= seconds; timeout=600.0
async def refresh(user: User) -> int:   # once per user who has the addon active, as that user
    return store.refresh(user)

@addon.service
async def watch(app) -> None:           # once per server; started again with backoff when it ends
    await my_websocket_loop()
```

One user's failure or timeout is logged and the next user still runs. `every=` and `daily=` keep their last run across restarts. Tests run one pass with `await scheduler.run_once(registry.job("tides.refresh"))`.

### Events and notifications

```python
events.emit("tides.tide.turned", user, place=place)          # core.events; never raises

@addon.on("tasks.task.created")
async def on_task(user: User, data: dict) -> None: ...       # in the background, as that user

await notify.send(user, notify.Message(text="High tide soon", kind="tide", data={}))

@addon.channel("push")
async def push(user: User, message: notify.Message) -> bool: ...   # True when delivered
```

Event names are `<addon>.<thing>.<what happened>`; list the ones you emit in your README. Tests wait with `await events.drain()`.

### Composer hooks

The Now screen, the briefing, the weekly review, search, self-checks and context chats are built from hooks; each is in the table of `../../docs/addons.md`, "The manifest", with its signature, and `examples/addons/hello/hooks.py` has a Now card with its action:

```python
@addon.now_cards
def cards(user: User, ctx: NowContext) -> list[Card]:
    return [Card(id=f"tides:{t['id']}", kind="tides", source="Tides", title=t["title"],
                 primary=core_cards.action("done", "Seen")) for t in store.open(user)]

@addon.now_action("tides")                                      # cards whose id is "tides:<ref>"
async def act(user, ref: str, action: str, args: dict, now) -> dict:
    store.done(user, ref)
    return core_cards.result("Seen")                            # core_cards.ActError(msg, 409) refuses
```

`core_cards` is `from core import cards as core_cards`. Composer hooks are plain functions (they run in a worker thread); jobs, services, handlers, channels and the action hooks are `async`. Keys (a card prefix, a section, a check) are unique across addons. `core.addons.helpers` has shared time and search helpers (`parse_time`, `ago`, `when_phrase`, `match_score`, `like_pattern`…).

### Data

```python
# migrations/m0001_baseline.py
VERSION, NAME = 1, "baseline"
def up(conn): conn.execute("CREATE TABLE tides_places (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL)")
```

Numbered 1, 2, 3… per addon, applied at start (after a snapshot) inside the runner's transaction (never commit); a change is the next file, never an edit. Every query filters by the user: `tests/test_sql_scope.py` fails a query on a table with a `user_id` column that does not name `user_id`, unless it carries a `# scoped:` comment with the reason. Never `CREATE TABLE` outside a migration.

### Routes

```python
async def get_tides(request: web.Request) -> web.Response:
    conn = store.require(request_user(request), "tides")      # 403 not_connected for a user without it
    return web.json_response({"next": "High tide: 14:05"})

def setup(app: web.Application) -> None:                       # api.py; behind the same sign-in as every route
    app.router.add_get("/api/tides", get_tides)
```

While an admin has disabled the addon its routes answer 404 `addon_disabled` before your handler runs.

### Pages

`web=(Page(id, panel, style="", nav=None), …)`: any number of pages. The id names the screen (`#panel-<id>`; `/<id>` and `#<id>` open it), unique across addons and never a core screen's (`now`, `chat`, `settings`, `addons`, `usage`, `debug`, `review`). A page without `nav` has no button and opens from a link or `api.open(id)`. The shell loads each page's script when the app starts, the style the first time it opens, and gives it its own `<section>`.

```js
Dana.panel({
  scripts: ["tides-core.js"],            // optional: files beside it, loaded first, in order
  start(api) {},                         // optional: once, at registration (a badge: api.badge(n))
  async mount(el, api, arg) {},          // each time it opens; el is empty the first time; arg from api.open(id, arg)
  unmount() {},                          // optional: when the user leaves it
  opens: ["tide"],                       // optional: the ref types it opens (a Now card's ref.type, a day item's <type>_id)
});
```

With `opens`, the Now screen opens this page for a card or a day item that refers to one of those types, with the thing's id as `arg` (the tasks page opens `task`, Calls `call`, Inbox `notification`, Calendar `event`).

`api` (one object per page):

| | |
|---|---|
| `api.addon`, `api.page`, `api.base` | the addon's name; this page's id; the URL of its `web/` folder |
| `api.me` | who is signed in (`GET /api/me`) |
| `api.json(method, path, body)` | a JSON request that never throws: `{status, body}` (status 0: no answer) |
| `api.call(path, {method, body})` | a JSON request that throws `Error` (`.status`, `.body`) unless 2xx |
| `api.fetch(path, init)` | `fetch`, signed in, never from a cache (uploads) |
| `api.stream(path, {event: fn(data, ev)})` | Server-Sent Events; returns `{close()}` |
| `api.open(id, arg)` | a core screen (`now`, `chat`, `settings`, `addons`, `review`, `usage`) or any page by its id |
| `api.openSession(sid)` | a chat session in Chat |
| `api.piece(name, el)` | draws a core piece into an element of the page: `"today"` (the morning briefing and the weekly-review banner) |
| `api.badge(value, tone)` | this page's navigation badge: a count (0 / null hides it) or a short text; tone `"quiet"` |
| `api.toast(text)`, `api.visible()`, `api.icons()`, `api.esc(text)` | a short message; whether this page is on screen; draw `<i data-lucide>` icons; HTML-escape |
| `api.store.get(key, fallback)` / `.set` / `.remove` | `localStorage` that never throws |

A page uses only its own elements and `api`. It builds on the shell's tokens and classes (`static/styles.css`: `--ink`, `--canvas`, the named hues `--sage` / `--sage-tint` / `--sage-text` …, `.top-bar`, `.btn-primary`, `.chip`, `.form-control`; `static/kit.css`: `.ibx` list and detail panes, toolbars, modals, fields) and the shared motion (`api.motion`: rows marked `.dn-row` rise in on the page's first opening, `complete(row)`, `flash(el)`, `number(el, text)`, skeletons with `.dn-shimmer`); `docs/web-design.md` says which colour means what and which motion goes where. Put pure logic in a `web/<x>-core.js` that sets `globalThis.<Name>` and test it with `tests/test_web_<x>.mjs` (the official pages do: shopping, projects, money, tasks, calls).

### Dashboard widgets

A tablet's or TV's dashboard (`dashboard.md`) is built from widgets addons declare. The function gives the widget's data for a user as JSON; a script in `web/` draws it:

```python
@addon.widget("tides_next", title="Next tide", script="widget.js", icon="waves", refresh=300,
              sizes=("small", "medium"),                         # of small / medium / large; the first is the default
              settings=(Field("beach", "The beach", default="", required=False, label="Beach"),),
              description="The next high and low tide")
def next_tide(user: User, ctx: WidgetContext) -> dict:          # or async
    return {"beach": ctx.settings["beach"], "next": store.next_tide(user, ctx.settings["beach"], ctx.now)}
```

```js
// web/widget.js
Dana.widget("tides_next", {
  style: "widget.css",                   // optional: a stylesheet beside it, loaded once
  mount(el, w, api) {                    // w = {id, widget, size, settings, data}
    el.innerHTML = `<div class="dw-big">${api.esc(w.data.next)}</div>`;
  },
  update(el, w, api) {},                 // optional: new data (default: emptied and mounted again)
  unmount(el, api) {},                   // optional: stop timers, close streams
});
```

The key is unique across addons. Settings are text `Field`s (at most 8) a person sets per placed widget on the editor; `ctx.settings` has each, its default filled in. `refresh` is how often (seconds, 0 or ≥ 10) the dashboard reads the data again; the widget's `api` adds `size`, `settings`, `refresh()`, `night()`, `kiosk` and `setValue(el, text)` to a page's. A user without the addon gets `not_connected` where a preset placed it, and the editor does not offer it. Build on the dashboard's shared classes (`.dw-big`, `.dw-sub`, `.dw-list` / `.dw-item`, `.dw-empty`…; `docs/web-design.md`, "Dashboards"): the dashboard is a low-power WebView, so no entrance motion.

### Workers

`worker=Worker(port=8091, url_env="TIDES_WORKER_URL")` declares a sidecar built from the addon's `worker/` folder (its own `Dockerfile`, self-contained; the app image leaves it out); the addon reaches it at the URL in `url_env`. An addon published in its own repository (a catalog addon) builds its worker image there instead (a workflow pushing `ghcr.io/<owner>/<repo>-worker:<commit>`) and names it, `Worker(port, url_env, image="ghcr.io/<owner>/<repo>-worker")`: deployments then run that image at the installed commit and build nothing. `bin/dana addons new tides --worker` writes a small standard-library server and a tool that calls it. Deploying and running workers: `operations.md`, "Workers".

### Testing

`bin/test` runs `tests/test_*.py` and `test_*.mjs` of every official and site addon. A script starts with `import testenv; testenv.isolate("<name>")` (a clean environment and a temporary database; `DANA_ADDON_DIRS=""`), and a site addon's test sets `DANA_ADDON_DIRS` to the folder holding it before importing the app, as the scaffold's `dana_harness.start()` does. Cover two users with the addon and one without.
