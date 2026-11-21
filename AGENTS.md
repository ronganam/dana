# Dana — Agent Guide

**Dana** is a self-hosted personal assistant: a Python server (agent loop, addons, web UI, Telegram) and Android clients (phone, wall-tablet kiosk, TV, Wear OS watch). This file is the map of the repository; each part has its own guide. Read the one for the part you change before changing it.

| Path | What | Guide |
|---|---|---|
| `server/` | aiohttp server: the ReAct agent over LiteLLM, addons (each owns its tools; `docs/addons.md`), REST/SSE/WebSocket APIs, the web UI (`static/`), Telegram, background pipelines, the bank-worker sidecar (`bank-worker/`) | `server/AGENTS.md`, `server/docs/` |
| `android/` | Gradle project: `:app` (flavors `phone`, `kiosk`, `tv`), `:wear` (watch), `:watchface` | `android/AGENTS.md`, `android/docs/` |
| `extensions/raycast-assistant/` | Raycast extension (macOS) | its `README.md` |
| `examples/addons/hello/` | The example addon, loaded as a site addon by `server/tests/test_addon_loading.py` | `server/docs/writing-an-addon.md` |
| `site.example/` | The template of a deployment's **site** folder: `site/` (gitignored, often a link to a private repository) holds one deployment's config, site addons, app defaults and notes | `docs/site.md` |
| `.githooks/pre-push` | Pushing master runs the server tests, then deploys when `site/deploy.env` exists (`server/docs/operations.md`) | |
| `.github/` | CI (server tests, Android unit tests), issue and PR templates | |

**This deployment**: when `site/AGENTS.md` exists, read it first. It describes the deployment this checkout serves (production, secret store, devices, site addons). Change a deployment through its site (config, site addons), and Dana's code only in ways every deployment gets (`docs/site.md`). Never copy a site's details into the repository.

## Working across the parts

- **Payloads**: the Android apps, the web UI and the Raycast extension read the server's payloads. A change that needs a client too lands in the same commit, with both sides' tests and docs.
- **Tests**: server `cd server && bin/test` (`bin/test --changed` while working); Android `cd android && ./gradlew :app:testPhoneDebugUnitTest :wear:testDebugUnitTest` (always name the module and flavor). Shared web fixtures (`server/tests/fixtures/*_web.json`) are copied into `android/app/src/test/resources/` when a payload changes.
- **Keep deployments on upstream**: anything one deployment needs goes in its site (`site/addons/` for its own addons), or becomes a setting or extension point that every deployment gets. Never a fork of Dana's code (`docs/site.md`). The shipped example addon (`examples/addons/hello/`) is tested; keep it working when the framework changes.
- **Docs** stay current in the same commit as the code; a new area gets a doc and one line in its part's guide.
- **Nothing personal in the repository**: no real names, phone numbers, addresses, domains, device serials, account ids or real money data in code, tests, fixtures or docs. Tests use made-up people (`server/tests/testenv.py`: Robin, Gal, Maya…). A deployment's own setup lives in its site (`site/`) and in gitignored secrets: `server/.env`, `android/keystore.properties`, `android/local.properties` (`sdk.dir`).
- **Old internal identifiers stay**: the project used to be called `ganam-assistant`, and ids that live on devices and in databases keep that prefix: the Android `applicationId` / package `app.ganam.assistant` (a new id would be a different app, without updates or data), `GanamPrefs`, the web cookie `ganam_session` and `localStorage` keys `ganam_*`. Don't rename them; user-facing text says Dana.

## Setup (once per clone)

```bash
git config core.hooksPath .githooks
cp -r site.example site      # or link your own site: ln -s ~/my-dana-site site
cd server && python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp .env.example .env
```

Contributor docs for people: `README.md`, `CONTRIBUTING.md`.
