# Addons

Dana is a **small core plus addons**. Everything a user can turn on is an addon: tasks, shopping, Home Assistant, WhatsApp, Gmail, money, the bank scrapers. The addons that ship with Dana are written exactly like the ones you write or install. There is no second, more powerful way reserved for built-in code.

```
server/
  core/                 the runtime: agent loop, users and auth, sessions and runs, the database,
                        LLM calls, notify, events, the scheduler, the web shell, Telegram, voice
  core/addons/          the addon framework (what this document describes)
  addons/<name>/        the official addons, one folder each
examples/addons/hello/  the smallest complete addon, used by the tests
examples/addons/showcase/ every capability once, tested in the suite; the reference's examples
server/cli/template/    what `bin/dana addons new` writes; server/cli/kit/: check, test, dev, publish, reference
<data dir>/addons/      every other addon, installed from the catalog, a git repository or a folder
                        (the web Addons page, `bin/dana addons install`)
github.com/ronganam/dana-catalog  the addon catalog (catalog.json): community addons and where to get them
```

## What an addon is

A folder. Only `addon.py` is required:

```
addons/weather/
  addon.py          the manifest: `addon = Addon("weather", ...)` plus the hooks below
  tools.py          tools Dana can call            (imported by the loader when present)
  demo.py           `@addon.demo`: its sample data for the demo household (imported when present)
  api.py            HTTP routes: `setup(app)`      (registered automatically when present)
  migrations/       m0001_<name>.py, m0002_…        (its own tables, numbered per addon)
  web/              weather.js, weather.css        (its pages in the web app, when declared)
  worker/           Dockerfile + code              (a sidecar process, when declared and built from here)
  tests/            test_*.py, test_*.mjs          (found by bin/test)
  README.md         what it does, its settings, the events it emits (shown in the catalog)
  requirements.txt  extra Python packages, if any (an official addon's are installed into the image; an installed
                    addon can use only what the image has: a missing one fails it at load, not the server)
```

`bin/dana addons new weather` writes a working one with a test in `./weather` (`--web` adds a page, `--worker` a sidecar).

## The manifest

```python
# addons/weather/addon.py
from core.addons import Addon, Field, Nav, Need, Page, PromptPart, Touches

addon = Addon(
    "weather",
    title="Weather",
    description="Current weather and the forecast for your places.",
    version="1.0.0",
    needs=(Need("account", "an OpenWeather account", "Its forecasts"),          # what running it takes
           Need("addon", "phone", "Where you are", optional=True)),
    touches=Touches(hosts=("api.openweathermap.org",), stores_credentials=True), # what it may touch
    icon="cloud-sun",                           # the Addons page: a Lucide icon, what it adds, the steps, what to try
    features=("Today's weather and the week ahead",),
    setup=("Make a free account at openweathermap.org.",),
    examples=("Will it rain tomorrow?",),
    credentials=(Field("api_key", "OpenWeather API key", label="API key",
                       help="On openweathermap.org: your name → My API keys.",
                       url="https://home.openweathermap.org/api_keys"),),
    settings=(Field("units", "metric or imperial", default="metric", label="Units", placeholder="metric"),),
    default_on=False,                           # new users get it turned on (needs no credentials)
    everyone=False,                             # every user has it, cannot turn it off (essentials)
    requires=None,                              # an access capability needed to turn it on ("ops")
    prompt_parts=(PromptPart("guides", "When the user asks about weather, use weather_now."),),
    llm_jobs={"weather_summary": "fast"},       # its LLM purposes and their default model profile
    web=(Page("weather", panel="weather.js", style="weather.css", nav=Nav("Weather", icon="cloud-sun", order=60)),),
)                                               # web=: any number of pages; worker=Worker(...): a sidecar
```

Everything an addon adds is declared on that one object, as a field or as `@addon.<hook>`:

