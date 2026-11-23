# Build a Dana add-on: a guide for your coding agent

You are a coding agent (Claude Code, Codex, Cursor or another) helping a person build an **add-on** for
**Dana**, their self-hosted personal assistant, end to end: from an idea in their words to the add-on
running on their own Dana. The person may not be a programmer. You interview them, propose a design,
build and test it, install it on their server, and try it with them.

An add-on (an *addon* in the code) is its own folder and git repository: an `addon.py` manifest and
typed Python functions with docstrings that Dana offers to its model as tools, plus optional hooks for
the Now screen, the morning briefing, background jobs, events, notifications and a page in the web app.
It is the one way to extend Dana: official add-ons ship with Dana, and every other one is **installed**
into the server's data folder, from the catalog, from a git repository at a full commit, or from a
folder on the person's computer.

Source of truth: <https://github.com/ronganam/dana>. When this guide and the code disagree, the code
wins.

## Rules

- **Ask instead of guessing.** One question at a time, with choices and a recommended answer.
- **Secrets only in credentials fields.** An API key, password or token is a `Field` in
  `credentials=` (stored encrypted, per user; then `stores_credentials=True`). Never in code,
  settings, env values, logs, tests, commits or the chat. Never ask the person to paste one to you:
  they type it on the Addons page.
- **Declare everything it touches.** Every host the code calls is in `Touches(hosts=...)`, every env
  value it reads is a `Need("env", ...)`, sending or changing things for the person outside Dana is
  `acts_outside=True`, another add-on's data it reads is in `reads=`. `bin/dana addons check` fails
  when the code does more than the manifest says.
- **Never put the person's real data in tests**: no real names, numbers, addresses, accounts, keys
  or money. The harness's made-up users are Robin, Gal and Maya.
- **Keep tools small**, one job each, with a docstring whose first line says what it does and an
  `Args:` line per parameter (the model reads only that).
- Every tool and hook runs for one user; another user's data answers like missing data.
- Stop and ask when a step fails twice; show the error and what you tried.

## 1. Interview

Ask these one at a time. Offer choices, recommend one, and accept "you decide". Write the answers down.

1. **What should Dana be able to do?** One sentence, and **two things the person would say** to Dana
   once it works ("When's the next bus to work?", "Remind me when the library book is due"). These two
   become the add-on's `examples=` and the final test.
2. **Which service or data does it use?** A website's API (ask for its docs URL), a device or server at
   home, a file, or nothing outside Dana. Does it need an account or an API key? If so: **one per
   person** (each household member enters their own: a credentials field), or **one for the whole
   household** (the server admin sets it once: an env value, `Need("env", ...)`)? *Recommended: per
   person, unless the service only gives one key.*
3. **Does it only read, or does it act for them?** Reading (look things up, show, summarise) is
   simple. Sending, buying, booking, changing things at a service is `acts_outside=True`, and the tool
   should ask first (`confirm=True`: the person sees an Approve card) or at least be undoable
   (`is_write=True`). *Recommended: read-only first.*
4. **When does it act on its own?** Never (only when asked) / on a schedule (every N minutes, daily at
   a time: a job) / when something happens in Dana (an event, e.g. a task was created).
   *Recommended: only when asked, unless the idea needs more.*
5. **Where should it show up?** Answers in chat and voice (always) / a card on the Now screen when
   something needs them / a section of the morning briefing / a page in the web app / a push
   notification.
6. **Who in the household gets it?** Each person turns an add-on on for themselves on the Addons page;
   ask whether everyone will, or one person, and whether it should be on by default for new users
   (`default_on=True`, only when it needs no account).
7. **Region and language**: does it only work in one country, and which languages will they speak to
   it in? (A region is a `Need("region", ...)`.)

## 2. Propose the design and wait for a yes

Say it back in plain words first (what it does, what it asks for, what it may touch, when it acts),
then as the manifest, for example:

```python
addon = Addon(
    "bus_times", title="Bus times", version="0.1.0", icon="bus",
    description="The next buses at your stops, in chat and on Now",
    features=("The next buses at a stop", "A Now card when your usual bus is late"),
    examples=("When's the next bus to work?", "Is the 18 late?"),
    setup=("Make a free account at transit.example and create an API key.",),
    needs=(Need("account", "a transit.example account", "Its API gives the live arrivals"),),
    touches=Touches(hosts=("api.transit.example",), stores_credentials=True),
    credentials=(Field("api_key", "transit.example API key", label="API key",
                       help="On transit.example: Account → API keys → copy it.",
                       url="https://transit.example/account/keys"),),
    settings=(Field("home_stop", "Your usual stop", label="Home stop"),),
)
```

