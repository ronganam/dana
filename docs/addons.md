# Addons

> **Status: target design (branch `addons`); steps 1 (Rename and move), 2 (the framework runtime) and 2b (the marketplace and tooling) are done.** Built: the loader (sources, `needs` order, failure isolation), the manifest and every hook in the table below, automatic routes, jobs and services, events, notify channels, prompt slots, LLM jobs, per-addon migrations with the squashed baselines and the in-place conversion of a 9.0 database; `essentials` and `tasks` use all of it (writing one: `server/docs/writing-an-addon.md`). Also built: `/api/addons` with sources, states and the failed addons; the server-wide disable; serving each addon's `web/` files; workers in `bin/deploy` and `compose.yaml` (money's bank worker); `bin/dana` (users and addons, with the scaffold); the catalog with install / update / remove and the restart; the Addons page's Installed / Official / Catalog tabs. Not built yet, still target: the web shell loading panels (`static/dana.js`), `requirements.txt`, `core.addons.data` and the SQL lint; and the composers still import the other official addons directly (wave 3 moves them onto hooks). Each build step makes more of this true and updates this file. The section "Build plan" at the end is removed once everything is built.

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
catalog.json            the addon catalog: official and community addons and where to get them
```

## What an addon is

A folder. Only `addon.py` is required:

```
addons/weather/
  addon.py          the manifest: `addon = Addon("weather", ...)` plus the hooks below
  tools.py          tools Dana can call            (imported by the loader when present)
  api.py            HTTP routes: `setup(app)`      (registered automatically when present)
  migrations/       m0001_<name>.py, m0002_…        (its own tables, numbered per addon)
  web/              panel.js, panel.css            (a page in the web app, when declared)
  worker/           Dockerfile + code              (a sidecar process, when declared: e.g. scrapers)
  tests/            test_*.py, test_*.mjs          (found by bin/test)
  README.md         what it does, its settings, the events it emits (shown in the catalog)
  requirements.txt  extra Python packages, if any (installed into the image; missing = the addon fails to load, not the server)
```

`bin/dana addons new weather` writes a working one with a test.

## The manifest

```python
# addons/weather/addon.py
from core.addons import Addon, Field, PromptPart, Web, Nav, minutes

addon = Addon(
    "weather",
    title="Weather",
    description="Current weather and the forecast for your places.",
    version="1.0.0",
    needs=(),                                   # other addons it builds on: loaded first, turned on first
    credentials=(Field("api_key", "OpenWeather API key"),),
    settings=(Field("units", "metric or imperial", default="metric"),),
    default_on=False,                           # new users get it turned on (needs no credentials)
    everyone=False,                             # every user has it, cannot turn it off (essentials)
    requires=None,                              # an access capability needed to turn it on ("ops")
    prompt_parts=(PromptPart("guides", "When the user asks about weather, use weather_now."),),
    llm_jobs={"weather_summary": "fast"},       # its LLM purposes and their default model profile
    web=Web(panel="panel.js", style="panel.css", nav=Nav("Weather", icon="cloud-sun", order=60)),
)
```

Everything an addon adds is declared on that one object, as a field or as `@addon.<hook>`:

| Hook | Signature | What the framework does |
|---|---|---|
| `@addon.tool(...)` | `def f(conn, ...)` or `async def` | Offers it to users who have the addon; schema from type hints and the docstring. Flags: `is_write`, `confirm`, `acts_outside`, `untrusted_result`, `needs`, `available`, `describe=` (`def d(args) -> Approval`: the Approve card's words for a call). |
| `@addon.job(every=minutes(5))` / `(daily="10:00")` / `(at_start=True)` | `async def f(user)` | Runs it for every user who has the addon, as that user (`users.acting_as`), one failing user never stopping the next; a heartbeat feeds the self-check. |
| `@addon.service` | `async def f(app)` | A long-running task started once with the server (watchers, websockets); restarted with backoff when it dies. |
| `@addon.on("event.name")` | `async def f(user, data)` | Called when any addon or core emits that event for a user (`core.events.emit`). The way addons react to each other without importing each other. |
| `@addon.channel("push")` | `async def f(user, message) -> bool` | A delivery channel for `core.notify.send(user, Message(text, kind, data))` (Telegram is core's; the phone addon adds push). |
| `@addon.now_cards` | `def f(user, ctx: NowContext) -> list[Card]` | Cards on the Now screen. |
| `@addon.day_items` | `def f(user, ctx: NowContext) -> list[DayItem]` | Items in the Now screen's day strip. |
| `@addon.now_action("prefix")` | `async def f(user, ref, action, args, now) -> dict` | Acting on its cards whose id is `prefix:ref` (`POST /api/now/act`); `core.cards.result(...)`, `ActError` to refuse. |
| `@addon.briefing` | `def f(user, ctx: BriefingContext) -> Section \| None` | A section of the morning briefing / weekly review (`ctx.kind`). |
| `@addon.review_action("type", actions)` | `async def f(user, item, action, decision, today) -> str` | Applying a weekly-review item of that type. |
| `@addon.search` | `def f(user, query, limit) -> list[Hit]` | Results in the global search. |
| `@addon.selfcheck("key", "Label", server=False, after="")` | `def f(user, now) -> Check` | A line in the self-checks (`/api/checks`); a failing one is a Now card. |
| `@addon.context("kind")` / `@addon.context_card("kind")` | `def f(user, ref_id, req) -> str \| None` / `def f(user, ref_id) -> dict` | Text about an item (a call, a task, a person) for context chats, and the card shown with the chat. |
| `test=` (field) | `async def f(conn) -> str \| None` | "Test connection" for a stored connection (was `check`). |
| `options=`, `validate=`, `blocked=`, `on_connect=`, `on_change=`, `connect_url=` | as today | Unchanged from the old `Integration`. |

Composer hooks are plain functions (the composers call them in a worker thread); jobs, services, handlers, channels and the action hooks are `async`. The types (`Card`, `DayItem`, `NowContext`, `BriefingContext`, `Section`, `Hit`, `Check`, `ContextRequest`, `Approval`) are in `core/addons/hooks.py`; each `to_json()` gives the payload the screens have always had.

Core composers (Now, briefing, weekly review, wake, search, self-checks, context chats, approval text) **only** collect these hooks; none of them imports an addon. `core/server.py` imports no addon either: routes, jobs and services come from the registry. *(Built for `core/server.py` and for the `tasks` addon; the composers still import the other official addons until wave 3.)*

### Prompt slots

`PromptPart(slot, text, rank)` slots are a fixed list in `core/agent.py` (`SLOTS`), each with its own joiner (a phrase in a list joins with ", ", a sentence with " ", a paragraph with a blank line). An unknown slot fails the load.

### Data

An addon owns its tables. `migrations/mNNNN_<name>.py` (`VERSION`, `NAME`, `up(conn)`, numbered 1, 2, 3… per addon) run at start for every addon folder found, whether or not anyone has it turned on, recorded in `schema_migrations (addon, version, name, applied_at)`; core's first, then each addon's, after a snapshot of an existing database. Core's own tables are the addon name `core` (`server/core/db/migrations/`). Removing an addon keeps its tables and its rows in `schema_migrations`. The migrations Dana 9.0 numbered in one sequence are squashed into `m0001_baseline` of core and each official addon; a 9.0 database (at migration 33 or 34) is converted in place on its first start (`core/db/convert.py`), an older one is refused ("upgrade with Dana 9.0.0 first").

Per-user data: every query filters by the acting user. *(Target:)* addons get `core.addons.data.UserTable` helpers for the common cases; a test lints SQL on per-user tables (a table with a `user_id` column touched without `user_id` in the statement fails unless the line says `# scoped:` with the reason).

