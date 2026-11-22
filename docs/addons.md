# Addons

Dana is a **small core plus addons**. Everything a user can turn on is an addon: tasks, shopping, Home Assistant, WhatsApp, Gmail, money, the bank scrapers. The addons that ship with Dana are written exactly like the ones you write or install. There is no second, more powerful way reserved for built-in code.

```
server/
  core/                 the runtime: agent loop, users and auth, sessions and runs, the database,
                        LLM calls, notify, events, the scheduler, the web shell, Telegram, voice
  core/addons/          the addon framework (what this document describes)
  addons/<name>/        the official addons, one folder each
examples/addons/hello/  the smallest complete addon, used by the tests and the scaffold
site/addons/<name>/     your deployment's own addons (docs/site.md)
<data dir>/addons/      addons installed from the catalog (the web Addons page or `bin/dana addons install`)
server/catalog.json     the addon catalog: official and community addons and where to get them
```

## What an addon is

A folder. Only `addon.py` is required:

```
addons/weather/
  addon.py          the manifest: `addon = Addon("weather", ...)` plus the hooks below
  tools.py          tools Dana can call            (imported by the loader when present)
  api.py            HTTP routes: `setup(app)`      (registered automatically when present)
  migrations/       m0001_<name>.py, m0002_…        (its own tables, numbered per addon)
  web/              weather.js, weather.css        (its pages in the web app, when declared)
  worker/           Dockerfile + code              (a sidecar process, when declared and built from here)
  tests/            test_*.py, test_*.mjs          (found by bin/test)
  README.md         what it does, its settings, the events it emits (shown in the catalog)
  requirements.txt  extra Python packages, if any (installed into the image; missing = the addon fails to load, not the server)
```

`bin/dana addons new weather` writes a working one with a test (`--web` adds a page, `--worker` a sidecar).

## The manifest