plus the tools (names, one line each, read or write), the hooks (a job and when, a Now card and when
it shows) and the page if any. Wait for a clear yes; change the design until they say it.

## 3. Build it

### A Dana checkout

The add-on kit runs from a Dana checkout with its Python environment (Python 3.12 or newer). If there
is none yet (ask where to put it; *recommended `~/dana`*):

```bash
git clone https://github.com/ronganam/dana ~/dana
cd ~/dana/server
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

Use that environment for every `bin/dana` command below: `source ~/dana/server/.venv/bin/activate`
in the shell first, then call `~/dana/server/bin/dana` from the add-on's parent folder (`new` writes
into the current folder). `bin/dana addons dev` also needs Docker.

### Start from the scaffold

In the folder where the person keeps code (ask; *recommended `~/code`*):

```bash
cd ~/code
~/dana/server/bin/dana addons new bus_times              # writes ./bus_times, its own git repository
~/dana/server/bin/dana addons new bus_times --web        # also a page in the web app and its route
```

`--title "Bus times"` names it for people; `--worker` adds a sidecar container (only when the add-on
needs a long-running process or a dependency Dana cannot import). The name is lowercase with
underscores.

**Before writing code, read the scaffold's `AGENTS.md` and `REFERENCE.md`.** `AGENTS.md` is the loop
and the rules for this add-on; `REFERENCE.md` lists every manifest field and every hook, each with a
working, tested example. Dana's `server/docs/writing-an-addon.md` is the guide, and
`examples/addons/showcase/` uses every capability once.

### The loop

Work in the loop the scaffold's `AGENTS.md` describes, in small steps:

1. **Edit**: turn the scaffold's example tool, setting and Now card into the design. Remove what the
   design does not use.
2. **Check**: `~/dana/server/bin/dana addons check ./bus_times`. Fix every error it prints; each comes with its fix.
3. **Test**: `~/dana/server/bin/dana addons test ./bus_times`. The tests run against Dana's harness with a fresh
   database, made-up users (Robin and Gal have the add-on, Maya does not, and is refused), a fake
   model and a fake HTTP server standing in for the service: no network, no real keys. Add a test for
   every behaviour, asserting on data, including the two example requests' tools.
4. **Try it**: `~/dana/server/bin/dana addons dev ./bus_times` starts Dana's local test environment (Docker, stub
   services, a fake model) with the add-on loaded and prints its log, its tools and its Now cards; it
   reloads when a file changes. Show the person the page or the card when there is one.

Commit in the add-on's repository as you go, with clear messages.

## 4. Install it on their Dana

The person's Dana must be reachable from this computer, and installing needs an **admin token**. They
get one on the server (`dana users tokens issue <their name> --label add-ons`, printed once; with
Docker Compose: `docker compose exec dana dana users tokens issue <their name> --label add-ons`). The
command reads it from `DANA_TOKEN` or asks for it; do not ask them to paste it into the chat. Let them
run the command themselves when the token prompt would show it to you.

```bash
~/dana/server/bin/dana addons install ./bus_times --server https://their-dana.example --restart
```

It shows what the add-on may touch and asks before installing (`--yes` agrees without asking: only
when the person already agreed), copies it into the server's data folder (kept across upgrades and in
backups) and, with `--restart`, restarts the server so it loads.

Then, in the web app: **Settings → Addons**, open the add-on, follow its setup steps (the credentials
fields, Connect, which runs the add-on's connection check), and it is on for that person. Each
household member who wants it turns it on for themselves.

Finally **try the two example requests** from the interview in Dana's chat with the person. If an
answer is wrong: fix, check, test, and run the install command again.

## 5. Share it (only if they want)

Ask whether they want to share it with other people running Dana. If yes:

```bash
~/dana/server/bin/dana addons publish ./bus_times     # --dry-run first prints every git / gh command
```

It runs check and test, pushes the add-on to a GitHub repository (with `gh`, signed in as them),
tags a release and opens a pull request adding it to the community catalog
([dana-catalog](https://github.com/ronganam/dana-catalog)), pinned to that commit. Make sure the
README says what it does, its settings and what it touches, and that nothing personal is in the
repository's history before publishing.

End by telling the person what the add-on does, where its code lives, how to change it (edit, check,
test, install again) and that they can turn it off on the Addons page at any time.
