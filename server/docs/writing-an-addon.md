# Writing an addon

An addon is a folder with an `addon.py`; everything it adds to Dana is declared on the `addon` object there or with `@addon.<hook>` in its other modules. Official addons and yours are written the same way. The design (and what is not built yet) is [`../../docs/addons.md`](../../docs/addons.md).

## Start from the scaffold

```bash
bin/dana addons new my_addon        # from server/: writes site/addons/my_addon (--dir DIR for another folder)
bin/test my_addon                   # its test passes as written
```

It writes a working addon (from `cli/template/`, shaped like the hello example): `addon.py` with a setting and a prompt line, `tools.py` with `my_addon_hello`, `api.py` with `GET /api/my_addon`, `tests/test_my_addon.py` (two users with it, one without) and a `README.md`. Restart Dana to load it and turn it on in Settings → Addons, or `bin/dana addons connect <user> my_addon`. Grow it from there: [`examples/addons/hello/`](../../examples/addons/hello/) adds a table, a job, an event handler and a Now card with an action; the `tasks` addon (`addons/tasks/`) uses every hook. To ship it to others, put it in a git repository and add an entry to `catalog.json` pinned to a full commit (`../../docs/addons.md`, "The catalog").

## The folder

```
my_addon/
  addon.py          addon = Addon("my_addon", ...)      the manifest (required)
  tools.py          @addon.tool() functions             imported when present
  hooks.py          jobs, handlers, Now cards…          imported when present
  api.py            setup(app): its HTTP routes         registered automatically
  web/              its page: script, style             served at /addons/<name>/web/<file> (web=Web(...); "A web page")
  worker/           Dockerfile of a sidecar             built by bin/deploy as <image>-<name> (worker=Worker(...))
  migrations/       m0001_baseline.py, m0002_…          its own tables
  tests/            test_*.py                           run by bin/test
```

The loader imports `addon.py`, then `tools.py`, `hooks.py`, the modules named in `modules=(...)`, then `api.py`, as `addons.<name>.*`. Another module of the addon refers to it as `from addons.my_addon.addon import addon`.

## Where it lives and the order

| Source | Folder |
|---|---|
| Official | `server/addons/<name>/` |
| Site | `site/addons/` at the repository root, or `$DANA_SITE/addons` (`../../docs/site.md`) |
| Image | `/app/addons-site` (what `bin/deploy` builds in from your site) |
| Installed | `<data dir>/addons/` |

`DANA_ADDON_DIRS` (folders joined by `:`) replaces every source but the official one; `""` means official only. Order: after every addon in `needs=`; then official addons by `order=`, then the rest by name. Two addons with one name stop the start. A non-official addon that fails to import, needs a missing addon or names an unknown prompt slot is skipped (`loader.failed()` says why); the server runs without it.

## The manifest

```python
from core.addons import Addon, Field, PromptPart

addon = Addon("my_addon", title="My addon", description="One line for the Addons page",
              settings=(Field("greeting", "How to greet people", default="Hello"),),
              credentials=(Field("api_key", "API key"),),          # stored encrypted
              prompt_parts=(PromptPart("guides", "Use my_tool when …"),),
              llm_jobs={"my_summary": "fast"}, needs=())
```

Other fields (`default_on`, `everyone`, `requires`, `test=`, `options=`, `validate=`, `on_connect=`, `on_change=`, `connect_url=`, …): the `Addon` docstring in `core/addons/addon.py`.

**Prompt slots**: `PromptPart(slot, text, rank)`; the slots are the table `SLOTS` in `core/agent.py`, each with its joiner (`tools` lists "a, b, and c", `guides` are paragraphs, `send_via` keeps its leading space). `{user}` becomes the user's name, `{currency}` their currency (ISO 4217, `core/user_locale.py`). An unknown slot fails the load.

**LLM jobs**: each purpose the addon passes to `core.llm.complete(purpose=...)` is declared in `llm_jobs` with its default profile (`fast` / `balanced` / `deep`, or `LlmJob("fast", "Label")`). `MODEL_<PURPOSE>` in env overrides it; an undeclared purpose raises.

