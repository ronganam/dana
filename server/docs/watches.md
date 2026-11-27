# Watches

The `watches` addon (`addons/watches/`, on by default) turns anything a user asks — "make sure a transfer of about 22k from a person arrives by tomorrow", "remind me at 20:30 to pick up Noa, 30 minutes before too, or when I get in the car", "when the dishwasher finishes ask Gal on WhatsApp to empty it" — into a **watch**: triggers + a window + actions. A model compiles the words into a spec once; a cheap engine fires it: structured conditions in Python first (microseconds), then keywords, then a small-model yes/no check only on the events that are left.

Code: `spec.py` (shape, validation, time rules, matching, dry runs, `explain`, `pinned_values`; pure), `store.py` (tables), `trace.py`, `interest.py`, `identity.py`, `engine.py` (the events tap, the time pass), `pipeline.py` (what the engine did, in memory), `compiler.py`, `actions.py`, `service.py` (shared by tools and routes), `tools.py`, `api.py`, `hooks.py` (jobs, the Now card). Tests: `addons/watches/tests/test_watches.py`.

## The spec

Stored as JSON in `watches.spec`:

```json
{"title": "...", "kind": "watch|reminder", "task_title": "...", "task_id": null,
 "triggers": [
   {"id": "t1", "kind": "time", "at": "YYYY-MM-DD HH:MM", "every": "daily|weekdays|weekly|monthly|null"},
   {"id": "t2", "kind": "time", "relative_to": "t1", "offset_minutes": -30},
   {"id": "t3", "kind": "time", "at": "...", "skip_if_fired": ["home"]},
   {"id": "home", "kind": "event", "event_type": "core.signal.location",
    "where": [{"field": "zone", "op": "eq", "value": "home"}],
    "keywords": ["..."], "during": "HH:MM-HH:MM", "semantic": "yes/no question"}],
 "window_start": "...", "window_end": "...", "repeat": "once|each", "cooldown_minutes": 10,
 "on_match": [Action], "on_deadline": [Action],
 "evidence_event_types": ["..."], "evidence_keywords": ["..."], "evidence_question": "...",
 "unsupported": "honest note or empty"}
```

