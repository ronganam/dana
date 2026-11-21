# Tasks and triggers

## Trigger system (tasks)

Tasks support triggers stored as JSON in `tasks.triggers`:
- `time`: ISO datetime — checked every 30s, per user (the `check_time_triggers` job in `addons/tasks/hooks.py`)
- `location`: `{"location": "zone_name", "event": "arrive|leave"}` — fired by the user's Home Assistant watcher (below) from their person entity (the event `homeassistant.location.changed`, handled in `addons/tasks/hooks.py`)
- `car`: `"enter"|"leave"` — fired by the same watcher from their car Bluetooth sensor (`homeassistant.car.changed`)
- `wake`: `true` — fired once a day per user when they are up (their phone unlock confirmed by the screen history from their Home Assistant, or a fallback screen-history poll; only for users with `phone` and `homeassistant`; `docs/people.md` → Wake detection); the same moment sends their morning briefing

All triggers send one `notify.send(user, Message(text, kind="reminder", data))` (`core/notify.py`): the text to the Telegram chat of the task's creator, the `reminder` event to their phone through the phone addon's push channel (direct push, see below). Completing a task from the reminder (app, Now) sends `reminder_clear` the same way. The `wake` trigger fires on core's `core.user.woke` event.

## Home Assistant watcher (location / car)

Dana watches each user's entities itself (`addons/homeassistant/watcher.py`); Home Assistant needs no component or webhook.

- **Who**: one watcher per user with `homeassistant` connected and their `person` setting, or their `car_sensor` + `car_device` settings, set; over that user's own WebSocket (`subscribe_entities` for exactly those entities); it fires `addons.tasks.triggers.fire(user, …)` as that user. `Watchers` (started with the server, `core/server.py`) checks every `HA_WATCH_RECONCILE_SECONDS` (10) against the connections: a new connection or changed settings / credentials (re)start a watcher, a disconnect stops it.
- **Location** (as the old component did): the person's state changes old → new: old not `not_home` / `unknown` / `unavailable` → `fire(user, "location", action="leave", target=old)`; new not one of those → `action="arrive", target=new`. The zone is the person's state (the zone's name); the trigger engine strips and lower-cases it, so stored zone targets keep matching. An entity that appears or disappears fires nothing.
- **Car**: the `car_device` name in the sensor's `connected_paired_devices` attribute (as a string): not connected → connected = `fire(user, "car", action="enter")`, connected → not connected = `action="leave"` (a sensor that appears already connected counts as an enter, as in the component).
- **Initial state and reconnects**: the first value after the watcher starts is only recorded; nothing fires on it. It reconnects with back-off (1 s doubling to `HA_WATCH_RETRY_MAX_SECONDS`, 300) forever; after a reconnect the current value is compared with the last one seen, so a transition missed meanwhile fires once (and the refire / flap marks still drop duplicates). The last values live in memory: a transition during a server restart is not replayed. One user's failing connection never affects another's.
- **Self-check** marks per user (`addons/homeassistant/status.py`, `system_marks` keys `ha_watcher:<user>`, `ha_entity:<user>:<role>`, `ha_event:<user>:<role>`) → `docs/now.md` (Self-check: `location`, `car`, `ha_entities`, `ha_watcher`).

## Code

The `tasks` addon (`addons/tasks/`) owns lists, tasks, links and the trigger engine; the Home Assistant watcher is in the `homeassistant` addon (it is given `triggers.fire`, the addon never imports `tasks`), the wake flow in `core/wake.py`.

| Module | What |
|---|---|
| `groups.py` | task lists: default set, lookups, create / rename / delete, the `task_list` sharing item type |
| `service.py` | tasks: what a user sees (`visible_sql`), lookups, create / save / complete / snooze / delete, payloads (`public_task`), the location-trigger check |
| `links.py` | task links (`task_links`: task → `call` / `session` / `whatsapp` / `memory` / `project` / `notification` / `person` ref) |
| `titles.py` | emoji and list picking for new tasks (model at `llm.complete`, purposes `task_emoji` / `task_group`) |
| `triggers.py` | the trigger engine: `fire(user, type, action=, target=)`, the time checker, refire / flap / cooldown marks |
| `tools.py` | the agent tools |
| `web/tasks.js`, `web/tasks-core.js`, `web/tasks.css` | the web page `tasks` (Reminders: filters, the list by group / priority / due date, the detail pane, the new-reminder and group modals; above them the core Today strip through `api.piece("today", el)`); the pure part is tested by `tests/test_web_tasks.mjs` |

## Per user

- **Lists** (`task_groups.user_id`): each user with `tasks` has their own lists; names are unique per user. The default lists (General, Home, Work, Shopping, Health, Personal) are created when `tasks` is connected for a user and again at every start (a deleted default list comes back on restart). The first admin's list ids are what they were (`general`, `home`, a new list `fitness`); other users' ids carry their user id (`<user id>-general`).
- **Tasks** belong to their list's owner (`tasks.user_id`) and record who created them (`tasks.created_by`). Moving a task to another list gives it to that list's owner. A user sees their own tasks and the tasks of lists shared with them (docs/access.md, "Sharing"); another user's task answers "not found" (404).
- **Triggers** fire for the task's creator, while they can still see the task (also on a list someone shared with them), and the reminder goes to them. `fire(user, "location" | "car" | "wake", …)` fires only that user's tasks; the time checker runs for every user with `tasks`, as that user, one user's failure not stopping the others. Refire / flap / cooldown marks are per user in `task_trigger_marks (user_id, key, at)` (same keys as the old `memories` rows: `last_trigger_v2_<type>_<task id>`, `last_event_v2_<type>_<zone or ->`).
- **Location triggers** need the user's own `homeassistant` connection (its zones). Without it, adding or snoozing a task with a location trigger is refused: "Location reminders use Home Assistant zones, and Home Assistant isn't connected for you. Use a time, car or wake-up reminder instead." (HTTP 400; the tool's error in chat).
- HTTP endpoints (`/api/tasks*`, `/api/task-groups*`; `addons/tasks/api.py`) need `tasks` connected (403 `not_connected`); a write to a task or list shared read-only answers 403, renaming / deleting / sharing a list someone else owns 403, an unknown or invisible id 404.
