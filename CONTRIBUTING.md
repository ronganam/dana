# Contributing to Dana

Thanks for looking. Dana started as one household's assistant, so the bar for a change is: it works for that household, and it makes Dana easier to use or adapt for others.

## Before you start

- For anything bigger than a fix, open an issue first and say what you want to build; it saves you a rewrite.
- Read the guide of the part you change: [`server/AGENTS.md`](server/AGENTS.md) or [`android/AGENTS.md`](android/AGENTS.md), then the doc for your area (listed at the end of each guide).

## Set up

```bash
git config core.hooksPath .githooks          # pushing master runs the server tests
cp -r site.example site                      # your deployment's folder (docs/site.md); optional for contributing
cd server
python3 -m venv .venv                        # Python 3.12 or newer; the image and the tests run 3.14
.venv/bin/pip install -r requirements.lock
cp .env.example .env                         # ANTHROPIC_API_KEY and API_TOKEN (tests need neither)
```

Android: JDK 21 and the Android SDK; open `android/` in Android Studio or use `./gradlew`.

## Test

```bash
cd server
bin/test --changed      # while working: only what your change touches (seconds)
bin/test                # before you open a pull request (about 2 minutes)
bin/test --list         # what each test script covers

cd android
./gradlew :app:testPhoneDebugUnitTest :wear:testDebugUnitTest
```

- Server tests are standalone scripts (`test_*.py`, `test_*.mjs`), not pytest: core tests in `server/tests/`, each addon's in `server/addons/<name>/tests/`. Each starts with `import testenv; testenv.isolate("<name>")`, which gives it a clean environment and a throwaway database. Assert on data, not just status codes; a per-user feature gets checked with two users and one without it.
- Never put real people, phone numbers, addresses, accounts or money data in tests or fixtures: use made-up ones (`server/tests/testenv.py`).
- `bin/local-env.sh` runs the real image against stub services for end-to-end checks ([`server/docs/local-testing.md`](server/docs/local-testing.md)).

## Conventions

- Keep the docs current in the same pull request as the code.
- Schema changes go through a new migration in `server/core/db/migrations/`; never edit a shipped one.
- A server payload change that a client reads updates that client (and its fixtures) in the same pull request.
- User-facing text calls the assistant Dana; internal identifiers that start with `ganam` stay as they are ([`AGENTS.md`](AGENTS.md)).
- No new dependency without a reason in the pull request.
- Something only your own deployment needs belongs in your site (a site addon, a setting), not in Dana's code; a missing extension point is a welcome pull request ([`docs/site.md`](docs/site.md)).

## Pull requests

One topic per pull request, with a description of what changed and how you checked it. CI runs the server suite and, for Android, the unit tests, the phone and watch debug builds and lint.