- **Ops**: `eq ne in contains contains_any not_contains gt gte lt lte between approx exists` (`in` / `contains_any` values joined by `|`, `between` uses `value2`, `approx` `tolerance_pct`, default 10). Text matching ignores case, spaces and punctuation (`spec.norm`: "K S P" = "ksp" = "k.s.p").
- **Times** are local (`core.clock`). `every: weekdays` means the five days from the day weeks start on (`clock.week_start_day`: Sunday–Thursday by default, Monday–Friday with `WEEK_START=mon`). A relative trigger follows each occurrence of the one it names. `skip_if_fired`: the trigger does not fire if any of those triggers already fired ("at 19:00 if I'm not home by then").
- **Validation** (`spec.validate`) fills defaults (`repeat` once for a watch, each for a reminder; cooldown 10; window: from now, a reminder until the end of its last time's day, a repeating one a year, a watch 7 days) and returns errors in words the compiler can fix: unknown event types and fields (naming the valid ones), numbers for numeric ops, a bare event trigger, unknown trigger ids, a tool the user does not have ("use an agent action"). Each error starts with the path of what is at fault (`triggers[0].where[1].value: …`), a plain message otherwise.
- **Event types** come from `registry.event_types(user)`: core's (`core.signal.car`, `core.signal.location`, `core.user.woke`) and those of the addons active for the user (`Addon(events=(EventType(name, about, fields, history, values), …))`). Only those can be watched. Sources: the phone (notifications, emails, analysed calls), Gmail (`gmail.email.received`, from Gmail itself: the real sender and recipients; preferred over the phone's email notification), money (transactions), tasks (created / completed), Home Assistant (`homeassistant.state.changed`: any entity's state change, e.g. "phone battery under 50%" = `entity_id` eq the battery sensor, `old_state gte 50` and `new_state lt 50`, so it fires once per crossing; `server/docs/access.md`, "Home Assistant"); the list with each event's data: `../docs/addons.md`, "Events".
- **Known values** (`EventType.values`, `spec.known_values`): real values of closed fields (Home Assistant: the user's entity ids). An `eq` / `in` condition on such a field naming another value is an error with the close matches (`triggers[0].where[0].value: 'sensor.phone_battery' is not one of the user's real entity_id values; close: …`); nothing is checked while none are known.

## Engine

- **Events**: `engine.on_event` is a `core.events.tap`, called for every emit. A car / location signal that arrived late (`late: true`) is skipped, like the tasks addon does. For a user with the addon and an event in their catalog: `event` → the structured filter over their active watches in their window (`during`, `where`, keywords) → per candidate, drop when `skip_if_fired` names a trigger that fired, a reminder trigger already fired, the same amount (±1%) fired from a *different* event type within 72 h ("same as an earlier fire": the bank push and the later transaction), or — `repeat: each` — the same source (counterparty, sender, address, person, app or entity) fired within `cooldown_minutes` → one `watch_match` call (fast profile) for all semantic and evidence questions of that event, the event framed by `core.text.untrusted`, the JSON read from the reply's text → `fire` (one per watch per event) → actions. `repeat: once` closes the watch (`done`) at its first event fire. Decisions for one user run under a lock, actions after it.
- **Time**: a 30-second job (`hooks.tick`) fires due occurrences (one per trigger per pass; an occurrence missed by more than 2 h — the server was down — is skipped), runs `on_deadline` when the window ends with no event fire (`expired`), closes ended watches (`done` when anything fired, else `expired`), a time-only watch once nothing is left, and keeps `next_time` current. Paused watches do nothing.
- **Reminders** (`kind: reminder`): the task is made through the tasks addon when the watch is created (`task_id`); each trigger fires once; the watch closes when the task is completed (`tasks.task.completed`). **Evidence**: an event of `evidence_event_types` with an `evidence_keywords` hit asks "was it done?"; a yes only records a held `suggest_complete` fire and shows a "Done?" Now card (`watch:<fire id>`, Done / Not yet) — it never closes anything by itself.
- **Budget**: a semantic trigger whose cheap filters passed more than 20 events a day over the user's last 14 days (`EventType.history`) is pushed back to the compiler once; `load` (`{matches_14d, per_day, semantic, over_budget}`) is shown per trigger.
- Fires older than 90 days are pruned daily (the trace: see "Trace").

## Actions

`{type, text, urgency, tool, args_json, instructions, autonomy}`; each fire stores `[{type, status: done|held|failed|skipped, detail, run_id, session_id}]` (`run_id` / `session_id`: the agent run an action started).

- `notify`: `core.notify.send`; a reminder as kind `reminder` (like the tasks addon's, full screen on the phone), anything else as kind `notify` (`urgency` now → the messages channel).
- `create_task` / `complete_task`: `registry.execute(user, "add_task" / "update_task")`, so the tasks addon stays its own.
- `tool`: run directly only when the tool neither asks nor acts outside Dana and the action says `act_and_tell`; otherwise as an agent run, so the normal Approve card appears.
- `agent`: `core.session_runner.start_agent_task` for the user in the watch's own session `watch-<id>` (never the shared thread; `watch-<id>-2`, `-3` while it is busy), created with the title `👀 <watch title>` so `GET /api/sessions` lists it; the live activity's `open` is `/chat/<session id>` (the web app opens that session). The message starts with the identity block (below), with the triggering event framed as untrusted — so that turn asks before every tool that acts outside Dana, even with `act_and_tell` (the taint rule wins). `ask_first` without an event frames the saved request the same way. While it runs it is a **live activity** (`core/live.py`, key `run:<session id>:<run id>`: the phone's Live Update, the web Now strip, the Omarchy bar) showing its current step ("Using search_calendar"); it ends with "Done" and the reply, or "It did not work". When the run ends waiting for an approval, the fire's action is `held`, the activity turns into what Dana wants to do with **Approve / Deny** (`@addon.live_action` → `actions.answer` resumes that session's run, which gets its own activity) (on the phone: buttons on the Live Update, with its alert); the approval also stays in the chat and on Now as before.
- `suggest_complete`: held; the Now card does it.

## Compiler

`compiler.compile_request(user, text, old=None)`: one `watch_compile` call (balanced profile) with the moment, the user's zones, the event catalog (fields, each type's known values, at most 120 per field, most relevant first, and the spec's JSON schema), common real values and names from the last 14 days (framed as others' text) and the user's tools. Fields an event carries only in its history (a notification's `category` / `priority`: it is announced before its triage) are shown as unusable. The model answers with the spec as one JSON object (no forced tool use, no JSON mode); no JSON or validation errors go back once each (up to 3 tries), and on the first try the budget push-back. `change` compiles again from the old spec plus the change (the task is kept). `create_watch` / `update_watch` are `acts_outside`: in a turn carrying others' text they ask first, since a watch acts later on its own.

The compiler prompt also says: a `semantic` question is judged from the event's fields only, so never ask about what the event does not carry (use `where` on a field that carries it, e.g. `from_email` against the user's own address, or `unsupported`); prefer the most specific event type, from the source itself (e.g. `gmail.email.received` over `phone.email.received`); never claim the user cannot be reached on a listed channel.

## Who the user is

`identity.py`, in the compiler prompt and every agent run's message: the user's name, other names and pronoun (`core.users.User`), and never another person's name for them; the user's own addresses, read from their connections as plain data (the Gmail connection's `account`, the WhatsApp account's chat id from `addons.whatsapp.client.own_chat_id`: the stored account, else asked from WAHA by `identity.learn` before a compile or an agent run, at most 2 s, kept in memory; the Telegram account's `account`), e.g. "The user's own addresses: email a@example.com, WhatsApp 15550100301@c.us, Telegram Dana (@dana)"; where a `notify` reaches them (Telegram when `telegram_chat_id` is set, plus each `@addon.channel` that `core.notify.send` delivers on, e.g. the phone); a weather tool (over web search) when the user has one. No special rules or recipients: "an email from me" is a `where` on `from_email` with the user's address, "WhatsApp me" a normal `send_whatsapp_message` to their own chat id, asking first like any send. Agent runs also get the user's places (Home first, rough coordinates).

## Interest

`interest.py`: after any change to the user's watches (`store.on_change`: create, change, pause, resume, delete, expire, undo) and at start-up (`hooks.hint_interest`), `core.events.set_interest(user.id, "homeassistant.state.changed", "entity_id", values)` with `spec.pinned_values` over the active watches: the union of `eq` / `in` values on that field; None when any trigger on the type pins none or a reminder takes it as evidence; `[]` when none listens. Skipped while `core.events` has no `set_interest`.

## Trace

`watch_trace` (`migrations/m0002_trace.py`, `trace.py`): per active watch, each event of a type it listens to with `{at, trigger_id, event_type, event_id, summary, data (JSON ≤ 4 KB), outcome, reason, near}`. Outcomes: `drop` (the first failing condition and the value seen: `from contains_any "x|y" ← "me"`), `during`, `outside_window`, `semantic_no` / `semantic_yes` / `semantic_failed` (the small model's reason: `watch_match` also answers `why` per question), `fired`, `cooldown`, `skipped`, `unstored`. `near`: at least one condition held, or it got past the filter. Past 200 rows an hour for one watch only near misses and fires are stored; the rest are counted (one `unstored` row when that watch's next hour starts). The daily prune keeps 30 days and the newest 2000 rows per watch. A deleted watch's rows stay (for undo) and age out.

## Tools

`create_watch(request)`, `list_watches(include_closed)`, `update_watch(watch_id, change)`, `pause_watch`, `resume_watch`, `delete_watch` — every write with Undo; results say what was understood (each trigger in words) and what cannot be done (`unsupported`).

## API

All need the addon (403 `not_connected`); another user's watch id answers 404 like a missing one. Payloads: `tests/fixtures/watches_web.json` (`WRITE_WEB_FIXTURE=1 bin/test test_watches`).

- `GET /api/watches` → `{watches: [Watch]}`; Watch = `{id, title, kind, status, request, spec, created_at, window: {start, end}, fires_count, last_fired_at, next_time, task_id, load}`.
- `GET /api/watches/{id}` → `{watch, fires: [{id, at, trigger_id, event_type, event, actions}]}` (newest first, 50).
- `GET /api/watches/{id}/trace?limit=&outcome=all|near|<outcome>` → `{trace: [{id, at, trigger_id, event_type, event_id, summary, outcome, reason, near, data}], unstored}` (newest first, limit ≤ 500, default 100; `unstored`: this hour's checks over the cap). The Watches page shows it as "Recent checks" (All / Near misses / Fires, the event data expandable).
- `POST /api/watches {text}` → 201 `{watch}`; 400 `{error: "invalid", errors}`.
- `PATCH /api/watches/{id}` `{spec}` | `{status: active|paused}` | `{change}` → `{watch}`; 400 as above.
- `DELETE /api/watches/{id}` → `{ok, undo_id}` (`POST /api/undo/{id}` brings it back with its fires).
- `POST /api/watches/dry-run {spec, days}` → `{triggers: {id: {matches, per_day, over_budget, sample}}}` (sample: the last 20).
- `GET /api/watches/catalog` → `{event_types: [{name, about, fields}], ops, action_types, tools}`.
- `GET /api/watches/pipeline` → `{stats: {since, events_in, by_type, filter_pass, semantic_checks, semantic_yes, fires, actions, avg_filter_us, cost_usd, clock}, recent (last 100), watches_active, triggers_active}`; `GET /api/watches/pipeline/stream` → SSE, one `data: <PipelineEvent>` per step, a comment every 20 s.

## Pipeline

In memory per user since the server started (`pipeline.py`: the last 200 steps, counters, subscriber queues). PipelineEvent = `{seq, at, stage, event_type, event_id, summary, watch_id, watch_title, trigger_id, result, candidates, us, cost_usd, action_type}` (`action_type` on `action` steps; `clock` in the stats counts `time` and `deadline` steps). One event: `event` → `filter` (pass/drop, `candidates`, `us`; a drop of one candidate says why) → per semantic trigger `semantic` (yes/no/failed, its share of the estimated cost) → per fire `fire` → per action `action` (done/held/failed). Time: `time` → `fire` → `action`; `deadline`; reminder evidence `evidence`.
