# Plant care (showcase)

The example that uses every capability an addon has once: watering reminders for house plants. The addon reference (`server/docs/addon-reference.md`, also each new addon's `REFERENCE.md`) quotes its code, between `# --- reference: <key> ---` and `# --- end ---` lines, so every example there is code that runs and is tested.

- **Settings** `room`, `remind_hour` (6–22, checked by `validate=`), `paused` (`blocked=` stops its tools); **credential** `api_key` (optional; `test=` asks the plant service with it).
- **Tools** `showcase_add_plant`, `showcase_plants`, `showcase_water` (writes with Undo), `showcase_forget` (asks first, with its own Approve card), `showcase_tip` (the plant service at `api.plants.example`, or `SHOWCASE_API_URL`), `showcase_poem` (the model, purpose `showcase_poem`), `showcase_worker_ping` (its worker), `showcase_water_now` (starts a live activity: a watering timer on the phone).
- **Hooks**: a daily reminder job, a service, a handler of `tasks.task.created` ("Water <name>" adds the plant), a notify channel keeping messages for the page, a Now card per thirsty plant with its `water` action, a day item, a briefing section (`plants`, count `thirsty_plants`), a weekly-review section with its `plant` action, search, a self-check, context for a chat about a plant, the provider `showcase.thirsty_count`, the dashboard widget `plants_thirsty` (setting `heading`; drawn by `web/widget.js`), a `live_refresh` ending the watering timer when its countdown is over and a `live_action` for its "Watered" button (`watering.py`).
- **Page** `plants` (`web/plants.js`) reading **route** `GET /api/showcase/plants`; **worker** (`worker/`) on port 8092 at `SHOWCASE_WORKER_URL`; **tables** `showcase_plants`, `showcase_inbox` (`migrations/`).

**Events** it emits: `showcase.plants.thirsty` (`names`: the plants that need water), from the morning job.

Test: `bin/dana addons test ../examples/addons/showcase` (from `server/`); `tests/test_addon_kit.py` runs it in Dana's suite.