```python
# addons/weather/addon.py
from core.addons import Addon, Field, Nav, Page, PromptPart

addon = Addon(
    "weather",
    title="Weather",
    description="Current weather and the forecast for your places.",
    version="1.0.0",
    needs=(),                                   # other addons it builds on: loaded first, turned on first
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
| `@addon.selfcheck("key", "Label", server=False, after="")` | `def f(user, now) -> Check` | A line in the self-checks (`/api/checks`); a failing one is a Now card. `server=True`: a check of the server itself (`user` is None), shown to users who may see everyone. |
| `@addon.context("kind")` / `@addon.context_card("kind")` / `@addon.context_block("kind")` | `def f(user, ref_id, req: ContextRequest) -> str \| None` / `def f(user, ref_id) -> dict` / `def f(user, ref_id) -> str` | Text about an item (a call, a task, a person) for context chats; the card shown with the chat (raise `LookupError` when it is not theirs); a plain description of the item for another addon's context (`req.block(kind, ref_id)`). Connection enforced by `hook_for`. |
| `@addon.provide("key")` | any arguments the caller and the provider agree on, plain or `async` | A named capability a core composer or another addon calls through `registry.provider(key, user)` (the hook, or None; its `func`) without importing the addon (weather, today's events, home zone, Telegram reply interceptors). |
| `test=` | `async def f(conn) -> str \| None` | "Test connection" (`POST /api/addons/<name>/test`): None when the stored connection works, else a short reason. |
| `options=` | `async def f(conn) -> {setting: [{value, label}]}` | Values a client offers for its settings, read from the user's own service (`GET /api/addons/<name>/options`). |
| `validate=` | `def f(user, given, credentials, settings, by) -> None` | Its own rule over a connect / update (raise `InvalidValue(field, message)` to refuse; `by` is who makes the change, None for the server). |
| `blocked=` | `def f(conn) -> str \| None` | Why its tools cannot run for this connection now; the reason becomes the tool's result. |
| `on_connect=` / `on_change=` / `on_credentials_change=` | `def f(user)` | After it is turned on for a user / after any connect, update or disconnect / after its stored credentials changed (a failure is logged, the change stands). |
| `prompt_section=` | `def f(user) -> str` | Its section of the system prompt, asked every turn while the user has it (cheap, cached; "" for nothing). |
| `connect_url=`, `connect_label=` | a path of this server, its button's words | Connecting by a sign-in at the service instead of typed credentials (the Google addons). |
| `change_requires=`, `realtime=`, `modules=`, `order=` | | Who may change anyone's connection (a capability); False keeps its tools out of realtime voice; more modules the loader imports; its place among the official addons. The `Addon` docstring (`core/addons/addon.py`) has each field. |
| `icon=`, `features=`, `setup=`, `examples=`, `disconnect_warning=`; `Field(label=, help=, url=, placeholder=)` | words (checked at load: `url` https only, short lines) | The Addons page's onboarding: the list, "What it adds", the numbered setup steps, "Try asking Dana" (`server/docs/writing-an-addon.md`, "Make it easy to set up"). |

Composer hooks are plain functions (the composers call them in a worker thread; `briefing` and `provide` may be either); jobs, services, handlers, channels and the action hooks are `async` (a wrong kind fails at import). Page ids, keyed hooks (`now_action`, `briefing`, `review`, `review_action`, `selfcheck`, `context`, `context_card`, `context_block`, `provide`) and briefing `counts` keys are unique across addons (a second addon declaring the same key fails to load). `hook_for(kind, key, user)` enforces that the owning addon is active for `user` (connected, enabled on the server, and its `needs` connected too). The types (`Card`, `DayItem`, `NowContext`, `BriefingContext`, `Section`, `Hit`, `Check`, `ContextRequest`, `Approval`) are in `core/addons/hooks.py`; shared time/search helpers live in `core/addons/helpers.py`.

Core composers (Now, briefing, weekly review, wake, search, self-checks, context chats, approval text, shortcuts, Telegram) **only** collect these hooks; none of them imports an addon (enforced by `tests/test_addon_hooks.py::test_core_imports_no_addons`). `core/server.py` imports no addon either: routes, jobs and services come from the registry.

### Prompt slots

`PromptPart(slot, text, rank)` slots are a fixed list in `core/agent.py` (`SLOTS`), each with its own joiner (a phrase in a list joins with ", ", a sentence with " ", a paragraph with a blank line). An unknown slot fails the load.

### Data

An addon owns its tables. `migrations/mNNNN_<name>.py` (`VERSION`, `NAME`, `up(conn)`, numbered 1, 2, 3… per addon) run at start via `loader.migrate()` in `needs` order — core's first, then each loaded addon's, after a snapshot of an existing database — and are recorded in `schema_migrations (addon, version, name, applied_at)`. Non-official addons' migrations run each in its own transaction, are skipped if the addon failed to load, and a failing migration unloads only that addon instead of stopping the server. Core's own tables are the addon name `core` (`server/core/db/migrations/`). An addon never references another addon's table with a foreign key unless its `needs` declares it. Removing an addon keeps its tables and its rows in `schema_migrations`. The migrations Dana 9.0 numbered in one sequence are squashed into `m0001_baseline` of core and each official addon; a 9.0 database (with every migration 1..33 present, plus optional 34) is converted in place on its first start (`core/db/convert.py`), an older or incomplete one is refused ("upgrade with Dana 9.0.0 first"). Rolling back after conversion means restoring the pre-conversion snapshot.

Per-user data: every query filters by the acting user. Addons can use `core.addons.data.UserTable` for user-scoped CRUD helpers; `server/tests/test_sql_scope.py` lints SQL on per-user tables (any query touching a table with a `user_id` column without `user_id` in the statement fails unless marked `# scoped:` with the reason).

### Events

`core.events.emit(name, user, **data)`; handlers of the addons that user has run in the background as that user, a failing one logged (tests wait with `events.drain()`). Names are `<addon>.<thing>.<what happened>` (`tasks.task.created`, `homeassistant.location.changed`); core's own start with `core.` (`core.user.woke`). Each addon lists the events it emits and their data (its README, or the module that emits them until it has one: `addons/tasks/service.py`).

### Web