## Tools

```python
@addon.tool(is_write=True)
def my_add(conn: Connection, title: str) -> str:
    """Add a thing.

    Args:
        title: What to add.
    """                                               # type hints + docstring → the schema
```

`conn` is the user's connection (`conn.user`, `conn.settings`, `conn.credentials`), left out of the schema. A write runs at once and registers an undo (`core/undo.py`); `confirm=True` asks first, and `describe=` gives the Approve card's words:

```python
def describe_delete(user: User, a: dict) -> Approval:
    return Approval(title=f"Delete “{a['title']}”", heading="Delete", emoji="🗑", question="Delete it?")

@addon.tool(is_write=True, confirm=True, describe=describe_delete)
```

If `describe` raises, the framework logs it and falls back to the default Approve card. Other flags: `acts_outside`, `untrusted_result` (`docs/agent-actions.md`).

## Background work

```python
@addon.job(every=minutes(5))            # or daily="07:30", or at_start=True; delay= seconds; timeout=600.0
async def refresh(user: User) -> int:   # once per user who has the addon active, as that user
    return store.refresh(user)
```

One user's failure (or exceeding `timeout`) is logged and the next user still runs, and the whole pass is guarded so a transient error never stops the job. `every=` and `daily=` persist their last run in `system_marks`, so a restart waits out the remainder of an interval and never repeats a daily pass on the same day. Each pass writes a heartbeat (`scheduler.last_run("my_addon", "refresh")`). Tests run a pass with `await scheduler.run_once(registry.job("my_addon.refresh"))`.

```python
@addon.service
async def watch(app) -> None:           # once per server; started again with backoff when it ends
    await my_websocket_loop()
```

## Events

```python
events.emit("my_addon.thing.created", user, thing_id=tid)     # core.events; never raises

@addon.on("tasks.task.created")
async def on_task(user: User, data: dict) -> None:            # in the background, as that user
    ...
```

Handlers run only for users who have the handler's addon. Names are `<addon>.<thing>.<what happened>`; core's are `core.*` (`core.user.woke`). List the events you emit and their data in the module that emits them (tasks: `addons/tasks/service.py`). Tests wait with `await events.drain()`.

## Notifications

```python
await notify.send(user, notify.Message(text="Due: call Noa", kind="reminder", data={"task_id": tid}))

@addon.channel("push")
async def push(user: User, message: notify.Message) -> bool:  # True when delivered
    return message.kind in KINDS and await send_push(user, message.kind, message.data)
```

Telegram gets `text`; every channel of an addon the user has gets the whole message.

## The Now screen

```python
@addon.now_cards
def cards(user: User, ctx: NowContext) -> list[Card]:          # ctx.now, ctx.hidden, ctx.events
    return [Card(id=f"my:{r['id']}", kind="my", source="My addon", title=r["title"],
                 primary=core_cards.action("done", "Done")) for r in store.open_items(user)]

@addon.day_items
def day(user: User, ctx: NowContext) -> list[DayItem]:
    return [DayItem(title=r["title"], kind="my", at=r["at"]) for r in store.today(user)]

@addon.now_action("my")                                         # cards whose id is "my:<ref>"
async def act(user, ref: str, action: str, args: dict, now) -> dict:
    store.done(user, ref); return core_cards.result("Done")    # core_cards.ActError(msg, 409) refuses
```

`core_cards` is `from core import cards as core_cards`: the helpers (`action`, `result`, `hide_with_undo`, `until_from`, `ActError`).

## Briefing and weekly review

```python
@addon.briefing("my", counts=("my_due",))
def morning(user: User, ctx: BriefingContext) -> Section | None:
    return Section(data=summary, points=["2 things due"], counts={"my_due": 2})

@addon.review("my")
def weekly(user: User, ctx: BriefingContext) -> Section | None:
    return Section(data=summary, items=[{"ref": "1", "type": "my_item", "title": "…", "why": "…", "facts": {}}])

@addon.review_action("my_item", ("complete", "keep"))
async def apply(user, item: dict, action: str, decision: dict, today) -> str:
    return "Completed"
```

