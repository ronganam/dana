# Working fast

How to iterate on Dana quickly without losing quality. It covers the inner loop, how to ship, how to plan a big change, and how to split work across agents and brief them.

## The loop

| Step | Command | Time |
|---|---|---|
| While editing | `bin/test --changed` (only the scripts touched by, or importing, what changed) or `bin/test <word>` | seconds |
| Before a commit | `bin/test` (everything, side by side; prints only failures) | ~1.5 min |
| On push to master | the pre-push hook runs `bin/test` (and the image's test stage when the Dockerfile or requirements change), then `bin/deploy` builds and deploys (when the site has a `deploy.env`), all on your machine | ~2.5 min + rollout |

- Don't run the suite "to see where we are" at the start of a task. The last commit passed the suite, so a failure you see is yours.
- A script that fails once in parallel and passes alone is reported as **flaky**, not failed. Look at it when it happens often, but it doesn't block.
- `bin/test` runs one script per CPU, fewer when free memory is short (about 400 MB each). With emulators, Gradle or a Docker build running, don't start a second suite (a local `bin/test` and `docker build --target test` at once); run them one after the other.
- `bin/test --list` says what each script covers. Read it to find the script for an area instead of searching the code.
- **Running the real server:**
  1. Run `DANA_LOCAL_SOURCE=1 bin/local-env.sh up` once. It starts the real image against stub services, with your working tree mounted.
  2. After changing Python code, run `bin/local-env.sh restart` (a few seconds, no rebuild). Static files take effect when the browser reloads.
  3. Rebuild only after changing `requirements.lock` or the `Dockerfile`: `bin/local-env.sh build`.
  4. The end-to-end rehearsal is `bin/local-env.sh test`. See `docs/local-testing.md`.
- **A migration or anything touching production data:** rehearse it on a copy of the production database first, with `DANA_LOCAL_DB=<copy> bin/local-env.sh up` (`docs/local-testing.md`, "Against a copy of a real database"). Keep the copy outside the repo and delete it afterwards.

## Shipping small

- Every push to master that changes the app deploys, as `<VERSION>-<commit>`, with no version bump (`docs/operations.md`). The pre-push test gate makes that safe. Several small pushes beat one big release: each is easier to verify, and a problem points at fewer changes. A push of only docs or tests deploys nothing.
- New work can go live for one user first. An addon is used only by users who have it connected, so a new addon or a new tool can run for the admin for a few days before anyone else gets it (`bin/users.py addons connect`).
- After a deploy, verify with the self-checks (`/api/checks`) and the log, not by reading code (`docs/operations.md`).

## Before a big change

- **Remove what's unused first.** Before refactoring an area, look at how much production uses it (`usage_events`, its own tables) and decide what to remove or remake. Converting something and then deleting it the next day doubles the work.
- **Plan on one page in plain words.** Give an example for each idea and ask all the open decisions at once, with options and a recommendation. Dense plans lead to rounds of "what does this mean?".
- **Decide the order by what depends on what,** not by area. Foundations go first and one after another. Everything that only builds on them can go in parallel (next section).

## Splitting work across agents

A cold agent costs a few minutes to get oriented, and most of its time goes to writing code and tests. Most of the time saved comes from running independent tasks side by side.

**Shape of the work**
1. **Foundations go one after another**: the schema and identity, the framework, enforcement. Each builds on the one before.
2. **Do the first slice of a repeated pattern alone,** for example the first kind converted or the first client built. Give it one deep, independent review, then turn the findings into rules in the brief. Every later slice then starts with them, instead of a review finding the same hole in eight places at the end.
3. **Run the remaining slices in parallel waves of 3–4,** each agent in its own git worktree on its own branch. Clients (web, Android, watch, Raycast) can run together once the server API is fixed and its payload fixture is captured (`tests/fixtures/*_web.json`).
4. **The orchestrator merges each wave,** applies the shared-doc notes, runs `bin/test` once, and starts the next wave. It does not re-run each agent's checks; it reads the reports and spot-checks what looks off.
5. **Final review**: one independent review of the whole diff against `docs/access.md` (Security rules).

**Keeping parallel branches from colliding**
- **Migration numbers**: numbers are per addon (`addons/<name>/migrations/`, core's in `core/db/migrations/`); two agents adding to the same addon get their numbers in the brief (`m0002_…` for one, `m0003_…` for the next). Discovery refuses gaps and repeats, so a mistake fails at start, not in production.
- **Shared docs** (`AGENTS.md`, `docs/access.md`, `docs/operations.md`): agents change their own area's doc. Changes to a shared doc go in the report as notes, and the orchestrator applies them once per wave.
- **Tests**: one script per area (`test_<area>.py`). There is no list to update; its docstring is the description.
- **Fixtures** (`tests/fixtures/parity/`, `golden.sse`, `*_web.json`): only the agent whose task changes them regenerates them, and the brief names who that is.

**Effort**: use high reasoning for design, frameworks and anything security-related. Use medium for applying an established pattern to one more area, and for client work against a fixed API.

## The brief

Each agent gets the common rules below plus a task part. Keep the task part short and concrete: an agent that has to guess re-derives what the orchestrator already knew.

**Task part**: the goal in two sentences; the area and files; what was decided and must not be reopened; the migration number, if any; which fixtures it may regenerate; what the next agents need from it (payload examples, helper signatures).

**Common rules** (paste as is):

```
Repo: <the repository> (server in server/, Android in android/), your own worktree / branch <branch>.
Do the work yourself; do not start sub-agents.

Read AGENTS.md, then only the doc sections for your area (docs/development.md for how
to work). `bin/test --list` tells you which test script covers what.

Hard rules
- Use server/.venv/bin/python (a worktree has none: use the main checkout's). Never print, log or commit secrets; do not cat .env.
- No production systems (no kubectl, no real Home Assistant / WAHA / Google / Frigate),
  no git push, no deploy, no VERSION or app version bump.
- No legacy code, compatibility shims or re-export modules: replace, update every
  caller, delete the old path with its tests and docs. No new dependencies (ask in
  your report if one is needed, and do without it).
- Follow AGENTS.md "Key conventions" (core/llm.py with purpose=, core.clock, core/paths.py,
  schema only by a new migration with the number you were given, tools through the
  addons framework, sharing through core/addons/sharing.py, every
  query scoped to the acting user: another user's id answers like a missing one).
- Payloads other clients depend on change only where your task says so.
- Typed public functions, short docstrings that say what, no import inside a function
  to dodge a circular import. Leave unrelated changes alone.

Tests
- While working: bin/test --changed (or bin/test <word>). At the end: bin/test, once.
  Do not run the suite before you start; the base passed.
- New behaviour gets checks that assert on data. Per-user features: two users with the
  kind plus one without (isolation both ways, 403 not_connected).

Docs: your area's doc in the same commit, describing what is built (no history).
Changes needed in AGENTS.md / docs/access.md / docs/operations.md: put them in your
report as notes; the orchestrator applies them.

Commit: one commit on your branch, a clear message and short body, ending with
Co-Authored-By: <the model's trailer>

Report (factual, short): what was built / removed (one line per file); bin/test result
(exact output of any failure; do not claim a pass you did not run); every deviation
from the brief and why; the shared-doc notes; what the next agents must know; the
commit hash.
```
