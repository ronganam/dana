# Tasks and triggers

## Trigger system (tasks)

Tasks support triggers stored as JSON in `tasks.triggers`:
- `time`: ISO datetime — checked every 30s, per user (the `check_time_triggers` job in `addons/tasks/hooks.py`)
- `location`: `{"location": "zone_name", "event": "arrive|leave"}` — the name of one of the user's zones (Dana's own, `docs/zones.md`); fired by core's `core.signal.location` event (the `location` signal: the phone's geofences, `docs/signals.md`; handled in `addons/tasks/hooks.py`)
- `car`: `"enter"|"leave"` — fired by core's `core.signal.car` event (the phone's car detection)
- `wake`: `true` — fired once a day per user when they are up (`core.user.woke`: a phone unlock confirmed by screen time, a sleep session ending, or the fallback screen poll; `docs/people.md` → Wake detection); the same moment sends their morning briefing

Dana's phone app is the source of the location, car and wake signals. A `core.signal.*` event marked `late` (it happened more than `SIGNAL_LATE_MINUTES`, 30, before it arrived: the phone was offline) only records the transition for the refire logic; no reminder goes out.

All triggers send one `notify.send(user, Message(text, kind="reminder", data))` (`core/notify.py`): the text to the Telegram chat of the task's creator, the `reminder` event to their phone through the phone addon's push channel (direct push, see below). Completing a task from the reminder (app, Now) sends `reminder_clear` the same way. The `wake` trigger fires on core's `core.user.woke` event.

## Code

The `tasks` addon (`addons/tasks/`) owns lists, tasks, links and the trigger engine; the signals and their ranking are core's (`core/signals.py`), the phone's source is the `phone` addon's (`addons/phone/signals.py`, which does not import `tasks`), the wake flow in `core/wake.py`.

| Module | What |
|---|---|
| `groups.py` | task lists: default set, lookups, create / rename / delete, the `task_list` sharing item type |
| `service.py` | tasks: what a user sees (`visible_sql`), lookups, create / save / complete / snooze / delete, payloads (`public_task`), a location trigger's zone (`resolve_zone`) |
| `links.py` | task links (`task_links`: task → `call` / `session` / `whatsapp` / `memory` / `project` / `notification` / `person` ref) |
| `titles.py` | emoji and list picking for new tasks (model at `llm.complete`, purposes `task_emoji` / `task_group`) |
| `triggers.py` | the trigger engine: `fire(user, type, action=, target=)`, the time checker, refire / flap / cooldown marks |
| `tools.py` | the agent tools |
| `web/tasks.js`, `web/tasks-core.js`, `web/tasks.css` | the web page `tasks` (Reminders: filters, the list by group / priority / due date, the detail pane, the new-reminder and group modals; above them the core Today strip through `api.piece("today", el)`); the pure part is tested by `tests/test_web_tasks.mjs` |

## Per user

- **Lists** (`task_groups.user_id`): each user with `tasks` has their own lists; names are unique per user. The default lists (General, Home, Work, Shopping, Health, Personal) are created when `tasks` is connected for a user and again at every start (a deleted default list comes back on restart). The first admin's list ids are what they were (`general`, `home`, a new list `fitness`); other users' ids carry their user id (`<user id>-general`).
- **Tasks** belong to their list's owner (`tasks.user_id`) and record who created them (`tasks.created_by`). Moving a task to another list gives it to that list's owner. A user sees their own tasks and the tasks of lists shared with them (docs/access.md, "Sharing"); another user's task answers "not found" (404).
- **Triggers** fire for the task's creator, while they can still see the task (also on a list someone shared with them), and the reminder goes to them. `fire(user, "location" | "car" | "wake", …)` fires only that user's tasks; the time checker runs for every user with `tasks`, as that user, one user's failure not stopping the others. Refire / flap / cooldown marks are per user in `task_trigger_marks (user_id, key, at)` (same keys as the old `memories` rows: `last_trigger_v2_<type>_<task id>`, `last_event_v2_<type>_<zone or ->`).
- **Location triggers** name one of the user's own zones (`docs/zones.md`) (`service.resolve_zone`: any case; "home" is their Home zone). Another name is refused with the list of their zones, and with no zones at all with "Location reminders need one of your zones, and you have none yet: add your places in Settings → Zones. Use a time, car or wake-up reminder instead." (HTTP 400; the tool's error in chat). The tool `get_zones` lists them for the agent.
- HTTP endpoints (`/api/tasks*`, `/api/task-groups*`; `addons/tasks/api.py`) need `tasks` connected (403 `not_connected`); a write to a task or list shared read-only answers 403, renaming / deleting / sharing a list someone else owns 403, an unknown or invisible id 404.