Section keys and briefing `counts` keys are unique across addons (a second addon declaring the same key fails to load; core keys like `events` and `date` are reserved).

## Search, self-checks, context chats

```python
from core.addons import helpers

@addon.search
def find(user: User, query: str, limit: int) -> list[Hit]:
    return [Hit("my", r["id"], r["title"], score=helpers.match_score(r["title"], query)) for r in rows]

@addon.selfcheck("my_sync", "My sync", after="calendar")      # server=True: a check of the server
def check(user: User | None, now) -> Check:
    return Check(ok=fresh, detail="last 5 min ago", title="My sync stopped", why="…")

@addon.context("my")                                             # a chat opened from one of its items
def context(user: User, ref_id: str, req: ContextRequest) -> str | None:
    return f"The user is asking about {store.get(user, ref_id)['title']}."

@addon.context_card("my")
def card(user: User, ref_id: str) -> dict:                       # LookupError: not theirs
    return {"kind": "my", "id": ref_id, "title": store.get(user, ref_id)["title"]}
```

## Helpers

`core.addons.helpers` gives plain functions addons share without importing core composers:
- Times: `parse_time`, `ago`, `when_phrase`, `fmt_date`, `age_days`, `next_weekday`, `valid_future_date`.
- Search: `like_escape`, `like_pattern`, `search_terms`, `match_score`.

## Data

```python
# migrations/m0001_baseline.py
VERSION, NAME = 1, "baseline"
def up(conn): conn.execute("CREATE TABLE my_items (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL)")
```

Numbered 1, 2, 3… per addon, found by file name, applied at start in `needs` order (after a snapshot) and recorded in `schema_migrations (addon, version, name, applied_at)`. Non-official addons' migrations run in their own transaction and a failure unloads only that addon. `up` runs inside the runner's transaction: never commit. A change is the next file; never edit a shipped one. An addon touches only its own tables (it may reference core's, or another addon's only when `needs` declares it) and every query filters by the user. Never `CREATE TABLE` outside a migration.

## Routes

```python
def setup(app: web.Application) -> None:      # api.py; behind the same auth as every route
    app.router.add_get("/api/my", handler)    # handler: store.require(request_user(request), "my_addon")
```

`store.require` raises `NotConnected` (403 `not_connected` from the middleware) for a user without the addon. While an admin has disabled the addon on the server its routes answer 404 `addon_disabled` before your handler runs.

## A web page

```python
web=Web(panel="weather.js", style="weather.css", nav=Nav("Weather", icon="cloud-sun", order=60))
```

The addon's `web/` folder is served at `/addons/<name>/web/<file>` (signed in, only while the addon is enabled on the server), and `GET /api/addons` gives each user, for the addons on for them, `web: {panel, style, nav}` with those URLs. The web app's shell knows no addon by name: it builds its navigation from that list (a button per `Nav`, in `order`; Lucide icon names), loads each page's script when the app starts, the style the first time the page is opened, and mounts the page in its own `<section id="panel-<id>">`. `/<id>` and `#<id>…` open it when the app loads (an addon that wants a path of its own adds a route serving the app: shopping's `GET /shopping` in its `api.py`).

The script registers the page with the SDK in `static/dana.js`:

```js
// web/weather.js
(() => {
  "use strict";
  let A = null;
  Dana.panel({
    id: "weather", title: "Weather", icon: "cloud-sun",
    scripts: ["weather-core.js"],          // optional: files beside it, loaded first, in order
    start(api) { A = api; },               // optional: once, at registration (a badge: api.badge(n))
    async mount(el, api, arg) {            // each time it is opened; el is empty the first time
      if (!el.firstChild) el.innerHTML = `<header class="top-bar"><span class="top-bar-title">Weather</span></header><div data-now></div>`;
      const now = await api.call("/api/weather");
      el.querySelector("[data-now]").textContent = now.summary;
    },
    unmount() {},                          // optional: when the user leaves it
  });
})();
```