`web=(Page(id, panel, style, nav), …)` declares the addon's pages, any number (phone has two: `inbox` and `calls`). The addon's `web/` folder is served at `/addons/<name>/web/<file>` (signed in like every page, only for a loaded addon enabled on the server, nothing outside its `web/` folder, no dot-files; `core/addons_api.py`), and `/api/addons` gives each user, for the addons on for them, `web: [{id, panel, style, nav}]` with those URLs (`[]` otherwise). A page id names the screen (`#panel-<id>`, `/<id>` and `#<id>` open it); it is unique across addons and never a core screen's (`chat`, `settings`, `addons`, `usage`, `debug`, `review`: `core/addons/hooks.py` `CORE_SCREENS`). The web shell puts a navigation button for each page with a `Nav(label, icon, order, primary)` (`order` places it among the others; `primary=True` asks for a place in the default navigation bar, which holds Chat and up to three such pages until the user picks their own: `server/docs/web-design.md`, "Navigation"), loads each page's script, which registers itself with the small SDK in `static/dana.js` (`Dana.panel({scripts, start(api), mount(el, api, arg), unmount()})`: the SDK knows which page a script is from the URL it loaded), its style on first open, and mounts it (`static/app-panels.js`; writing one: `server/docs/writing-an-addon.md`, "Pages"). A page can host a **core piece** in an element of its own with `api.piece(name, el)`: `today`, the morning briefing and the weekly-review banner (`static/review.js`), which the tasks addon's page shows above its lists. The shell owns navigation, auth, the theme, the core screens and pieces, and the service worker, which keeps the shell and the pages' files in one cache per release (a digest of `static/` and every loaded addon's `web/`; `core/pages_api.py`); it names no addon page (`tests/test_web_shell.py`).

### Workers (sidecars)

An addon with a sidecar process declares `worker=Worker(port=8090, url_env="EXAMPLE_WORKER_URL", image="")`; Dana reaches it at the URL in `url_env`. Two ways to get its image:

- **Published** (a catalog addon): `image="ghcr.io/<owner>/<repo>-worker"` names the image its own repository builds and pushes (tagged with each commit, and `latest`). `bin/deploy` builds nothing for it: it pins the image to the tag the manifest names, else to the commit the addon was installed at (`.installed.json`; `core/addon_dirs.py` `worker_image`), and writes that into your GitOps values. `money_il` (Israeli banks, `github.com/ronganam/dana-addon-money-il`) works this way.
- **Built** (a site addon): a `worker/` folder (a self-contained `Dockerfile` context) and no `image`: `bin/deploy` builds `<DANA_IMAGE>-<addon>` with Dana's tag (`DANA_WORKERS` picks them) and writes it to your GitOps values (`workers.<addon>.image`).

`compose.yaml` runs a worker under a profile with the addon's name (`--profile money_il`); the app image leaves `worker/` folders out. Details: `server/docs/operations.md`, "Workers".

### LLM jobs

`llm_jobs={"purpose": "profile"}` registers purposes for `core.llm.complete(purpose=...)`; the model is `MODEL_<PURPOSE>` from env, else the profile's model (`fast` / `balanced` / `deep`), and usage is filed under the purpose.

## Where addons come from, and turning them on

| Source | Folder | Who adds it | Trust |
|---|---|---|---|
| Official | `server/addons/` | ships with Dana | reviewed in this repository |
| Site | `site/addons/` (or `$DANA_SITE/addons`) | you, in your site | yours |
| Installed | `<data dir>/addons/` | an admin, from the catalog or a git URL | pinned to a commit; runs with the server's full access, shown before installing |

`DANA_ADDON_DIRS` replaces the site, image and data folders (`""` = official only: the tests). Load order: an addon after every addon in its `needs`; among those free to go, official addons first by `order=` then name, then the others by name; that order is the order of every tool list and hook result. Two addons with one name fail the start. A site or installed addon that fails to import (any `BaseException` except `KeyboardInterrupt`), declares a name that disagrees with its folder, needs a missing/failed addon, names an unknown prompt slot, clashes on a keyed hook or briefing count, fails its migration, or raises in `api.setup(app)` is unloaded with its dependents and recorded as failed with the error (`loader.failed()`; listed by `/api/addons`, on the Addons page and by `bin/dana addons list`, the error for admins only); an official one failing stops the start (it is a bug). Non-official routes cannot shadow core paths such as `/static/`.

Three levels of "on":
1. **Loaded**: found in a folder above.
2. **Enabled on this server**: an admin can disable a loaded addon for everyone (Addons page, `POST /api/addons/<name>/disable`, `bin/dana addons disable <name>`; the `addon_state` table, `core/addons/state.py`). A disabled addon offers nothing: no tools, prompt parts, hooks, jobs, services (a running one is cancelled within seconds) or notify channels; its routes and `web/` files answer 404 `addon_disabled`; nobody can connect or change it (its connections and data are kept). Addons whose `needs` include it are disabled with it. The running server sees a change, from the page or the CLI, within 5 seconds; no restart. `everyone` addons cannot be disabled.
3. **On for a user**: the user turned it on (with credentials, if it has any), or it is `default_on` / `everyone`.

## The catalog (marketplace)

`server/catalog.json` lists addons: `name`, `title`, `description`, `icon` (optional, a Lucide name), `author`, `homepage`, `tags`, `min_dana`, and `source` = `{repo, path, ref}` (a git repository, a folder in it, a full commit hash). Servers read it from `DANA_CATALOG_FILE` (a local file) or `DANA_CATALOG_URL` (default: this file on GitHub's `master`) with a 5-second timeout, and keep it 10 minutes (`core/addons/catalog.py`); when the URL cannot be read they use the copy shipped in their image (the file as it was at that release) and the page says so above the list; an entry that is not valid (a missing field, a ref that is not a full commit, a repeated name) is skipped, its reason logged and shown under the list. `GET /api/addons/catalog` (admins) merges it with what is here: official, already in another source, installed (at which commit, loaded or waiting for a restart), update available, compatible with this Dana.

The Addons page (`static/addons.js`) is an onboarding flow built only from the manifests: the addons grouped On / Available / Not available (an admin's choice, off on the server, failed to load: admins see the error) and, for admins, the Catalog; opening one shows what it adds and numbered setup steps (the manifest's `setup`, the sign-in or the fields with their help, Connect, which runs `test=` and says in plain words whether it works), then "Try asking Dana". A user with no optional addon on gets a one-time welcome over the app suggesting addons to set up (`GET /api/addons` → `welcome`, `POST /api/addons/welcome`; dismissed is kept per user in `now_hidden`, and the page can bring it back). An admin installs from the catalog, or any git repository + full commit (+ folder), after a confirmation showing the repository, the commit and that **an installed addon runs with the server's full access**: the server fetches exactly that commit with git (`git init` + `git fetch --depth 1 <repo> <commit>`, no shell; `core/addons/install.py`) into `<data dir>/addons/<name>` with a `.installed.json` record, after checking the folder has an `addon.py` declaring that name (read, not imported), the name is not official, reserved or in another source, and there are no symbolic links. Branches, tags and short hashes are refused. Nothing runs until the restart the page asks for (`POST /api/server/restart`: the process exits and its supervisor starts it again; `server/docs/operations.md`, "Restarting"). Updating is installing a newer commit; removing deletes the folder (tables stay).

`bin/dana addons install <name|repo@commit[:path]>` does the same without a server, into `site/addons/` by default (for images built by `bin/deploy`; `--data` for the data dir, `--dir` for any folder); `bin/dana addons new <name> [--web] [--worker]` writes a working addon to start from (`server/docs/writing-an-addon.md`). The other commands: `list`, `remove`, `disable`, `enable`, and the per-user `show` / `connect` / `update` / `disconnect`.

## Core, not addons

The agent loop, users / access / auth, sessions, runs and the shared thread, undo and approvals, notify (Telegram channel), events, the scheduler, the database and migrations, LLM calls and usage, the web shell and the Addons page, Telegram, realtime voice, Android app updates, and the composers that collect addon hooks. The `essentials` addon (`everyone=True`) holds the always-on tools: the time, asking to choose, web search, `share_item`.
