# Writing an addon

An addon is a folder with an `addon.py`; everything it adds to Dana is declared on the `addon` object there or with `@addon.<hook>` in its other modules. Official addons and yours are written the same way. The design (and what is not built yet) is [`../../docs/addons.md`](../../docs/addons.md). Start from [`examples/addons/hello/`](../../examples/addons/hello/): a tool, a setting, a prompt line, a table, a job, an event handler, a Now card with an action, a route and its test. The `tasks` addon (`addons/tasks/`) uses every hook.

```bash
cp -r examples/addons/hello site/addons/my_addon   # rename hello → my_addon inside, restart Dana
```

## The folder

```
my_addon/
  addon.py          addon = Addon("my_addon", ...)      the manifest (required)
  tools.py          @addon.tool() functions             imported when present
  hooks.py          jobs, handlers, Now cards…          imported when present
  api.py            setup(app): its HTTP routes         registered automatically
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

**Prompt slots**: `PromptPart(slot, text, rank)`; the slots are the table `SLOTS` in `core/agent.py`, each with its joiner (`tools` lists "a, b, and c", `guides` are paragraphs, `send_via` keeps its leading space). `{user}` becomes the user's name. An unknown slot fails the load.

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
def describe_delete(a: dict) -> Approval:
    return Approval(title=f"Delete “{a['title']}”", heading="Delete", emoji="🗑", question="Delete it?")

@addon.tool(is_write=True, confirm=True, describe=describe_delete)
```

Other flags: `acts_outside`, `untrusted_result` (`docs/agent-actions.md`).

## Background work

```python
@addon.job(every=minutes(5))            # or daily="07:30", or at_start=True; delay= seconds
async def refresh(user: User) -> int:   # once per user who has the addon, as that user
    return store.refresh(user)
```

One user's failure is logged and the next user still runs; each pass writes a heartbeat (`scheduler.last_run("my_addon", "refresh")`). Tests run a pass with `await scheduler.run_once(registry.job("my_addon.refresh"))`.

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
@addon.briefing
def section(user: User, ctx: BriefingContext) -> Section | None:   # ctx.kind: BRIEFING or WEEKLY_REVIEW
    return Section(key="my", data=summary, points=["2 things due"], counts={"due": 2})

@addon.review_action("my_item", ("complete", "keep"))
async def apply(user, item: dict, action: str, decision: dict, today) -> str:
    return "Completed"
```

A weekly-review section lists its decisions in `Section(items=[{"ref", "type", "title", "why", "facts"}])`.

## Search, self-checks, context chats

```python
@addon.search
def find(user: User, query: str, limit: int) -> list[Hit]:
    return [Hit("my", r["id"], r["title"], score=search.match_score(r["title"], query)) for r in rows]

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

## Data

```python
# migrations/m0001_baseline.py
VERSION, NAME = 1, "baseline"
def up(conn): conn.execute("CREATE TABLE my_items (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL)")
```

Numbered 1, 2, 3… per addon, found by file name, applied at start (after a snapshot) and recorded in `schema_migrations (addon, version, name, applied_at)`. `up` runs inside the runner's transaction: never commit. A change is the next file; never edit a shipped one. An addon touches only its own tables (it may reference core's) and every query filters by the user. Never `CREATE TABLE` outside a migration.

## Routes

```python
def setup(app: web.Application) -> None:      # api.py; behind the same auth as every route
    app.router.add_get("/api/my", handler)    # handler: store.require(request_user(request), "my_addon")
```

`store.require` raises `NotConnected` (403 `not_connected` from the middleware) for a user without the addon.

## Rules that save time

- Import core composers as modules (`from core import now as now_screen`) and use their attributes inside functions: a composer may still be importing when your addon loads. Never import another addon to react to it: use its events.
- Composer hooks are plain functions (run in a worker thread); jobs, services, handlers, channels and the action hooks are `async`.
- Background work takes a `User`; user-facing times come from `core.clock.now()`.

## Testing

`bin/test` runs `tests/test_*.py` of every official and site addon. A script starts with `import testenv; testenv.isolate("<name>")`, which sets `DANA_ADDON_DIRS=""`: a site addon's test then sets `DANA_ADDON_DIRS` to its parent folder before importing the app (`examples/addons/hello/tests/test_hello.py`). Cover two users with the addon and one without.

Connect it for a user on the Addons page, with `bin/users.py addons connect <user> <name>`, or `PUT /api/addons/<name>`.
