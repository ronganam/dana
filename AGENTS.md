# Dana — Agent Guide

**Dana** is a self-hosted personal assistant: a Python server (agent loop, addons, web UI, Telegram) and Android clients (phone, wall-tablet kiosk, TV, Wear OS watch). This file is the map of the repository; each part has its own guide. Read the one for the part you change before changing it.

| Path | What | Guide |
|---|---|---|
| `server/` | aiohttp server: the ReAct agent over LiteLLM, addons (each owns its tools; `docs/addons.md`), REST/SSE/WebSocket APIs, the web UI (`static/` + addon `web/` panels), Telegram, background pipelines | `server/AGENTS.md`, `server/docs/` |
| `android/` | Gradle project: `:app` (flavors `phone`, `kiosk`, `tv`), `:wear` (watch), `:watchface` (the Dana watch face), `:protocol` (phone↔watch Data Layer contract) | `android/AGENTS.md`, `android/docs/` |
| `extensions/raycast-assistant/` | Raycast extension (macOS) | its `README.md` |
| `examples/addons/hello/` | The example addon, loaded as a site addon by `server/tests/test_addon_loading.py` | `server/docs/writing-an-addon.md` |
| `examples/addons/showcase/` | An addon using every capability once; `server/docs/addon-reference.md` quotes it | `server/docs/writing-an-addon.md` ("The add-on kit") |
| `website/` | The project's one-page site (static, no build) | its `README.md` |
| `site.example/` | The template of a deployment's **site** folder: `site/` (gitignored, often a link to a private repository) holds one deployment's config, site addons, app defaults and notes | `docs/site.md` |
| `.githooks/pre-push` | Pushing master runs the server tests, then deploys when `site/deploy.env` exists (`server/docs/operations.md`) | |
| `.github/` | CI (`workflows/ci.yml`: server tests, Android unit tests / debug builds / lint, Raycast build & lint, Docker image builds; the website on GitHub Pages: `workflows/pages.yml`), releases (`workflows/release.yml`: the published server image `ghcr.io/ronganam/dana`, amd64 and arm64), Dependabot, issue and PR templates | |
| `compose.yaml` | The quick start: runs the published image with no checkout (curl it, `docker compose up -d`, open the setup link from the logs); `server/compose.yaml` builds from source | `server/docs/operations.md` ("Running it") |

**This deployment**: when `site/AGENTS.md` exists, read it first. It describes the deployment this checkout serves (production, secret store, devices, site addons). Change a deployment through its site (config, site addons), and Dana's code only in ways every deployment gets (`docs/site.md`). Never copy a site's details into the repository.

## Working across the parts

- **Payloads**: the Android apps, the web UI and the Raycast extension read the server's payloads. A change that needs a client too lands in the same commit, with both sides' tests and docs.
- **Tests**: server `cd server && bin/test` (`bin/test --changed` while working); Android `cd android && ./gradlew :app:testPhoneDebugUnitTest :wear:testDebugUnitTest` (always name the module and flavor). The Android contract tests read the shared web fixtures (`server/tests/fixtures/*_web.json`, `golden.sse`) straight from the server's folder, so a payload change that breaks the app fails them; CI rejects stale copies under `android/`.
- **Keep deployments on upstream**: anything one deployment needs goes in its site (`site/addons/` for its own addons), or becomes a setting or extension point that every deployment gets. Never a fork of Dana's code (`docs/site.md`). The shipped example addons (`examples/addons/hello/`, `examples/addons/showcase/`) are tested; keep them working when the framework changes, and regenerate the reference (`bin/dana addons reference`). The add-on catalog is its own repository (`dana-catalog`).
- **Docs** stay current in the same commit as the code; a new area gets a doc and one line in its part's guide.
- **Nothing personal in the repository**: no real names, phone numbers, addresses, domains, device serials, account ids or real money data in code, tests, fixtures or docs. Tests use made-up people (`server/tests/testenv.py`: Robin, Gal, Maya…). A deployment's own setup lives in its site (`site/`) and in gitignored secrets: `server/.env`, `android/keystore.properties`, `android/local.properties` (`sdk.dir`).
- **Old names**: the project was called `ganam-assistant` before 10.0; nothing is named that any more, except the transition for the app built before the rename (package `app.ganam.assistant`: `kinds` in `/api/me`, notification ingest) and the one-time move of `ganam_*` browser storage keys to `dana_*`.

## Setup (once per clone)

```bash
git config core.hooksPath .githooks
cp -r site.example site      # or link your own site: ln -s ~/my-dana-site site
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.lock && cp .env.example .env  # Python 3.12+, tested on 3.14
```

Contributor docs for people: `README.md`, `CONTRIBUTING.md`.