### Events

`core.events.emit(name, user, **data)`; handlers of the addons that user has run in the background as that user, a failing one logged (tests wait with `events.drain()`). Names are `<addon>.<thing>.<what happened>` (`tasks.task.created`, `homeassistant.location.changed`); core's own start with `core.` (`core.user.woke`). Each addon lists the events it emits and their data (its README, or the module that emits them until it has one: `addons/tasks/service.py`).

### Web

An addon with `web=` gets its files served at `/addons/<name>/web/<file>` (built: signed in like every page, only for a loaded addon enabled on the server, nothing outside its `web/` folder, no dot-files; `core/addons_api.py`), and `/api/addons` gives each user, for the addons on for them, `web: {panel, style, nav}` with those URLs. *(Target, wave 3:)* the web shell asks `GET /api/addons` for the user's addons and loads each enabled one's panel; the panel registers itself with the small SDK in `static/dana.js` (`Dana.panel({id, title, icon, mount(el, api), unmount()})`). The shell owns navigation, auth, the theme and the service worker (which caches what `/api/addons` lists).

### Workers (sidecars)

An addon with a `worker/` folder (a self-contained `Dockerfile` context) declares `worker=Worker(port=8090, url_env="MONEY_WORKER_URL")`. `bin/deploy` builds one image per worker (`<DANA_IMAGE>-<addon>`, same tag as Dana's; `DANA_WORKERS` picks them) and writes it to your GitOps values (`workers.<addon>.image`); `compose.yaml` runs it under a profile with the addon's name (`--profile money_il`); the app image leaves `worker/` folders out. Details: `server/docs/operations.md`, "Workers". The bank scrapers are the `money_il` provider addon's worker (`server/addons/money_il/worker/`).

### LLM jobs

`llm_jobs={"purpose": "profile"}` registers purposes for `core.llm.complete(purpose=...)`; the model is `MODEL_<PURPOSE>` from env, else the profile's model (`fast` / `balanced` / `deep`), and usage is filed under the purpose.

## Where addons come from, and turning them on

| Source | Folder | Who adds it | Trust |
|---|---|---|---|
| Official | `server/addons/` | ships with Dana | reviewed in this repository |
| Site | `site/addons/` (or `$DANA_SITE/addons`) | you, in your site | yours |
| Installed | `<data dir>/addons/` | an admin, from the catalog or a git URL | pinned to a commit; runs with the server's full access, shown before installing |

`DANA_ADDON_DIRS` replaces the site, image and data folders (`""` = official only: the tests). Load order: an addon after every addon in its `needs`; among those free to go, official addons first by `order=` then name, then the others by name; that order is the order of every tool list and hook result. Two addons with one name fail the start. A site or installed addon that fails to import, needs a missing addon or names an unknown prompt slot is skipped and recorded as failed with the error (`loader.failed()`; listed by `/api/addons`, on the Addons page and by `bin/dana addons list`, the error for admins only); an official one failing stops the start (it is a bug).

Three levels of "on":
1. **Loaded**: found in a folder above.
2. **Enabled on this server**: an admin can disable a loaded addon for everyone (Addons page, `POST /api/addons/<name>/disable`, `bin/dana addons disable <name>`; the `addon_state` table, `core/addons/state.py`). A disabled addon offers nothing: no tools, prompt parts, hooks, jobs, services (a running one is cancelled within seconds) or notify channels; its routes and `web/` files answer 404 `addon_disabled`; nobody can connect or change it (its connections and data are kept). Addons whose `needs` include it are disabled with it. The running server sees a change, from the page or the CLI, within 5 seconds; no restart. `everyone` addons cannot be disabled.
3. **On for a user**: the user turned it on (with credentials, if it has any), or it is `default_on` / `everyone`.

## The catalog (marketplace)

`catalog.json` at the repository root lists addons: `name`, `title`, `description`, `author`, `homepage`, `tags`, `min_dana`, and `source` = `{repo, path, ref}` (a git repository, a folder in it, a full commit hash). Servers read it from `DANA_CATALOG_FILE` (a local file) or `DANA_CATALOG_URL` (default: this file on GitHub's `master`) with a 5-second timeout, and keep it 10 minutes (`core/addons/catalog.py`); an entry that is not valid (a missing field, a ref that is not a full commit, a repeated name) is skipped, its reason logged and shown under the list. `GET /api/addons/catalog` (admins) merges it with what is here: official, already in another source, installed (at which commit, loaded or waiting for a restart), update available, compatible with this Dana.

The Addons page shows Installed, Official and Catalog. An admin installs from the catalog, or any git repository + full commit (+ folder), after a confirmation showing the repository, the commit and that **an installed addon runs with the server's full access**: the server fetches exactly that commit with git (`git init` + `git fetch --depth 1 <repo> <commit>`, no shell; `core/addons/install.py`) into `<data dir>/addons/<name>` with a `.installed.json` record, after checking the folder has an `addon.py` declaring that name (read, not imported), the name is not official, reserved or in another source, and there are no symbolic links. Branches, tags and short hashes are refused. Nothing runs until the restart the page asks for (`POST /api/server/restart`: the process exits and its supervisor starts it again; `server/docs/operations.md`, "Restarting"). Updating is installing a newer commit; removing deletes the folder (tables stay).

`bin/dana addons install <name|repo@commit[:path]>` does the same without a server, into `site/addons/` by default (for images built by `bin/deploy`; `--data` for the data dir, `--dir` for any folder); `bin/dana addons new <name>` writes a working addon to start from (`server/docs/writing-an-addon.md`). The other commands: `list`, `remove`, `disable`, `enable`, and the per-user `show` / `connect` / `update` / `disconnect`.

## Core, not addons

The agent loop, users / access / auth, sessions, runs and the shared thread, undo and approvals, notify (Telegram channel), events, the scheduler, the database and migrations, LLM calls and usage, the web shell and the Addons page, Telegram, realtime voice, Android app updates, and the composers that collect addon hooks. The `essentials` addon (`everyone=True`) holds the always-on tools: the time, asking to choose, web search, `share_item`.

## Build plan (temporary)

1. **Rename and move** (one agent, no behavior change): `server/integrations/` → `server/addons/`, `integrations/framework/` → `core/addons/`, `Integration` → `Addon`, `kind.py` → `addon.py`, the `core` kind → `essentials`, plugins → site addons, tests into each addon's `tests/` (core tests into `server/tests/`), `bin/test` finds them, the API `/api/integrations*` → `/api/addons*`, docs follow.
2. **Framework** (one agent; done, the marketplace and tooling as step 2b): the loader (sources, `needs` order, failure isolation, server-wide disable), auto routes, jobs and services, per-addon migrations with the squashed baselines and the in-place conversion of an existing 9.0 database, events, notify channels, prompt slots, LLM jobs, web panel serving and `/api/addons`, workers, `bin/dana` (addons new / list / install / disable, users), the catalog. Converts `essentials` and `tasks` fully as the first slice; reviewed deeply.
3. **Waves in parallel**: composers onto hooks and the remaining addons converted (2–3 agents); addon cycles broken with events and notify channels; money split into `money` and `money_il` (scrapers as its worker); the web shell and panels; security defaults, first run and locale; Android (app id, flavors, protocol module, typed API, cleanup).
4. **Finish**: leftovers and docs, CI (contract drift, Raycast, Docker build, Dependabot), README, an independent review, a rehearsal on a copy of the production database, then the rollout.
