# Dana — Agent Guide

**Dana** is a self-hosted personal assistant: a Python server (agent loop, integrations, web UI, Telegram) and Android clients (phone, wall-tablet kiosk, TV, Wear OS watch). This file is the map of the repository; each part has its own guide. Read the one for the part you change before changing it.

| Path | What | Guide |
|---|---|---|
| `server/` | aiohttp server: the ReAct agent over LiteLLM, integrations (each kind owns its tools), REST/SSE/WebSocket APIs, the web UI (`static/`), Telegram, background pipelines, the bank-worker sidecar (`bank-worker/`) | `server/AGENTS.md`, `server/docs/` |
| `android/` | Gradle project: `:app` (flavors `phone`, `kiosk`, `tv`), `:wear` (watch), `:watchface` | `android/AGENTS.md`, `android/docs/` |
| `extensions/raycast-assistant/` | Raycast extension (macOS) | its `README.md` |
| `.githooks/pre-push` | Pushing master runs the server tests, then deploys when `server/deploy.env` exists (`server/docs/operations.md`) | |
| `.github/` | CI (server tests, Android unit tests), issue and PR templates | |

**Maintainer-local notes**: a maintainer's own deployment (servers, secret store, devices, accounts) never goes in the repository. When `AGENTS.local.md` exists at the root, read it before production or device work; it is gitignored.

## Working across the parts

- **Payloads**: the Android apps, the web UI and the Raycast extension read the server's payloads. A change that needs a client too lands in the same commit, with both sides' tests and docs.
- **Tests**: server `cd server && bin/test` (`bin/test --changed` while working); Android `cd android && ./gradlew :app:testPhoneDebugUnitTest :wear:testDebugUnitTest` (always name the module and flavor). Shared web fixtures (`server/test_fixtures/*_web.json`) are copied into `android/app/src/test/resources/` when a payload changes.
- **Docs** stay current in the same commit as the code; a new area gets a doc and one line in its part's guide.
- **Nothing personal in the repository**: no real names, phone numbers, addresses, domains, device serials, account ids or real money data in code, tests, fixtures or docs. Tests use made-up people (`server/testenv.py`: Robin, Sam, Maya…). A person's own setup lives in gitignored files: `server/.env`, `server/deploy.env`, plugin folders outside the tree (`server/docs/plugins.md`), `android/local.properties`, `android/local.env`, `android/tv-defaults.json`, `android/app/src/phone/google-services.json`, `android/keystore.properties`, `AGENTS.local.md`.
- **Old internal identifiers stay**: the project used to be called `ganam-assistant`, and ids that live on devices and in databases keep that prefix: the Android `applicationId` / package `app.ganam.assistant` (a new id would be a different app, without updates or data), `GanamPrefs`, the web cookie `ganam_session` and `localStorage` keys `ganam_*`, the notification triage answer keys `ron_wrote_it` / `addressed_to_ron` (stored with each notification). Don't rename them; user-facing text says Dana.

## Setup (once per clone)

```bash
git config core.hooksPath .githooks
cd server && python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp .env.example .env
```

Contributor docs for people: `README.md`, `CONTRIBUTING.md`.