| Hook | Signature | What the framework does |
|---|---|---|
| `@addon.tool(...)` | `def f(conn, ...)` or `async def` | Offers it to users who have the addon; schema from type hints and the docstring. Flags: `is_write`, `confirm`, `acts_outside`, `untrusted_result`, `needs`, `available`, `describe=` (`def d(user, args) -> Approval`: the Approve card's words for a call; raising falls back to the default card), `shortcut=` / `shortcut_prep=` / `shortcut_describe=` (learned phrases). |
| `@addon.job(every=minutes(5))` / `(daily="10:00")` / `(at_start=True)` | `async def f(user)` | Runs it for every user who has the addon active, as that user (`users.acting_as`), with a per-user `timeout=` (default 600 s); one failing user never stops the next and the whole pass is guarded; `every=` and `daily=` last runs are persisted in `system_marks` across restarts; a heartbeat feeds the self-check. |
| `@addon.service` | `async def f(app)` | A long-running task started once with the server (watchers, websockets); restarted with backoff when it dies. |
| `@addon.on("event.name")` | `async def f(user, data)` | Called when any addon or core emits that event for a user (`core.events.emit`, safe from worker threads; pending handlers cancelled on shutdown). The way addons react to each other without importing each other. |
| `@addon.channel("push")` | `async def f(user, message) -> bool` | A delivery channel for `core.notify.send(user, Message(text, kind, data))` (Telegram is core's; the phone addon adds push; all channels deliver concurrently). |
| `@addon.now_cards(order=...)` | `def f(user, ctx: NowContext) -> list[Card]` | Cards on the Now screen, ordered by `order` then addon order. |
| `@addon.day_items` | `def f(user, ctx: NowContext) -> list[DayItem]` | Items in the Now screen's day strip. |
| `@addon.now_action("prefix")` | `async def f(user, ref, action, args, now) -> dict` | Acting on its cards whose id is `prefix:ref` (`POST /api/now/act`); `core.cards.result(...)`, `ActError` to refuse. Connection enforced by `hook_for`. |
| `@addon.briefing("key", counts=..., default=..., rank=...)` | `def f(user, ctx: BriefingContext) -> Section \| None` or `async def` | A section of the morning briefing stored under unique `key` (reserved keys refused); `counts` names the push count keys it owns (unique across addons). |
| `@addon.review("key")` | `def f(user, ctx: BriefingContext) -> Section \| None` | A section of the weekly review stored under unique `key`: `items` are decisions, `data` joins the review's facts. |
| `@addon.review_action("type", actions, primary=True)` | `async def f(user, item, action, decision, today) -> str` | Applying a weekly-review item of that type. Connection enforced by `hook_for`. |
| `@addon.search` | `def f(user, query, limit) -> list[Hit]` | Results in the global search (`core.addons.helpers` provides time and search helpers). |
| `@addon.selfcheck("key", "Label", server=False, after="")` | `def f(user, now) -> Check` | A line in the self-checks (`/api/checks`, Settings → Needs attention); a failing one is a Now card. `server=True`: a check of the server itself (`user` is None), shown to users who may see everyone. `Check(fix=Fix…)` gives it the button that fixes it: `Fix.connect(addon)`, `Fix.screen(id, label)`, `Fix.link(url, label)` (client) or `Fix.run(action, label)` (server: below). |
| `@addon.fix("key")` | `async def f(user, action) -> str` | Carries out the server fix (`Fix.run(action, …)`) of its failing check `key` for the user who pressed it, as them (`POST /api/checks/{key}/fix`, a Now card's `fix`); returns the result's words, `ValueError` to refuse. Called only while that check fails for that user and offers that action. |
| `@addon.context("kind")` / `@addon.context_card("kind")` / `@addon.context_block("kind")` | `def f(user, ref_id, req: ContextRequest) -> str \| None` / `def f(user, ref_id) -> dict` / `def f(user, ref_id) -> str` | Text about an item (a call, a task, a person) for context chats; the card shown with the chat (raise `LookupError` when it is not theirs); a plain description of the item for another addon's context (`req.block(kind, ref_id)`). Connection enforced by `hook_for`. |
| `@addon.demo` | `def f(user, ctx: DemoContext) -> int` | Its sample data for a user of the demo household (`addons/<name>/demo.py`; rows that go with the user only, nothing for the model or a job to pick up): `server/docs/household.md`. |
| `@addon.provide("key", rank=50, source="", label="")` | any arguments the caller and the provider agree on, plain or `async` | A named capability a core composer or another addon calls without importing the addon (weather, today's events, Telegram reply interceptors). Several addons may provide one key: `registry.providers(key, user)` lists them ranked (lower `rank` first), `registry.provider` gives the first (or None), `registry.answer(key, user, ...)` asks each in turn and takes the first that answers. `source` / `label` name it as a source of a signal (`signal.<name>` keys, `server/docs/signals.md`). |
| `@addon.widget("key", title=, script=, sizes=, settings=, description=, icon=, refresh=)` | `def f(user, ctx: WidgetContext) -> dict`, plain or `async` | A widget for tablets' and TVs' dashboards: its data for a user (`ctx`: the placed widget's `settings`, `size`, `now`), drawn by `script` in `web/` registering with `Dana.widget(key, {mount, update, unmount})` (`server/docs/dashboard.md`; writing one: `server/docs/writing-an-addon.md`, "Dashboard widgets"). |
| `@addon.live_refresh(every=60)` | `async def f(user, current: live.Current) -> live.Activity \| live.END \| None` | Keeps the addon's running live activities current (`core.live`, `server/docs/live.md`): called per activity, as its user, when due (at its `refresh_at`, else `every` seconds after the last call) by core's one loop; an `Activity` updates it (no-op updates dropped), `live.END` ends it. A failure is logged, never ends it. One per addon. |
| `@addon.live_action` | `async def f(user, current: live.Current, action, choice) -> live.Activity \| live.END \| None` | A person acted on one of its live activities (`/api/live/{id}/act`, `/select`, `DELETE`): a `server` action's id, `"select"` after the new selection is stored, `"stop"` after Dana ended it (clean up; answer ignored). One per addon. |
| `test=` | `async def f(conn) -> str \| None` | "Test connection" (`POST /api/addons/<name>/test`): None when the stored connection works (which also clears a pending reconnect), else a short reason. |
| `reconnect_fix=` | a `Fix` (not `Fix.run`) | Where its Reconnect card and check send the user (default `Fix.connect(<its name>)`: its own connect flow). The addon records a refusal with `store.needs_reconnect(user, name, reason, part="", label="")` and clears it with `store.reconnected(user, name, part)` (new credentials clear it by themselves): `server/docs/now.md`, "Reconnect". |
| `options=` | `async def f(conn) -> {setting: [{value, label}]}` | Values a client offers for its settings, read from the user's own service (`GET /api/addons/<name>/options`). |
| `validate=` | `def f(user, given, credentials, settings, by) -> None` | Its own rule over a connect / update (raise `InvalidValue(field, message)` to refuse; `by` is who makes the change, None for the server). |
| `blocked=` | `def f(conn) -> str \| None` | Why its tools cannot run for this connection now; the reason becomes the tool's result. |
| `on_connect=` / `on_change=` / `on_credentials_change=` | `def f(user)` | After it is turned on for a user / after any connect, update or disconnect / after its stored credentials changed (a failure is logged, the change stands). |
| `prompt_section=` | `def f(user) -> str` | Its section of the system prompt, asked every turn while the user has it (cheap, cached; "" for nothing). |
| `connect_url=`, `connect_label=` | a path of this server, its button's words | Connecting by a sign-in at the service instead of typed credentials (the Google addons). |
| `change_requires=`, `realtime=`, `modules=`, `order=` | | Who may change anyone's connection (a capability); False keeps its tools out of realtime voice; more modules the loader imports; its place among the official addons. The `Addon` docstring (`core/addons/addon.py`) has each field. |
| `icon=`, `features=`, `setup=`, `examples=`, `disconnect_warning=`; `Field(label=, help=, url=, placeholder=)` | words (checked at load: `url` https only, short lines) | The Addons page's onboarding: the list, "What it adds", the numbered setup steps, "Try asking Dana" (`server/docs/writing-an-addon.md`, "Make it easy to set up"). |

Composer hooks and `demo` are plain functions (the composers call them in a worker thread; `briefing`, `provide` and `widget` may be either); jobs, services, handlers, channels and the action hooks are `async` (a wrong kind fails at import). Page ids, keyed hooks (`now_action`, `briefing`, `review`, `review_action`, `selfcheck`, `fix`, `context`, `context_card`, `context_block`, `widget`) and briefing `counts` keys are unique across addons (a provider key is not: several addons may provide it, ranked) (a second addon declaring the same key fails to load). `hook_for(kind, key, user)` enforces that the owning addon is active for `user` (connected, enabled on the server, and the addons it depends on connected too). The types (`Card`, `DayItem`, `NowContext`, `BriefingContext`, `DemoContext`, `Section`, `Hit`, `Check`, `Fix`, `ContextRequest`, `Approval`, `WidgetContext`) are in `core/addons/hooks.py`; shared time/search helpers live in `core/addons/helpers.py`.

Core composers (Now, briefing, weekly review, wake, search, self-checks, context chats, approval text, shortcuts, Telegram) **only** collect these hooks; none of them imports an addon (enforced by `tests/test_addon_hooks.py::test_core_imports_no_addons`). `core/server.py` imports no addon either: routes, jobs and services come from the registry.

### Needs and touches

What an addon needs and what it may touch are declared on the manifest, so a stranger sees both before turning anything on: the Addons page shows them for each addon ("Needs", "May touch"), `GET /api/addons` carries them, and `bin/dana addons table` prints the official addons' needs as the Markdown table in the README ("What each feature needs"; `server/tests/test_addon_needs.py` fails when the README's copy differs).

- `needs=(Need(kind, what, why, optional=False), …)`, `kind` one of `env` (`what` is the env variable: `GOOGLE_CLIENT_ID`), `account` (an account at a service), `hardware` (a device, or a server you run: Home Assistant, WAHA, Frigate), `app` (the Dana Android app), `region` (where it works at all: the table's Region column) and `addon` (`what` is the other addon's name). `what` reads inside a sentence ("a Google account"), `why` is one line. A required `addon` need is what the addon **depends** on (`Addon.depends`): loaded and migrated first, disabled with it, and on for a user before the addon is active for them; an optional one is only shown. A need of an unknown kind, an env name that is not upper case, a need named twice or an addon needing itself fails the load like any manifest error.
- `touches=Touches(hosts=(), acts_outside=False, stores_credentials=False, reads=())`: the hosts it contacts (a domain, or words for an address the user gives: "your Home Assistant server"), whether it acts for the user outside Dana (sends messages or mail, changes things at a service or on devices), whether it keeps passwords, tokens or sign-ins (an addon with `credentials` must say so, or it fails to load), and the other addons whose data it reads. Not declaring it means touching nothing beyond Dana's own data. It is disclosure, not a sandbox: every addon runs with the server's full access (`SECURITY.md`, "Threat model").

Installing from the catalog shows the entry's touches before Install, and the server checks them against the fetched code: installing or updating an addon whose `touches=` declares something the admin has not agreed to (the version it replaces, plus what the page showed) asks again; an update that adds nothing goes ahead at once ("The catalog" below).

### Prompt slots

`PromptPart(slot, text, rank)` slots are a fixed list in `core/agent.py` (`SLOTS`), each with its own joiner (a phrase in a list joins with ", ", a sentence with " ", a paragraph with a blank line). An unknown slot fails the load.

### Data

An addon owns its tables. `migrations/mNNNN_<name>.py` (`VERSION`, `NAME`, `up(conn)`, numbered 1, 2, 3… per addon) run at start via `loader.migrate()` in dependency order (required addon needs first) — core's first, then each loaded addon's, after a snapshot of an existing database — and are recorded in `schema_migrations (addon, version, name, applied_at)`. Non-official addons' migrations run each in its own transaction, are skipped if the addon failed to load, and a failing migration unloads only that addon instead of stopping the server. Core's own tables are the addon name `core` (`server/core/db/migrations/`). An addon never references another addon's table with a foreign key unless it depends on that addon. Removing an addon keeps its tables and its rows in `schema_migrations`. The migrations Dana 9.0 numbered in one sequence are squashed into `m0001_baseline` of core and each official addon; a 9.0 database (with every migration 1..33 present, plus optional 34) is converted in place on its first start (`core/db/convert.py`), an older or incomplete one is refused ("upgrade with Dana 9.0.0 first"). Rolling back after conversion means restoring the pre-conversion snapshot.

Per-user data: every query filters by the acting user. Addons can use `core.addons.data.UserTable` for user-scoped CRUD helpers; `server/tests/test_sql_scope.py` lints SQL on per-user tables (any query touching a table with a `user_id` column without `user_id` in the statement fails unless marked `# scoped:` with the reason).

### Events

`core.events.emit(name, user, **data)` (`name` and `user` positional, so data may use those keys); handlers of the addons that user has run in the background as that user, a failing one logged (tests wait with `events.drain()`). Names are `<addon>.<thing>.<what happened>` (`tasks.task.created`, `phone.notification.ingested`); core's own start with `core.` (`core.user.woke`, `core.signal.location`, `core.zones.changed`).

**Taps**: `events.tap(fn)` / `events.untap(fn)`; `async fn(name, user, data)` is called for every `emit`, whether or not any addon handles it, started like a handler (its own task, as the user, a failure logged, `drain()` waits, also when emitted from another thread). The `watches` addon matches any event this way.

**The catalog**: an event others may watch is declared as an `EventType(name, about, fields, history=None, values=None)` (`core/addons/hooks.py`): `about` one sentence for the model that writes watches, `fields` each data key with its description (units, allowed values), `history(user, since)` this user's past events since an aware time, oldest first, in the emitted shape (dry runs; sync, one cheap query, scoped to the user), `values(user)` the known real values of some fields, `{field: ["value (what it is)", …]}`, most relevant first (shown to the watch compiler, capped; a field listed there is a closed set: an `eq` / `in` condition naming another value is refused with the close matches; sync, no network, scoped to the user; `{}` when nothing is known yet). An addon declares its own in `Addon(events=(...))` (named `<addon>.…`, each once); core's are `core.events.CORE_EVENTS`. `core.addons.registry.event_types(user)` lists core's and those of the addons active for the user. Emitting does not need a declaration; declaring makes the event watchable.

| Event | Data | History |
|---|---|---|
| `core.signal.car` | `action` (enter \| leave), `source`, `late` | none |
| `core.signal.location` | `action` (arrive \| leave), `zone` (the place's name), `target` (the same), `source`, `late` | none |
| `core.user.woke` | `why` | the wake log |
| `phone.notification.received` | `notification_id, app, sender, conversation, is_group, title, text, category, priority, amount` (`server/docs/notifications.md`) | the Inbox |
| `phone.email.received` | `notification_id, account, from, subject, text, amount` (`server/docs/notifications.md`) | the Inbox |
| `gmail.email.received` | `message_id, thread_id, from, from_name, from_email, to, cc, subject, snippet, labels, from_me, to_me, amount` (`from_me`: mail the user sent to themself; `server/docs/gmail.md`, "New mail for watches") | the emails announced (30 days) |
| `phone.call.ended` | `call_id, person, direction, duration_s, title, summary` (`server/docs/calls.md`) | analysed calls |
| `money.transaction.added` | `transaction_id, amount, direction, counterparty, counterparty_kind, flow, category, account, account_kind, status` (`server/docs/money.md`) | the transactions |
| `homeassistant.state.changed` | `entity_id, name, domain, old_state, new_state, unit, device_class` (states as text; known values: the user's entity ids; `server/docs/access.md`, "Home Assistant") | the changes Dana emitted, in memory since the server started |
| `tasks.task.created` / `tasks.task.completed` | `task_id, title` (+ `group_id` when created) | created only |

Other events (`phone.notification.ingested`, `tasks.task.deleted`, `knowledge.project.deleted`, `core.zones.changed`) are for handlers only; the module that emits each says its data.

### Web

`web=(Page(id, panel, style, nav), …)` declares the addon's pages, any number (phone has two: `inbox` and `calls`). The addon's `web/` folder is served at `/addons/<name>/web/<file>` (signed in like every page, only for a loaded addon enabled on the server, nothing outside its `web/` folder, no dot-files; `core/addons_api.py`), and `/api/addons` gives each user, for the addons on for them, `web: [{id, panel, style, nav}]` with those URLs (`[]` otherwise). A page id names the screen (`#panel-<id>`, `/<id>` and `#<id>` open it); it is unique across addons and never a core screen's (`now`, `chat`, `settings`, `attention`, `addons`, `devices`, `household`, `usage`, `debug`, `review`: `core/addons/hooks.py` `CORE_SCREENS`). The web shell puts a navigation button for each page with a `Nav(label, icon, order, primary)` (`order` places it among the others; `primary=True` asks for a place in the default navigation bar, which holds Chat and up to three such pages until the user picks their own: `server/docs/web-design.md`, "Navigation"), loads each page's script, which registers itself with the small SDK in `static/dana.js` (`Dana.panel({scripts, start(api), mount(el, api, arg), unmount()})`: the SDK knows which page a script is from the URL it loaded), its style on first open, and mounts it (`static/app-panels.js`; writing one: `server/docs/writing-an-addon.md`, "Pages"). A dashboard widget's script lives in the same `web/` folder and registers with `Dana.widget` (the dashboard page loads it: `server/docs/dashboard.md`). A page can host a **core piece** in an element of its own with `api.piece(name, el)`: `today`, the morning briefing and the weekly-review banner (`static/review.js`), which the tasks addon's page shows above its lists. The shell owns navigation, auth, the theme, the core screens and pieces, and the service worker, which keeps the shell and the pages' files in one cache per release (a digest of `static/` and every loaded addon's `web/`; `core/pages_api.py`); it names no addon page (`tests/test_web_shell.py`).

### Workers (sidecars)

An addon with a sidecar process declares `worker=Worker(port=8090, url_env="EXAMPLE_WORKER_URL", image="ghcr.io/<owner>/<repo>-worker")`; Dana reaches it at the URL in `url_env`. The `image` is **published** by the addon's own repository, built from its `worker/` folder (a self-contained `Dockerfile` context) and tagged with each commit (and `latest`). Nothing builds it on deploy: `bin/dana addons workers --json` lists each installed addon's worker image pinned to the tag the manifest names, else to the commit the addon was installed at (`.installed.json`; `core/addon_dirs.py` `worker_image`; an addon installed from a folder records no commit, so its manifest names a tag), and `bin/deploy` writes those into your GitOps values or compose `.env`. `money_il` (Israeli banks, `github.com/ronganam/dana-addon-money-il`) works this way. A worker without an `image` is built where it runs (`bin/dana addons dev`, your own compose file).

`compose.yaml` runs a worker under a profile with the addon's name (`--profile money_il`); the app image leaves `worker/` folders out. Details: `server/docs/operations.md`, "Workers".

### LLM jobs

`llm_jobs={"purpose": "profile"}` registers purposes for `core.llm.complete(purpose=...)`; the model is `MODEL_<PURPOSE>` from env, else the profile's model (`fast` / `balanced` / `deep`), and usage is filed under the purpose. `LlmJob(profile, label, background=True)` marks a purpose as background work, paused when the monthly budget is spent (`server/docs/usage.md`).

## Where addons come from, and turning them on

There is one way to extend Dana: **install an addon**. The official addons ship with it; every other addon, yours included, is installed into the data dir.

| Source | Folder | Who adds it | Trust |
|---|---|---|---|
| Official | `server/addons/` | ships with Dana | reviewed in this repository |
| Installed: catalog | `<data dir>/addons/` | an admin, from the catalog | pinned to a commit the catalog checked; runs with the server's full access, shown before installing |
| Installed: git | `<data dir>/addons/` | an admin, from any git repository + full commit (+ folder): a public or private repository | pinned to a commit; full access, shown before installing |
| Installed: folder | `<data dir>/addons/` | an admin, uploading a folder (`bin/dana addons install ./NAME --server URL`) | what was uploaded; full access, shown before installing |

The data dir is the data volume, so installed addons survive image updates; the published image is the same for every deployment. `DANA_ADDON_DIRS`, a development and test knob, replaces the data dir's `addons/` with the folders it names (`""` = official only: the tests; the add-on kit's harness and `bin/dana addons dev` point it at the addon being written); their addons count as installed. Load order: an addon after every addon it depends on; among those free to go, official addons first by `order=` then name, then the others by name; that order is the order of every tool list and hook result. Two addons with one name fail the start. An installed addon that fails to import (any `BaseException` except `KeyboardInterrupt`), declares a name that disagrees with its folder, needs a missing/failed addon, names an unknown prompt slot, clashes on a keyed hook or briefing count, fails its migration, or raises in `api.setup(app)` is unloaded with its dependents and recorded as failed with the error (`loader.failed()`; listed by `/api/addons`, on the Addons page and by `bin/dana addons list`, the error for admins only); an official one failing stops the start (it is a bug). Non-official routes cannot shadow core paths such as `/static/`.

Three levels of "on":
1. **Loaded**: found in a folder above.
2. **Enabled on this server**: an admin can disable a loaded addon for everyone (Addons page, `POST /api/addons/<name>/disable`, `bin/dana addons disable <name>`; the `addon_state` table, `core/addons/state.py`). A disabled addon offers nothing: no tools, prompt parts, hooks, jobs, services (a running one is cancelled within seconds) or notify channels; its routes and `web/` files answer 404 `addon_disabled`; nobody can connect or change it (its connections and data are kept). Addons that depend on it are disabled with it. The running server sees a change, from the page or the CLI, on its next lookup (`core.db.ChangeWatch`); no restart. `everyone` addons cannot be disabled.
3. **On for a user**: the user turned it on (with credentials, if it has any), or it is `default_on` / `everyone`.

## Installing an addon

An admin installs from the Addons page (the Catalog, or a git repository + commit), with `bin/dana addons install` against the running server, or with the API: `POST /api/addons/install` (`core/addons_api.py`) takes `{name}` (a catalog entry), `{repo, ref, path}` or, as `multipart/form-data`, an `archive` part (a `.tar.gz` of the addon folder, its files at the root or in one top folder) with an optional `accept` part. Every way ends in the same checks and record (`core/addons/install.py`): an `addon.py` declaring the addon's name (read, not imported); the name not official, reserved or in another source; no symbolic links; for an archive also no absolute paths or `..`, only plain files and folders, at most 20 MB packed, 2000 files and 50 MB unpacked (413 `too_large` over the packed size). It reads the `touches=` (plain values only, else refused) and installs only when everything declared was agreed to: what the version it replaces declared plus the request's `accept` (what the admin was shown); otherwise nothing is written and the answer is 409 `confirm_touches` with all its touches and the new ones, so the admin is asked again; an update whose touches grew asks again, one with the same touches updates without a question. The folder lands in `<data dir>/addons/<name>` with a `.installed.json` record: `source` (`{"kind": "catalog" | "git", repo, path, commit}` or `{"kind": "upload"}`), `installed_at`, `installed_by`. An upload replaces an earlier upload of that name, a git install an earlier git or catalog install; an upload never replaces a git install, nor the other way round (the refusal says to remove it first). A version older than the one that already ran here is refused before anything is written: one whose `migrations/` lack a migration the database applied for that addon (installing it would stop the addon from loading; the catalog's entry may lag behind a newer commit installed by hand). Nothing runs until the restart (`POST /api/server/restart`: the process exits and its supervisor starts it again; `server/docs/operations.md`, "Restarting"). Removing deletes the folder (tables stay).

`bin/dana addons install <name | repo@commit[:path] | ./folder>`:

- `--server URL` (or `DANA_URL`): on that running server, through the API, with an admin's token from `DANA_TOKEN` (else asked, hidden). A folder is packed first, leaving out `.git/`, `__pycache__/`, `.venv/`, `node_modules/`, `*.pyc` and what its `.gitignore` names (`cli/remote.py`), then uploaded. A 409 `confirm_touches` prints what the addon may touch and asks (`--yes` agrees); success says to restart, and `--restart` calls `POST /api/server/restart`.
- Without a server: into this machine's data dir `addons/` (`--dir` for any folder), e.g. inside the container: `docker compose exec dana dana addons install <name>`, then `docker compose restart dana`.

`bin/dana addons list` shows each installed addon's source (catalog / git at a commit / upload and when), `bin/dana addons workers --json` the installed addons' published worker images, pinned (what `bin/deploy` writes). `bin/dana addons new <name> [--web] [--worker]` writes a working addon in `./<name>`, its own git repository, to start from, and `check`, `test`, `dev`, `publish` and `reference` are the add-on kit (`server/docs/writing-an-addon.md`, "The add-on kit"). The other commands: `table` (the README's "What each feature needs"), `remove`, `disable`, `enable`, and the per-user `show` / `connect` / `update` / `disconnect`.

## The catalog (marketplace)

`catalog.json` in the [dana-catalog](https://github.com/ronganam/dana-catalog) repository lists addons: `name`, `title`, `description`, `icon` (optional, a Lucide name), `author`, `homepage`, `tags`, `min_dana`, `touches` (optional: what that version declares it may touch, `{hosts, acts_outside, stores_credentials, reads}`), and `source` = `{repo, path, ref}` (a git repository, a folder in it, a full commit hash). That repository's README says how an entry is added, and its workflow checks every pull request: the add-on at the pinned commit passes `bin/dana addons check` and its own tests against a pinned Dana, and its touches match the entry. Servers read it from `DANA_CATALOG_FILE` (a local file, for tests) or `DANA_CATALOG_URL` (default: that file's raw URL on the repository's `main`) with a 5-second timeout, and keep it 10 minutes (`core/addons/catalog.py`); when the URL cannot be read they show the copy read from it earlier in this process with a notice above the list, and with no earlier copy the Catalog says it cannot be read; an entry that is not valid (a missing field, a ref that is not a full commit, a repeated name) is skipped, its reason logged and shown under the list. `GET /api/addons/catalog` (admins) merges it with what is here: official, already here from another source or uploaded, installed (at which commit, loaded or waiting for a restart, what that version touches), update available and the touches the update adds, compatible with this Dana.

The Addons page (`static/addons.js`) is an onboarding flow built only from the manifests: the addons grouped On / Available / Not available (an admin's choice, off on the server, failed to load: admins see the error) and, for admins, the Catalog and under it **Build your own add-on**: the one-line prompt for a coding agent (Copy) and a link to the guide it follows, [`website/build-an-addon.md`](../website/build-an-addon.md) (`https://ronganam.github.io/dana/build-an-addon.md`); opening one shows what it adds, what it needs and what it may touch (the manifest's `needs` and `touches`), and numbered setup steps (the manifest's `setup`, the sign-in or the fields with their help, Connect, which runs `test=` and says in plain words whether it works), then "Try asking Dana". A user with no optional addon on gets a one-time welcome over the app suggesting addons to set up (`GET /api/addons` → `welcome`, `POST /api/addons/welcome`; dismissed is kept per user in `now_hidden`, and the page can bring it back). An admin installs from the catalog, or any git repository + full commit (+ folder), after a confirmation showing the repository, the commit, what the entry says it may touch and that **an installed addon runs with the server's full access**: the server fetches exactly that commit with git (`git init` + `git fetch --depth 1 <repo> <commit>`, no shell) and installs it as above ("Installing an addon"). Branches, tags and short hashes are refused. Updating is installing a newer commit.

## Core, not addons

The agent loop, users / access / auth, sessions, runs and the shared thread, undo and approvals, notify (Telegram channel), events, the scheduler, the database and migrations, LLM calls and usage, the web shell and the Addons page, Telegram, realtime voice, Android app updates, and the composers that collect addon hooks. The `essentials` addon (`everyone=True`) holds the always-on tools: the time, asking to choose, web search, `share_item`.
