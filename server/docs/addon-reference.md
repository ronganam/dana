# Addon reference

Generated from Dana's code by `bin/dana addons reference` (`server/cli/kit/reference.py`): do not edit it by hand; Dana's tests fail when it differs from the code. Every example is code of the [showcase addon](https://github.com/ronganam/dana/blob/master/examples/addons/showcase), which uses each capability once and is tested with Dana. The guide to writing one: [`server/docs/writing-an-addon.md`](https://github.com/ronganam/dana/blob/master/server/docs/writing-an-addon.md); the design: [`docs/addons.md`](https://github.com/ronganam/dana/blob/master/docs/addons.md).

## Contents

- [The folder](#the-folder)
- [The manifest](#the-manifest)
- [Manifest values](#manifest-values)
- [Functions on the manifest](#functions-on-the-manifest)
- [Hooks](#hooks)
- [What hooks get and return](#what-hooks-get-and-return)
- [More examples](#more-examples)
- [The manifest as JSON](#the-manifest-as-json)

## The folder

```
<name>/
  addon.py          addon = Addon("<name>", ...): the manifest (required)
  tools.py          @addon.tool() functions              imported when present
  hooks.py          jobs, handlers, Now cards…           imported when present
  <module>.py       more hooks: list it in modules=(...)
  api.py            setup(app): its HTTP routes          registered automatically
  migrations/       m0001_baseline.py, m0002_…           its own tables
  web/              its pages: scripts, styles           served at /addons/<name>/web/<file>
  worker/           a sidecar's Dockerfile and code
  requirements.txt  extra Python packages
  tests/            test_*.py, test_*.mjs                bin/dana addons test
  README.md         what it does, settings, events       shown in the catalog
```

Another module of the addon imports the manifest as `from addons.<name>.addon import addon`.

## The manifest

`addon = Addon("<name>", ...)` in `addon.py`. Every field but `name` is optional.

| Field | Type | Default | What |
|---|---|---|---|
| `name` | str | required | Unique id (`"homeassistant"`), the folder's name. |
| `title` | str | `''` | Its name for people ("Home Assistant"); default: the name, capitalised. |
| `description` | str | `''` | What it does, in a sentence. |
| `version` | str | `''` | Its own version ("1.0.0"). |
| `needs` | list of Need | `()` | What running it takes, a `Need` each: env values, accounts, hardware, the phone app, a region, other addons (required or `optional`). The required addon needs are `depends`: loaded (and their migrations run) before it, on for a user before it is active for them; a site / installed addon whose required addons are missing is not loaded. |
| `touches` | Touches | `Touches()` | What it may touch beyond Dana's own data (`Touches`: hosts, acting for the user outside Dana, stored credentials, other addons' data). An addon with `credentials` must say it stores them. |
| `credentials` | list of Field | `()` | Fields needed to connect, stored encrypted. |
| `settings` | list of Field | `()` | Per-user choices, stored as plain JSON (defaults allowed). |
| `default_on` | bool | `False` | New users get it turned on (it must need no credentials): `connect_defaults`. |
| `everyone` | bool | `False` | Every user has it, with no row and no way to turn it off (the `essentials` addon). |
| `requires` | str or None | `None` | An `AccessLevel` capability the user's level must have to connect it (`"ops"`), or None. |
| `prompt_parts` | list of PromptPart | `()` | The addon's pieces of the agent's fixed instructions (`PromptPart`), included only for a user who has the addon (`prompt_parts`); the slot must be one of `core.agent.SLOTS` (checked when loading). |
| `llm_jobs` | dict of str → str or LlmJob or None | `None` | Its LLM purposes and their default model profile (`{"task_emoji": "fast"}`, or an `LlmJob` with a label): `core.llm` accepts them as `purpose=`. |
| `web` | list of Page | `()` | Its pages in the web app, any number: a tuple of `Page` (files served from its `web/`; page ids unique across addons). |
| `worker` | Worker or None | `None` | Its sidecar (`Worker`): a published image, or built from its `worker/`. |
| `modules` | list of str | `()` | More modules of its folder the loader imports after `addon.py` and `tools.py` (dotted, relative: `"calls.tools"`): where its other tools and hooks are declared. `hooks.py` is imported without being named. |
| `order` | int or None | `None` | Its place among the official addons (lower first; others follow by name). |
| `on_connect` | function | `None` | Called with the user right after the addon is connected for them (e.g. `tasks` gives them the default task lists); a failure is logged and does not undo the connection. |
| `on_credentials_change` | function | `None` | Called with the user right after `update` changed the stored credentials (another address or account: `whatsapp` learns its account again); a failure is logged and does not undo the change. |
| `on_change` | function | `None` | Called with the user right after any connect, update or disconnect of the addon for them (`homeassistant`: reconcile the entity watchers now); a failure is logged. |
| `prompt_section` | function | `None` | The addon's section of the agent's system prompt for a user (`about`: their facts), asked on every turn through `prompt_section` only while the user has the addon connected. It must be cheap (cached), never block and return "" for nothing. |
| `test` | function | `None` | An async function of a stored connection making one cheap request: None when the connection works, else a short reason (never a secret). `POST /api/addons/{name}/test`. |
| `options` | function | `None` | An async function of a stored connection returning values a client can offer for its settings, read from the user's own service. `GET /api/addons/{name}/options`. |
| `change_requires` | str or None | `None` | A capability the user who *makes* a change needs to connect, update or disconnect the addon, for themselves or anyone (`cameras`: `manage_users`, so an admin decides who sees which cameras). Existing connections keep working for their user. |
| `validate` | function | `None` | The addon's own rule over the values a change would store (`SettingsRule`; `shopping`: one list per Telegram group, the active list; `whatsapp`: one user per WAHA session). |
| `blocked` | function | `None` | A function of the user's connection returning why its tools cannot run for it right now, or None (`whatsapp`: the account its WAHA reports is held by another user's connection). Asked by `execute` before each of the addon's tools; the reason becomes the tool's result (`"Error: <reason>"`), the tool function is not called. |
| `connect_url` | str or None | `None` | A path of this server that connects the addon by a sign-in at the service instead of typed credentials (the Google addons: `/api/google/connect`); clients offer it as a button. |
| `connect_label` | str | `''` | That button's words ("Connect Telegram"); empty: the Google sign-in's. |
| `realtime` | bool | `True` | False keeps the addon's tools out of realtime voice calls, which have no Approve card and no rule for untrusted content (`gmail`): there they are neither offered nor run. |
| `icon` | str | `''` | Its icon on the Addons page: a Lucide icon name (`"house"`). |
| `features` | list of str | `()` | What it adds for the user, a short line each in plain words ("Turn lights and devices on and off"); empty: the first line of each tool's docstring. |
| `setup` | list of str | `()` | Steps done outside Dana before connecting, in order, one sentence each ("Install the WAHA server", "Scan its QR code with WhatsApp"); the Addons page numbers them before its own steps. |
| `examples` | list of str | `()` | Things to say to Dana once it is on ("Turn off the living room lights"): shown after connecting. |
| `disconnect_warning` | str | `''` | What disconnecting deletes, in one sentence (`money`: the logins and transactions); empty: nothing is deleted. |

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
addon = Addon(
    "showcase",
    title="Plant care",
    description="Example addon: watering reminders for your house plants, using every capability once",
    version="1.0.0",
    icon="sprout",
    features=("Reminds you which plants need water", "Care tips for a plant"),
    setup=("Get a key at the plant service (any value works with the example's fake).",),
    examples=("Which plants need water today?", "Add a basil that needs water every 2 days"),
    disconnect_warning="Turning it off keeps your plants; turning it on again brings them back.",
    needs=(Need("env", "SHOWCASE_API_URL", "Another address for the plant service (tests point it at a fake)",
                optional=True),
           Need("env", "SHOWCASE_WORKER_URL", "Where Dana reaches the worker (default http://127.0.0.1:8092)",
                optional=True),
           Need("account", "a plant service account", "Its key unlocks the care tips", optional=True),
           Need("addon", "tasks", "Watering can also become a task", optional=True)),
    touches=Touches(hosts=("api.plants.example",), stores_credentials=True),
    credentials=(Field("api_key", "The plant service key", required=False, label="API key",
                       help="On the plant service: Account → API keys.", url="https://api.plants.example/keys",
                       placeholder="pk-123"),),
    settings=(Field("room", "Where most plants are", default="kitchen", label="Room",
                    help="Used in reminders.", placeholder="kitchen"),
              Field("remind_hour", "The hour reminders appear", default=9, label="Reminder hour",
                    help="Between 6 and 22."),
              Field("paused", "Pause plant care", default=False, label="Paused", help="True pauses its tools.")),
    prompt_parts=(PromptPart("tools", "your house plants"),
                  PromptPart("guides", "For house plants, use the showcase_ tools; add a plant before watering it.",
                             rank=90)),
    prompt_section=plants_section,
    llm_jobs={"showcase_poem": LlmJob("fast", "Plant poems")},
    web=(Page("plants", panel="plants.js", style="plants.css", nav=Nav("Plants", icon="sprout", order=85)),),
    worker=Worker(port=8092, url_env="SHOWCASE_WORKER_URL"),
    modules=("now",),                    # more modules the loader imports (tools.py and hooks.py go without saying)
    on_connect=welcome,
    test=check_connection,
    options=setting_options,
    validate=check_values,
    blocked=paused,
)
```

## Manifest values

### Need

Something the addon needs from whoever runs it, shown before it is
turned on (the Addons page, `bin/dana addons table`, the README).

`kind` is one of `NEED_KINDS`: `env` (`what` is the env
variable, `GOOGLE_CLIENT_ID`), `account` (an account at a service),
`hardware` (a device, or a server you run: Home Assistant, WAHA),
`app` (the Dana Android app), `region` (where it works at all: a
country), `addon` (`what` is the other addon's name). Other kinds'
`what` is a phrase that reads inside a sentence ("a Google account").
`why` says in one line what it is for. `optional`: the addon works without it, with
less. A required `addon` need is loaded first and must be on for the
addon to work (`Addon.depends`).

| Field | Type | Default |
|---|---|---|
| `kind` | str | required |
| `what` | str | required |
| `why` | str | required |
| `optional` | bool | `False` |

### Touches

What the addon may touch beyond Dana's own data, shown before it is
turned on and before it is installed: the network `hosts` it contacts
(a domain, or words for an address the user gives: "your Home Assistant
server"), whether it `acts_outside` (sends messages or mail, changes
things at a service or on devices for the user), whether it
`stores_credentials` (passwords, tokens, sign-ins; encrypted), and the
other addons whose data it `reads`. A declaration, not a sandbox: every
addon runs with the server's full access (SECURITY.md). Installing or
updating an addon whose touches grew asks the admin again
(core/addons/install.py).

| Field | Type | Default |
|---|---|---|
| `hosts` | list of str | `()` |
| `acts_outside` | bool | `False` |
| `stores_credentials` | bool | `False` |
| `reads` | list of str | `()` |

### Field

One credential or setting of an addon.

`default` (settings only): the value a connection gets while the user has
not set one. `required` (credentials only): connecting without it is
refused. `type`: one of `FIELD_TYPES` (what the value must look
like and who may set it).

What the Addons page shows beside the input, for someone setting it up:
`label` (its short name, "Access token"; default: the name in words),
`help` (where to find the value, one sentence of plain words: "In Home
Assistant: your profile → Security → Long-lived access tokens → Create
token"), `url` (an `https://` page where it is found or made, shown as
a link) and `placeholder` (an example value inside the empty input).
`description` stays what the value is for.

| Field | Type | Default |
|---|---|---|
| `name` | str | required |
| `description` | str | `''` |
| `default` | any | `None` |
| `required` | bool | `True` |
| `type` | str | `'text'` |
| `label` | str | `''` |
| `help` | str | `''` |
| `url` | str | `''` |
| `placeholder` | str | `''` |

### PromptPart

A piece of the agent's instructions that applies only to a user who has
the addon (and every addon in `needs`) connected.

`slot` names the place in the instructions (core/agent.py lists the
slots and how each joins its parts: a phrase in a list, a sentence, a
paragraph); `rank` orders the parts of one slot (lower first; ties in
the fixed addon order).

| Field | Type | Default |
|---|---|---|
| `slot` | str | required |
| `text` | str | required |
| `rank` | int | `50` |
| `needs` | list of str | `()` |

### LlmJob

An LLM purpose of the addon: its default model profile (`fast` /
`balanced` / `deep`), how the Usage screen names it ("" = from the
purpose's name), and whether it is `background` work Dana does on its
own (the monthly budget pauses it; core/budget.py) rather than something a
person asked for.

| Field | Type | Default |
|---|---|---|
| `profile` | str | required |
| `label` | str | `''` |
| `background` | bool | `False` |

### Page

A page of the addon in the web app: `id` names the screen (its
`#panel-<id>` element, `/<id>` and `#<id>` open it; unique across
addons, not a core screen's), `panel` (JavaScript) and `style` (CSS)
are paths inside the addon's `web/` folder, served at
`/addons/<name>/web/` and listed by `GET /api/addons`
(core/addons_api.py); `nav` puts a button in the navigation (None: the
page opens only from a link or another page).

| Field | Type | Default |
|---|---|---|
| `id` | str | required |
| `panel` | str | required |
| `style` | str | `''` |
| `nav` | Nav or None | `None` |

### Nav

The addon's entry in the web app's navigation: `order` places it
among the others, `primary` asks for a place in the default navigation
bar (Chat and up to three such pages; each user may choose their own:
docs/web-design.md, "Navigation").

| Field | Type | Default |
|---|---|---|
| `label` | str | required |
| `icon` | str | `''` |
| `order` | int | `100` |
| `primary` | bool | `False` |

### Worker

A sidecar process next to Dana: the port it listens on and the env
variable that tells Dana its URL. `image`: the published image it runs
(`ghcr.io/<owner>/<name>`, its repository building it; a tag of its own,
else `bin/deploy` pins it to the installed commit), the way for an addon
installed from the catalog; empty: `bin/deploy` builds the addon's
`worker/` folder (a `Dockerfile`) as `<image>-<addon>`, the way for a
site addon. `compose.yaml` runs it under a profile named after the addon
(docs/operations.md, "Workers").

| Field | Type | Default |
|---|---|---|
| `port` | int | required |
| `url_env` | str | required |
| `image` | str | `''` |

## Functions on the manifest

Given as manifest fields (`test=check_connection`); the signature is the example's.

### `test=`

An async function of a stored connection making one cheap request: None when the connection works, else a short reason (never a secret). `POST /api/addons/{name}/test`.

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
async def check_connection(conn: Connection) -> Optional[str]:
    """"Test connection": None when the plant service answers with the stored key, else a reason a person can act on."""
    from addons.showcase import service
    try:
        await service.tip(conn, "fern")
    except service.ServiceError as e:
        return str(e)
    return None
```

### `options=`

An async function of a stored connection returning values a client can offer for its settings, read from the user's own service. `GET /api/addons/{name}/options`.

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
async def setting_options(conn: Connection) -> Dict[str, List[Dict[str, Any]]]:
    """Values the Addons page offers for the ``room`` setting."""
    return {"room": [{"value": r, "label": r.title()} for r in ("kitchen", "bedroom", "balcony")]}
```

### `validate=`

The addon's own rule over the values a change would store (`SettingsRule`; `shopping`: one list per Telegram group, the active list; `whatsapp`: one user per WAHA session).

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
def check_values(user: User, given: Mapping[str, Any], credentials: Mapping[str, Any],
                 settings: Mapping[str, Any], by: Optional[User]) -> None:
    """Refuse a reminder hour outside the day."""
    hour = settings.get("remind_hour")
    if not isinstance(hour, int) or not 6 <= hour <= 22:
        raise InvalidValue("remind_hour", "Pick an hour between 6 and 22.")
```

### `on_connect=`

Called with the user right after the addon is connected for them (e.g. `tasks` gives them the default task lists); a failure is logged and does not undo the connection.

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
def welcome(user: User) -> None:
    """Right after a user turns it on: their first plant, so the Now screen has something to show."""
    from addons.showcase import store
    if not store.plants(user):
        store.add_plant(user, "Basil", every_days=2)
```

### `prompt_section=`

The addon's section of the agent's system prompt for a user (`about`: their facts), asked on every turn through `prompt_section` only while the user has the addon connected. It must be cheap (cached), never block and return "" for nothing.

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
def plants_section(user: User) -> str:
    """The user's plants in the system prompt, every turn (cheap: one small query)."""
    from addons.showcase import store
    names = [p["name"] for p in store.plants(user)]
    return f"The user's house plants: {', '.join(names)}." if names else ""
```

### `blocked=`

A function of the user's connection returning why its tools cannot run for it right now, or None (`whatsapp`: the account its WAHA reports is held by another user's connection). Asked by `execute` before each of the addon's tools; the reason becomes the tool's result (`"Error: <reason>"`), the tool function is not called.

From [`examples/addons/showcase/addon.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/addon.py):

```python
def paused(conn: Connection) -> Optional[str]:
    """Why its tools cannot run for this connection now: while the user paused it."""
    return "Plant care is paused in your settings." if conn.settings.get("paused") else None
```

### `on_change=`

Called with the user right after any connect, update or disconnect of the addon for them (`homeassistant`: reconcile the entity watchers now); a failure is logged.

### `on_credentials_change=`

Called with the user right after `update` changed the stored credentials (another address or account: `whatsapp` learns its account again); a failure is logged and does not undo the change.

## Hooks

Declared with a decorator on the `addon` object, in any module the loader imports. Composer hooks (`now_cards`, `day_items`, `review`, `search`, `selfcheck`, `context*`) are plain functions; jobs, services, handlers, channels and the action hooks are `async`; `briefing`, `provide` and `widget` may be either. Keys (a card prefix, a section, a check, a widget) are unique across addons; a provider key may be declared by several, ranked.

### `@addon.tool(name=None, *, is_write=False, confirm=False, acts_outside=False, needs=(), available=None, untrusted_result=False, describe=None, shortcut=False, shortcut_prep=False, shortcut_describe=None)`

Register a function as a tool of this addon. The function's first
parameter is `conn` (the user's connection), never in the schema.

Args:
    name: Tool name (defaults to the function name).
    is_write: The tool changes something (data, devices, other people's
        inboxes). Informational on its own: a write tool runs at once
        and registers an undo where it can (`core.undo`).
    confirm: The agent suspends and asks the user before running it
        (Approve / Deny). Only for tools that send something to another
        person or destroy something. Either a bool, or a callable that
        gets the call's arguments and returns whether this particular
        call needs asking (e.g. add_event only when it invites people).
        Implies `is_write`.
    acts_outside: The tool acts on the world outside Dana and cannot be
        undone (home devices, the TV, the cluster, running code), or it
        writes something that feeds every later prompt (what Dana
        remembers about the user). It runs at once normally, but asks
        first in a turn that carries text other people wrote (the
        phone screen the user shared, an `untrusted_result` tool's
        result, a context or message framed by
        `core.text.untrusted`): see
        `needs_confirmation`.
    needs: Other addons the tool also requires; it is offered and runs
        only for a user who has all of them connected.
    available: A function of the user, asked on top of the addons
        both when listing and when executing: the tool is offered and
        runs only while it returns True (`share_item`: more than one
        user on the server). It must be cheap (asked for every tool
        list) and never raise.
    untrusted_result: The tool's result carries text written by other
        people (an email, a chat message, a notification, a web page).
        The model reads the result framed by `core.text.untrusted`
        (unless the tool framed it itself), and once it ran the rest
        of that turn counts as carrying untrusted content: every
        `acts_outside` tool asks first (`core/agent.py`).
    describe: The Approve card's words for one call: ``f(user,
        args)`` returning a `Approval`
        (`core/approval_text.py` uses it before its own
        descriptions; when it raises, the default card is shown).
    shortcut: Calls of this tool may become learned phrases
        (core/shortcuts.py): a bool, or a function of the call's
        arguments saying whether this call may.
    shortcut_prep: A read-only lookup allowed before the shortcut's call
        in a turn that teaches a phrase.
    shortcut_describe: `f(user, args) -> str`: what a learned phrase
        for this call does, in words (default: the name and arguments).

Re-registering a name in the same addon replaces that tool in place
(tests swap a tool for a fake this way); a name owned by another addon
is refused.

From [`examples/addons/showcase/tools.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/tools.py):

```python
@addon.tool(is_write=True)
def showcase_add_plant(conn: Connection, name: str, every_days: int = 3) -> str:
    """Add a house plant to look after.

    Args:
        name: What the plant is called ("Basil", "the big fern").
        every_days: How many days it goes between waterings.
    """
    plant_id = store.add_plant(conn.user, name.strip(), every_days)
    undo.record(f"Add {name}", lambda: store.remove(conn.user, plant_id), source="showcase")
    return f"Added {name} (#{plant_id}), watered every {every_days} days."
```

From [`examples/addons/showcase/tools.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/tools.py):

```python
def describe_forget(user: User, args: Dict[str, Any]) -> Approval:
    """The Approve card's words for one showcase_forget call."""
    p = store.plant(user, int(args.get("plant_id") or 0))
    return Approval(title=f"Forget {p['name'] if p else 'a plant'}", heading="Forget a plant", emoji="🪴",
                    question="Forget it and its watering history?")


@addon.tool(confirm=True, describe=describe_forget)
def showcase_forget(conn: Connection, plant_id: int) -> str:
    """Forget a plant for good (asks the user first).

    Args:
        plant_id: The plant's id, from showcase_plants.
    """
    return "Forgotten." if store.remove(conn.user, plant_id) else "Error: no such plant."
```

From [`examples/addons/showcase/tools.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/tools.py):

```python
@addon.tool()
async def showcase_poem(conn: Connection, plant_id: int) -> str:
    """A two-line poem about one of the user's plants.

    Args:
        plant_id: The plant's id, from showcase_plants.
    """
    p = store.plant(conn.user, plant_id)
    if p is None:
        return "Error: no such plant."
    return await llm.complete([{"role": "user", "content": f"Two short lines about a house plant called {p['name']}."}],
                              purpose="showcase_poem")
```

### `@addon.job(*, every=None, daily=None, at_start=False, delay=0.0, timeout=600.0, name=None)`

`async def f(user)` run for every user who has this addon, each as
that user (`users.acting_as`), one failing user never stopping the
next (core/addons/scheduler.py). Exactly one of `every` (an interval:
`minutes(5)`; a restart waits out what is left of it), `daily`
("HH:MM", local time; once a day, also on the day the server starts after
that time, never twice on one day across restarts) or `at_start` (once,
when the server starts). `delay`: seconds after the start before its
first pass. `timeout`: seconds one user's run may take before it is
cancelled. Each pass writes a heartbeat (`last_run`).

From [`examples/addons/showcase/hooks.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/hooks.py):

```python
@addon.job(daily="09:00")
async def remind(user: User) -> int:
    """Each morning: tell the user which plants need water (an event other addons may react to)."""
    due = store.due(user, clock.now())
    if due:
        events.emit("showcase.plants.thirsty", user, names=[p["name"] for p in due])
        await notify.send(user, notify.Message(text=f"Water today: {', '.join(p['name'] for p in due)}",
                                               kind="showcase", data={"count": len(due)}))
    return len(due)
```

### `@addon.service`

`async def f(app)`: a long-running task started once with the
server (watchers, websockets), restarted with backoff when it ends or raises.

From [`examples/addons/showcase/hooks.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/hooks.py):

```python
TICKS = {"count": 0}


@addon.service
async def tick(app: Any) -> None:
    """Once per server, for as long as it runs (restarted with backoff if it ends): counts hours."""
    while True:
        TICKS["count"] += 1
        await asyncio.sleep(hours(1).total_seconds())
```

### `@addon.on(event)`

`async def f(user, data)` called in the background, as `user`,
whenever any addon or core emits `event` for a user who has this addon
(core/events.py). A failure is logged.

From [`examples/addons/showcase/hooks.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/hooks.py):

```python
@addon.on("tasks.task.created")
async def task_created(user: User, data: Dict[str, Any]) -> None:
    """A task titled "Water <name>" adds that plant, when the user does not have it yet."""
    title = str(data.get("title") or "")
    name = title[len("Water "):].strip() if title.startswith("Water ") else ""
    if name and all(p["name"].lower() != name.lower() for p in store.plants(user)):
        store.add_plant(user, name, every_days=3)
```

### `@addon.channel(name)`

`async def f(user, message) -> bool`: a delivery channel of
`send` for users who have this addon; True when it
delivered the message (core/notify.py).

From [`examples/addons/showcase/hooks.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/hooks.py):

```python
@addon.channel("showcase")
async def keep_message(user: User, message: notify.Message) -> bool:
    """A delivery channel of notify.send: keeps every message for the Plants page; True: delivered."""
    if not message.text:
        return False
    store.keep(user, message.text)
    return True
```

### `@addon.now_cards(*, order=50)`

`def f(user, ctx: NowContext) -> list[Card]`: cards on the Now screen.
Usable as `@addon.now_cards` or `@addon.now_cards(order=20)`.

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.now_cards(order=60)
def thirsty_cards(user: User, ctx: NowContext) -> List[Card]:
    """A card for each plant that needs water now."""
    return [Card(id=f"plant:{p['id']}", kind="plant", source="Plants", title=f"Water {p['name']}",
                 why=f"Every {p['every_days']} days", primary=cards.action("water", "Watered"), schedulable=False)
            for p in store.due(user, ctx.now)]
```

### `@addon.now_action(prefix)`

`async def f(user, ref, action, args, now) -> dict`: acting on this
addon's Now cards whose id is `<prefix>:<ref>` (`POST /api/now/act`);
raise `core.cards.ActError` to refuse, return `core.cards.result(...)`.
The user must have this addon (403 `not_connected` otherwise).

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.now_action("plant")
async def act(user: User, ref: str, action: str, args: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    """``water`` on a card "plant:<id>": that plant is watered now."""
    if action != "water":
        raise cards.ActError(f"'{action}' isn't an action for a plant")
    if not ref.isdigit() or not store.water(user, int(ref), now):
        raise cards.ActError("No such plant.", 404)
    return cards.result("Watered")
```

### `@addon.day_items`

`def f(user, ctx: NowContext) -> list[DayItem]`: items in the Now day strip.

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.day_items
def watering_time(user: User, ctx: NowContext) -> List[DayItem]:
    """Today's watering, at the user's reminder hour, when a plant needs it."""
    due = store.due(user, ctx.now)
    if not due:
        return []
    hour = int(store_settings(user).get("remind_hour") or 9)
    return [DayItem(title=f"Water {len(due)} plant{'s' if len(due) > 1 else ''}", kind="plant",
                    at=ctx.now.replace(hour=hour, minute=0, second=0, microsecond=0))]
```

### `@addon.briefing(key, *, counts=(), default=..., rank=50)`

`def f(user, ctx: BriefingContext) -> Section | None` (or async): this addon's
section of the morning briefing, stored under `key` in the briefing's
data (unique across addons). `counts`: the keys of `Section.counts`
it may give the briefing's push (unique across addons; others are dropped).
`default`: stored under `key` when the addon is not active for a user
or returns None. `rank`: orders plain-briefing points (lower first).

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.briefing("plants", counts=("thirsty_plants",), default=None)
def briefing_section(user: User, ctx: BriefingContext) -> Optional[Section]:
    """The morning briefing's plants: which need water (a count for the phone's push)."""
    due = [p["name"] for p in store.due(user, ctx.now)]
    if not due:
        return None
    return Section(data={"thirsty": due}, points=[f"Water {', '.join(due)}"], counts={"thirsty_plants": len(due)})
```

### `@addon.review(key)`

`def f(user, ctx: BriefingContext) -> Section | None`: this addon's
section of the weekly review (`key` unique across addons): its
`items` are the decisions, acted on by its `@addon.review_action`,
its `data` joins the review's facts.

Example: under `@addon.review_action`.

### `@addon.review_action(item_type, actions, *, primary=True)`

`async def f(user, item, action, decision, today) -> str`: applying
a weekly-review item of `item_type` (`actions`: what the model may
propose for it; `keep` is always allowed). `primary=False` marks a
type that enriches a review without making a user eligible on its own.
Returns the result's words.

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.review("plants")
def review_section(user: User, ctx: BriefingContext) -> Optional[Section]:
    """The weekly review: plants not watered for two weeks, to keep or forget."""
    cutoff = ctx.now - datetime.timedelta(days=14)
    stale = [p for p in store.plants(user)
             if p["last_watered"] and datetime.datetime.fromisoformat(p["last_watered"]) < cutoff]
    if not stale:
        return None
    return Section(data={"stale_plants": len(stale)},
                   items=[{"ref": str(p["id"]), "type": "plant", "title": p["name"],
                           "why": "Not watered for two weeks", "facts": {"every_days": p["every_days"]}}
                          for p in stale])


@addon.review_action("plant", ("forget",))
async def apply_review(user: User, item: Dict[str, Any], action: str, decision: Dict[str, Any],
                       today: datetime.date) -> str:
    """Apply the review's decision on a plant item: ``forget`` it (``keep`` changes nothing)."""
    if action == "forget" and str(item.get("ref", "")).isdigit() and store.remove(user, int(item["ref"])):
        return f"Forgot {item.get('title')}"
    return "Kept"
```

### `@addon.search`

`def f(user, query, limit) -> list[Hit]`: at most `limit` results in
the global search (`core.addons.helpers.match_score` / `like_pattern` help match).

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.search
def search_plants(user: User, query: str, limit: int) -> List[Hit]:
    """The user's plants whose name matches the global search."""
    hits = [Hit(kind="plant", id=str(p["id"]), title=p["name"], subtitle=f"Every {p['every_days']} days",
                score=match_score(p["name"], query)) for p in store.plants(user)]
    return sorted((h for h in hits if h.score > 1), key=lambda h: -h.score)[:limit]  # 0.5: no match at all
```

### `@addon.selfcheck(key, label, *, server=False, after='')`

`def f(user, now) -> Check`: a line of the self-checks
(`/api/checks`) for users who have this addon; `server=True`: a check
of the server itself, shown to users who may see everyone instead.
`after`: the check it is listed after (default: at the end).

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.selfcheck("showcase_overdue", "Plants watered")
def overdue_check(user: User, now: datetime.datetime) -> Check:
    """Fails when a plant has waited twice its interval for water (a Now card says which)."""
    late = [p["name"] for p in store.plants(user) if p["last_watered"]
            and now - datetime.datetime.fromisoformat(p["last_watered"]) > datetime.timedelta(days=2 * p["every_days"])]
    if late:
        return Check(ok=False, detail=f"{len(late)} overdue", title="Plants are overdue",
                     why=f"{', '.join(late)} waited twice their interval")
    return Check(ok=True, detail="All watered in time")
```

### `@addon.context(kind)`

`def f(user, ref_id, req: ContextRequest) -> str | None`: the agent's
context for a chat opened from this addon's item of `kind`.

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.context("plant")
def plant_context(user: User, ref_id: str, req: ContextRequest) -> Optional[str]:
    """What the agent knows in a chat opened from a plant."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    return f"This chat is about the plant {p['name']}, watered every {p['every_days']} days." if p else None


@addon.context_card("plant")
def plant_card(user: User, ref_id: str) -> Dict[str, Any]:
    """The card shown with that chat; LookupError when the plant is not the user's."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    if p is None:
        raise LookupError(ref_id)
    return {"title": p["name"], "subtitle": f"Every {p['every_days']} days"}


@addon.context_block("plant")
def plant_block(user: User, ref_id: str) -> str:
    """A plant in a few words, for another addon's context."""
    p = store.plant(user, int(ref_id)) if ref_id.isdigit() else None
    return f"Plant: {p['name']}" if p else ""
```

### `@addon.context_card(kind)`

`def f(user, ref_id) -> dict`: the card attached to a chat opened
from this addon's item of `kind`; raise LookupError when it is not theirs.

Example: under `@addon.context`.

### `@addon.context_block(kind)`

`def f(user, ref_id) -> str`: a plain description of this addon's
item of `kind` for another addon's context prompt (e.g. a task's source call).

Example: under `@addon.context`.

### `@addon.provide(key, *, rank=50, source='', label='')`

Register a named capability `key` (sync or async) that core composers
look up through `providers` without importing the addon.

Several addons may provide the same key: they come ranked, lower
`rank` first (ties in the fixed addon order), and a caller takes the
first that answers (`answer`). `source` /
`label`: who answers, when the key is a signal's (core/signals.py:
`"phone"` / `"Dana's phone app"`); default the addon's name and title.

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.provide("showcase.thirsty_count")
def thirsty_count(user: User) -> int:
    """How many of the user's plants need water now; others call it through registry.provider."""
    return len(store.due(user, clock.now()))
```

### `@addon.widget(key, *, title, script, sizes=('small', 'medium', 'large'), settings=(), description='', icon='', refresh=60)`

`def f(user, ctx: WidgetContext) -> dict` (or async): the data of this
addon's dashboard widget `key` (unique across addons) for `user`, as
JSON. Offered on the dashboards of users who have the addon; on one of a
user who does not, it answers `not_connected`. `script`: the file of
the addon's `web/` folder that draws it, registering with
`Dana.widget(key, …)` (static/dana.js). `sizes`: the sizes it can be
drawn at, of `small` / `medium` / `large` (the first is its
default). `settings`: what each placed widget lets the user choose
(`Field`; values are text, the default used while one is
empty). `refresh`: seconds between its data refreshes on a dashboard
(0: only when the page loads or its script asks).

From [`examples/addons/showcase/now.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/now.py):

```python
@addon.widget("plants_thirsty", title="Thirsty plants", script="widget.js", icon="sprout", refresh=300,
              sizes=("small", "medium"),
              settings=(Field("heading", "The words above the list", default="Needs water", required=False,
                              label="Heading", placeholder="Needs water"),),
              description="The plants that need water now")
def thirsty_widget(user: User, ctx: WidgetContext) -> Dict[str, Any]:
    """The widget's data for one dashboard: its heading (a setting) and the thirsty plants (fewer when small)."""
    names = [p["name"] for p in store.due(user, ctx.now)]
    return {"heading": ctx.settings["heading"], "plants": names[:3] if ctx.size == "small" else names}
```

From [`examples/addons/showcase/web/widget.js`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/web/widget.js):

```js
(() => {
  "use strict";
  Dana.widget("plants_thirsty", {
    mount(el, w, api) {
      const { heading, plants } = w.data;
      el.innerHTML = `<div class="dw-sub">${api.esc(heading)}</div>` + (plants.length
        ? `<ul class="dw-list">${plants.map((n) => `<li class="dw-item">${api.esc(n)}</li>`).join("")}</ul>`
        : `<div class="dw-empty">All watered</div>`);
    },
  });
})();
```

## What hooks get and return

From `core.addons` (`core/addons/hooks.py`); `to_json()` gives the payload clients read.

### Card

A card in the Now queue (docs/now.md). `id` is `<kind prefix>:<id>`;
the prefix routes `POST /api/now/act` to the addon's `@addon.now_action`.
`primary` / `secondary` are actions (`core.cards.action`). Set `why=None`
to omit the key from the payload; `extra` adds top-level keys.

| Field | Type | Default |
|---|---|---|
| `id` | str | required |
| `kind` | str | required |
| `source` | str | required |
| `title` | str | required |
| `why` | str or None | `''` |
| `primary` | dict of str → any or None | `None` |
| `secondary` | list of dict of str → any | `[]` |
| `schedulable` | bool | `True` |
| `ref` | dict of str → any or None | `None` |
| `blocks` | list of dict of str → any | `[]` |
| `extra` | dict of str → any | `{}` |

### DayItem

An item of the Now day strip: at an hour today (`at`), or in the
`home` / `wake` slot (`slot`); `fields` are extra keys of its payload
(`{"task_id": ...}`).

| Field | Type | Default |
|---|---|---|
| `title` | str | required |
| `kind` | str | required |
| `at` | datetime or None | `None` |
| `slot` | str | `''` |
| `fields` | dict of str → any | `{}` |

### NowContext

What a Now hook gets besides the user: the moment, the cards the user
hid (`now_hidden`: item id → row) and today's calendar events (None:
not read).

| Field | Type | Default |
|---|---|---|
| `now` | datetime | required |
| `hidden` | dict of str → any | required |
| `events` | list of dict of str → any or None | `None` |

### BriefingContext

What `@addon.briefing` and `@addon.review` get besides the user: the moment.

| Field | Type | Default |
|---|---|---|
| `now` | datetime | required |

### Section

An addon's part of a briefing (`@addon.briefing`) or weekly review
(`@addon.review`), stored under the key its decorator declares.

`data`: what goes in the stored briefing / review data (and the model's
prompt). Briefing: `points` are the plain lines used when the model is
not, `headline` is added to the plain headline, `counts` go to the
phone push (only the keys the decorator declares). Weekly review:
`items` are things to decide (each with `ref`, `type`, `title`,
`why`, `facts`), acted on by the addon's `@addon.review_action(type)`.

| Field | Type | Default |
|---|---|---|
| `data` | any | `None` |
| `points` | list of str | `[]` |
| `headline` | str | `''` |
| `counts` | dict of str → int | `{}` |
| `items` | list of dict of str → any | `[]` |

### Hit

A global-search result; `score` orders results (higher first),
`extra` are more keys of its payload (`{"project_id": ...}`).

| Field | Type | Default |
|---|---|---|
| `kind` | str | required |
| `id` | str | required |
| `title` | str | required |
| `subtitle` | str | `''` |
| `score` | float | `0.5` |
| `extra` | dict of str → any | `{}` |

### Check

A self-check line (`/api/checks`; a failing one is a Now card).
`title` / `why` / `blocks` describe the card when `ok` is False.

| Field | Type | Default |
|---|---|---|
| `ok` | bool | required |
| `detail` | str | required |
| `since` | datetime or None | `None` |
| `title` | str | `''` |
| `why` | str | `''` |
| `blocks` | list of dict of str → any | `[]` |

### ContextRequest

What `@addon.context` gets besides the user and item id: ``block(kind,
ref_id)`` gives another item's description for the prompt (a task's source
call), "" when there is none.

| Field | Type | Default |
|---|---|---|
| `block` | function | required |

### Approval

The words of an Approve card (Telegram, the app, the web): the action
as a sentence (`title`), the header (`heading` + `emoji`), the
content (`body` under `body_label`), other details (`facts`: label,
value; empty values are left out) and the `question`.

| Field | Type | Default |
|---|---|---|
| `title` | str | required |
| `heading` | str | required |
| `emoji` | str | required |
| `question` | str | required |
| `body_label` | str | `''` |
| `body` | any | `''` |
| `facts` | list of list of str | `()` |
| `fact_limit` | int | `300` |

### WidgetContext

What a dashboard widget's function gets besides the user (`@addon.widget`):
this widget's `settings` on the dashboard (every setting it declares, the
default filled in where the dashboard has none), the `size` it is drawn at
and the moment `now` (aware, the user's zone).

| Field | Type | Default |
|---|---|---|
| `settings` | dict of str → any | required |
| `size` | str | required |
| `now` | datetime | required |

## More examples

### Settings and credentials

Declared on the manifest (`settings=`, `credentials=`); a tool reads them from `conn.settings` / `conn.credentials` (decrypted only for the connection's own user). Secrets go in `credentials` only: never in code, settings, env or logs.

See the manifest's `credentials=` and `settings=` above, and `Field` under Manifest values.

### Calling a service over HTTP

Every host it calls is in `touches=Touches(hosts=...)`; an env value it reads is a `Need("env", ...)` (`bin/dana addons check` compares both with the code).

From [`examples/addons/showcase/service.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/service.py):

```python
BASE_URL = "https://api.plants.example"


class ServiceError(RuntimeError):
    """The plant service refused or did not answer; the message says so in plain words."""


async def tip(conn: Connection, plant: str) -> str:
    """The service's care tip for ``plant``, asked with the user's key."""
    base = os.getenv("SHOWCASE_API_URL", BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {conn.credentials.get('api_key') or ''}"}
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as session, \
                session.get(f"{base}/tips", params={"plant": plant}, headers=headers) as r:
            if r.status == 401:
                raise ServiceError("The plant service refused the API key.")
            r.raise_for_status()
            return str((await r.json())["tip"])
    except ClientError as e:
        raise ServiceError(f"The plant service did not answer ({type(e).__name__}).") from e
```

### Its own tables

`migrations/m0001_<name>.py`, `m0002_…` (numbered per addon, never edited once shipped), and queries that always name the user (`UserTable` does).

From [`examples/addons/showcase/migrations/m0001_baseline.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/migrations/m0001_baseline.py):

```python
VERSION = 1
NAME = "baseline"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE showcase_plants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        every_days INTEGER NOT NULL,
        last_watered TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_showcase_plants_user ON showcase_plants(user_id)")
    conn.execute("""CREATE TABLE showcase_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
```

From [`examples/addons/showcase/store.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/store.py):

```python
PLANTS = UserTable("showcase_plants")
INBOX = UserTable("showcase_inbox")


def add_plant(user: User, name: str, every_days: int) -> int:
    """Remember a plant of ``user``; returns its id."""
    return int(PLANTS.insert(user, {"name": name, "every_days": every_days,
                                    "created_at": clock.now().isoformat(timespec="seconds")}) or 0)


def plants(user: User) -> List[Dict[str, Any]]:
    """The user's plants, oldest first."""
    return PLANTS.list(user, order="id")
```

### A route

`api.py` with `setup(app)`, behind the same sign-in as every route.

From [`examples/addons/showcase/api.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/api.py):

```python
async def get_plants(request: web.Request) -> web.Response:
    user = request_user(request)
    conn = addon_store.require(user, "showcase")  # NotConnected → 403 not_connected in the middleware
    return web.json_response({"room": conn.settings.get("room"),
                              "plants": [{"id": p["id"], "name": p["name"]} for p in store.plants(user)],
                              "inbox": store.inbox(user)})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/showcase/plants", get_plants)
```

### A web page

`web=(Page(id, panel, style, nav),)` on the manifest and a script in `web/` that registers with `Dana.panel` (the page API: server/docs/writing-an-addon.md, "Pages").

From [`examples/addons/showcase/web/plants.js`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/web/plants.js):

```js
(() => {
  "use strict";
  Dana.panel({
    async mount(el, api) {
      if (!el.firstChild) {
        el.innerHTML = `<header class="top-bar"><span class="top-bar-title">Plants</span></header>
          <div class="plants-body"><ul data-plants></ul><ul data-inbox></ul></div>`;
      }
      const { plants, inbox } = await api.call("/api/showcase/plants");
      el.querySelector("[data-plants]").innerHTML = plants.length
        ? plants.map((p) => `<li class="dn-row">${api.esc(p.name)}</li>`).join("")
        : "<li>No plants yet.</li>";
      el.querySelector("[data-inbox]").innerHTML = inbox.map((t) => `<li>${api.esc(t)}</li>`).join("");
    },
  });
})();
```

### A worker (sidecar)

`worker=Worker(port, url_env)` on the manifest and a `worker/` folder with its own `Dockerfile`; Dana reaches it at the URL in `url_env`.

From [`examples/addons/showcase/worker/server.py`](https://github.com/ronganam/dana/blob/master/examples/addons/showcase/worker/server.py):

```python
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — the http.server name
        body = json.dumps({"message": "The showcase worker is up"}).encode()
        self.send_response(200 if self.path == "/ping" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8092"))), Handler).serve_forever()
```

## The manifest as JSON

The manifest's plain values (functions left out) follow this JSON schema, generated from the dataclasses above; `bin/dana addons check` validates a loaded manifest against it.

```json
{
 "$schema": "https://json-schema.org/draft/2020-12/schema",
 "title": "Dana addon manifest",
 "description": "addon = Addon(name, ...) in an addon's addon.py, as JSON (functions left out).",
 "type": "object",
 "properties": {
  "name": {
   "type": "string",
   "pattern": "^[a-z][a-z0-9_]*$",
   "description": "Unique id (``\"homeassistant\"``), the folder's name."
  },
  "title": {
   "type": "string",
   "description": "Its name for people (\"Home Assistant\"); default: the name, capitalised."
  },
  "description": {
   "type": "string",
   "description": "What it does, in a sentence."
  },
  "version": {
   "type": "string",
   "description": "Its own version (\"1.0.0\")."
  },
  "needs": {
   "type": "array",
   "items": {
    "$ref": "#/$defs/Need"
   },
   "description": "What running it takes, a :class:`~core.addons.hooks.Need` each: env values, accounts, hardware, the phone app, a region, other addons (required or ``optional``). The required addon needs are :attr:`depends`: loaded (and their migrations run) before it, on for a user before it is active for them; a site / installed addon whose required addons are missing is not loaded."
  },
  "touches": {
   "$ref": "#/$defs/Touches",
   "description": "What it may touch beyond Dana's own data (:class:`~core.addons.hooks.Touches`: hosts, acting for the user outside Dana, stored credentials, other addons' data). An addon with ``credentials`` must say it stores them."
  },
  "credentials": {
   "type": "array",
   "items": {
    "$ref": "#/$defs/Field"
   },
   "description": "Fields needed to connect, stored encrypted."
  },
  "settings": {
   "type": "array",
   "items": {
    "$ref": "#/$defs/Field"
   },
   "description": "Per-user choices, stored as plain JSON (defaults allowed)."
  },
  "default_on": {
   "type": "boolean",
   "description": "New users get it turned on (it must need no credentials): :func:`core.addons.store.connect_defaults`."
  },
  "everyone": {
   "type": "boolean",
   "description": "Every user has it, with no row and no way to turn it off (the ``essentials`` addon)."
  },
  "requires": {
   "anyOf": [
    {
     "type": "string"
    },
    {
     "type": "null"
    }
   ],
   "description": "An :class:`core.access.AccessLevel` capability the user's level must have to connect it (``\"ops\"``), or None."
  },
  "prompt_parts": {
   "type": "array",
   "items": {
    "$ref": "#/$defs/PromptPart"
   },
   "description": "The addon's pieces of the agent's fixed instructions (:class:`PromptPart`), included only for a user who has the addon (:func:`core.addons.registry.prompt_parts`); the slot must be one of ``core.agent.SLOTS`` (checked when loading)."
  },
  "llm_jobs": {
   "anyOf": [
    {
     "type": "object",
     "additionalProperties": {
      "anyOf": [
       {
        "type": "string"
       },
       {
        "$ref": "#/$defs/LlmJob"
       }
      ]
     }
    },
    {
     "type": "null"
    }
   ],
   "description": "Its LLM purposes and their default model profile (``{\"task_emoji\": \"fast\"}``, or an :class:`~core.addons.hooks.LlmJob` with a label): ``core.llm`` accepts them as ``purpose=``."
  },
  "web": {
   "type": "array",
   "items": {
    "$ref": "#/$defs/Page"
   },
   "description": "Its pages in the web app, any number: a tuple of :class:`~core.addons.hooks.Page` (files served from its ``web/``; page ids unique across addons)."
  },
  "worker": {
   "anyOf": [
    {
     "$ref": "#/$defs/Worker"
    },
    {
     "type": "null"
    }
   ],
   "description": "Its sidecar (:class:`~core.addons.hooks.Worker`): a published image, or built from its ``worker/``."
  },
  "modules": {
   "type": "array",
   "items": {
    "type": "string"
   },
   "description": "More modules of its folder the loader imports after ``addon.py`` and ``tools.py`` (dotted, relative: ``\"calls.tools\"``): where its other tools and hooks are declared. ``hooks.py`` is imported without being named."
  },
  "order": {
   "anyOf": [
    {
     "type": "integer"
    },
    {
     "type": "null"
    }
   ],
   "description": "Its place among the official addons (lower first; others follow by name)."
  },
  "change_requires": {
   "anyOf": [
    {
     "type": "string"
    },
    {
     "type": "null"
    }
   ],
   "description": "A capability the user who *makes* a change needs to connect, update or disconnect the addon, for themselves or anyone (``cameras``: ``manage_users``, so an admin decides who sees which cameras). Existing connections keep working for their user."
  },
  "connect_url": {
   "anyOf": [
    {
     "type": "string"
    },
    {
     "type": "null"
    }
   ],
   "description": "A path of this server that connects the addon by a sign-in at the service instead of typed credentials (the Google addons: ``/api/google/connect``); clients offer it as a button."
  },
  "connect_label": {
   "type": "string",
   "description": "That button's words (\"Connect Telegram\"); empty: the Google sign-in's."
  },
  "realtime": {
   "type": "boolean",
   "description": "False keeps the addon's tools out of realtime voice calls, which have no Approve card and no rule for untrusted content (``gmail``): there they are neither offered nor run."
  },
  "icon": {
   "type": "string",
   "pattern": "^$|^[a-z0-9]+(-[a-z0-9]+)*$",
   "description": "Its icon on the Addons page: a Lucide icon name (``\"house\"``)."
  },
  "features": {
   "type": "array",
   "items": {
    "type": "string"
   },
   "description": "What it adds for the user, a short line each in plain words (\"Turn lights and devices on and off\"); empty: the first line of each tool's docstring."
  },
  "setup": {
   "type": "array",
   "items": {
    "type": "string"
   },
   "description": "Steps done outside Dana before connecting, in order, one sentence each (\"Install the WAHA server\", \"Scan its QR code with WhatsApp\"); the Addons page numbers them before its own steps."
  },
  "examples": {
   "type": "array",
   "items": {
    "type": "string"
   },
   "description": "Things to say to Dana once it is on (\"Turn off the living room lights\"): shown after connecting."
  },
  "disconnect_warning": {
   "type": "string",
   "description": "What disconnecting deletes, in one sentence (``money``: the logins and transactions); empty: nothing is deleted."
  }
 },
 "required": [
  "name"
 ],
 "additionalProperties": false,
 "$defs": {
  "Field": {
   "type": "object",
   "description": "One credential or setting of an addon.",
   "properties": {
    "name": {
     "type": "string"
    },
    "description": {
     "type": "string"
    },
    "default": {},
    "required": {
     "type": "boolean"
    },
    "type": {
     "type": "string",
     "enum": [
      "text",
      "address",
      "telegram_chat",
      "name"
     ]
    },
    "label": {
     "type": "string"
    },
    "help": {
     "type": "string"
    },
    "url": {
     "type": "string"
    },
    "placeholder": {
     "type": "string"
    }
   },
   "required": [
    "name"
   ],
   "additionalProperties": false
  },
  "LlmJob": {
   "type": "object",
   "description": "An LLM purpose of the addon: its default model profile (``fast`` / ``balanced`` / ``deep``), how the Usage screen names it (\"\" = from the purpose's name), and whether it is ``background`` work Dana does on its own (the monthly budget pauses it; core/budget.py) rather than something a person asked for.",
   "properties": {
    "profile": {
     "type": "string",
     "enum": [
      "fast",
      "balanced",
      "deep"
     ]
    },
    "label": {
     "type": "string"
    },
    "background": {
     "type": "boolean"
    }
   },
   "required": [
    "profile"
   ],
   "additionalProperties": false
  },
  "Nav": {
   "type": "object",
   "description": "The addon's entry in the web app's navigation: ``order`` places it among the others, ``primary`` asks for a place in the default navigation bar (Chat and up to three such pages; each user may choose their own: docs/web-design.md, \"Navigation\").",
   "properties": {
    "label": {
     "type": "string"
    },
    "icon": {
     "type": "string"
    },
    "order": {
     "type": "integer"
    },
    "primary": {
     "type": "boolean"
    }
   },
   "required": [
    "label"
   ],
   "additionalProperties": false
  },
  "Need": {
   "type": "object",
   "description": "Something the addon needs from whoever runs it, shown before it is turned on (the Addons page, ``bin/dana addons table``, the README).",
   "properties": {
    "kind": {
     "type": "string",
     "enum": [
      "env",
      "account",
      "hardware",
      "app",
      "region",
      "addon"
     ]
    },
    "what": {
     "type": "string"
    },
    "why": {
     "type": "string"
    },
    "optional": {
     "type": "boolean"
    }
   },
   "required": [
    "kind",
    "what",
    "why"
   ],
   "additionalProperties": false
  },
  "Page": {
   "type": "object",
   "description": "A page of the addon in the web app: ``id`` names the screen (its ``#panel-<id>`` element, ``/<id>`` and ``#<id>`` open it; unique across addons, not a core screen's), ``panel`` (JavaScript) and ``style`` (CSS) are paths inside the addon's ``web/`` folder, served at ``/addons/<name>/web/`` and listed by ``GET /api/addons`` (core/addons_api.py); ``nav`` puts a button in the navigation (None: the page opens only from a link or another page).",
   "properties": {
    "id": {
     "type": "string",
     "pattern": "^[a-z][a-z0-9_-]*$"
    },
    "panel": {
     "type": "string"
    },
    "style": {
     "type": "string"
    },
    "nav": {
     "anyOf": [
      {
       "$ref": "#/$defs/Nav"
      },
      {
       "type": "null"
      }
     ]
    }
   },
   "required": [
    "id",
    "panel"
   ],
   "additionalProperties": false
  },
  "PromptPart": {
   "type": "object",
   "description": "A piece of the agent's instructions that applies only to a user who has the addon (and every addon in ``needs``) connected.",
   "properties": {
    "slot": {
     "type": "string"
    },
    "text": {
     "type": "string"
    },
    "rank": {
     "type": "integer"
    },
    "needs": {
     "type": "array",
     "items": {
      "type": "string"
     }
    }
   },
   "required": [
    "slot",
    "text"
   ],
   "additionalProperties": false
  },
  "Touches": {
   "type": "object",
   "description": "What the addon may touch beyond Dana's own data, shown before it is turned on and before it is installed: the network ``hosts`` it contacts (a domain, or words for an address the user gives: \"your Home Assistant server\"), whether it ``acts_outside`` (sends messages or mail, changes things at a service or on devices for the user), whether it ``stores_credentials`` (passwords, tokens, sign-ins; encrypted), and the other addons whose data it ``reads``. A declaration, not a sandbox: every addon runs with the server's full access (SECURITY.md). Installing or updating an addon whose touches grew asks the admin again (core/addons/install.py).",
   "properties": {
    "hosts": {
     "type": "array",
     "items": {
      "type": "string"
     }
    },
    "acts_outside": {
     "type": "boolean"
    },
    "stores_credentials": {
     "type": "boolean"
    },
    "reads": {
     "type": "array",
     "items": {
      "type": "string"
     }
    }
   },
   "required": [],
   "additionalProperties": false
  },
  "Worker": {
   "type": "object",
   "description": "A sidecar process next to Dana: the port it listens on and the env variable that tells Dana its URL. ``image``: the published image it runs (``ghcr.io/<owner>/<name>``, its repository building it; a tag of its own, else ``bin/deploy`` pins it to the installed commit), the way for an addon installed from the catalog; empty: ``bin/deploy`` builds the addon's ``worker/`` folder (a ``Dockerfile``) as ``<image>-<addon>``, the way for a site addon. ``compose.yaml`` runs it under a profile named after the addon (docs/operations.md, \"Workers\").",
   "properties": {
    "port": {
     "type": "integer"
    },
    "url_env": {
     "type": "string"
    },
    "image": {
     "type": "string"
    }
   },
   "required": [
    "port",
    "url_env"
   ],
   "additionalProperties": false
  }
 }
}
```