`api` (the same object for `start` and every `mount`):

| | |
|---|---|
| `api.me` | who is signed in (`GET /api/me`: id, name, access, capabilities, addons, languages, currency) |
| `api.json(method, path, body)` | a JSON request that never throws: `{status, body}` (status 0: no answer) |
| `api.call(path, {method, body})` | a JSON request that throws `Error` (the server's `message` / `error`, `.status`, `.body`) unless 2xx |
| `api.fetch(path, init)` | `fetch`, signed in like the page, never from a cache (uploads) |
| `api.stream(path, {event: fn(data, ev)})` | Server-Sent Events; returns `{close()}` |
| `api.open(id, arg)` | another screen: a core one (`chat`, `tasks`, `calls` with a call id, `settings`, `addons`, `review`, `usage`) or another page by its id (`arg` reaches its `mount`) |
| `api.openSession(sid)` | a chat session in Chat |
| `api.badge(value, tone)` | the page's navigation badge: a count (0 / null hides it, above 99 shows "99+") or a short text; tone `"quiet"` for a count that is not an alert |
| `api.toast(text)`, `api.visible()`, `api.icons()`, `api.esc(text)` | a short message; whether the page is on screen; draw `<i data-lucide>` icons; HTML-escape |
| `api.store.get(key, fallback)` / `.set` / `.remove` | `localStorage` that never throws |
| `api.addon`, `api.base` | the addon's name; the URL of its `web/` folder |

A page uses only its own elements and these; nothing else of the shell is its business. It builds on the shell's tokens (`static/styles.css`: `--ink`, `--canvas`, `--radius-card`…, `.top-bar`, `.btn-primary`, `.chip`, `.icon-btn`, `.form-control`) and its screen kit (`static/kit.css`: `.ibx` list and detail panes that become one on a phone, `.ibx-toolbar` with view chips and `.ibx-search`, `.ibx-titles` / `.ibx-kicker`, `.ibx-toast`, `.ibx-modal` with `.ibx-modal-card`, `.ibx-field`, `.ibx-row2`, `.ibx-btns`, `.ibx-empty`). The service worker keeps a page's files with the shell's, one cache per release (a digest of the shell's and every addon's web files), so a deploy or an addon update never leaves an old page. Pure parts go in a separate file (`web/<x>-core.js` that sets `globalThis.<Name>`) and get a `tests/test_web_<x>.mjs` run by `bin/test` (the official pages: shopping, knowledge, money). The shell's own part is tested by `tests/test_web_panels.mjs` and `tests/test_web_shell.py`.

## Workers

`worker=Worker(port=8091, url_env="MY_ADDON_WORKER_URL")` declares a sidecar built from the addon's `worker/` folder (its own `Dockerfile`, self-contained: the app image leaves it out); the addon reaches it at the URL in `url_env`. Deploying and running workers: `operations.md`, "Workers"; money's bank worker (`addons/money/worker/`) is the example.

## Rules that save time

- Use `core.addons.helpers` for shared time and search helpers instead of importing core composers (which may pull in other modules at load time). Never import another addon to react to it: use its events.
- Composer hooks are plain functions (run in a worker thread); jobs, services, handlers, channels and the action hooks are `async`.
- Background work takes a `User`; user-facing times come from `core.clock.now()`.

## Testing

`bin/test` runs `tests/test_*.py` of every official and site addon. A script starts with `import testenv; testenv.isolate("<name>")`, which sets `DANA_ADDON_DIRS=""`: a site addon's test then sets `DANA_ADDON_DIRS` to its parent folder before importing the app (`examples/addons/hello/tests/test_hello.py`). Cover two users with the addon and one without.

Connect it for a user on the Addons page, with `bin/dana addons connect <user> <name>`, or `PUT /api/addons/<name>`.
