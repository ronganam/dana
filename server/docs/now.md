# Now, Notifications screen, self-check, search

Server side of the **Now** screen (one queue of things that need a decision: the phone app's, and the web app's, which opens on it: `docs/web-design.md`, "Now"), the **Notifications** screen, the self-check and search. Code: `core/now.py` (composition + actions), `core/now_api.py` (routes), `addons/phone/notifications/overview.py`, `core/checks.py`, `core/search.py`. Notifications are the caller's own (the `phone` addon, docs/notifications.md): Now's needs-you cards, `notifications` counts and the notification suggestions in `quiet` are theirs, empty / zero without the addon; acting on another user's notification answers 404, on one without the addon 403 `not_connected`; search returns only their notifications. Every part of the screen is the caller's own and comes only from the addons they have connected (a composer, `core/composers.py`): suggestions and notifications need `phone`, reminders and the weekly review `tasks`, the day strip's events `calendar`, weather Home Assistant, the "At home" slot / choice a Home zone (`docs/zones.md`), the "On waking" slot / choice the wake flow (`phone` + `homeassistant`); a missing addon adds nothing, and acting on a card whose addon the caller lacks answers 403 `not_connected` (a suggestion or `quiet:all` → `phone`, a reminder or review → `tasks`). Hidden cards, undo entries and the lede's briefing are per user. Tests: `test_now.py`, `test_composers_users.py`. Undo: `core/undo.py` (route `POST /api/undo/{id}` in `core/actions_api.py`). An action that needs an addon the caller has not connected (accepting a calendar suggestion without `calendar`, or undoing it after `calendar` was disconnected) answers 403 `{"error": "not_connected", "addon": "calendar"}` (docs/access.md, "Enforcement").

All endpoints need the API token like every other `/api/*` route. Clients must ignore fields they don't know; optional fields may be missing.

## `GET /api/now`

Plain DB reads plus cached values; no model call. Calendar events are the caller's (their `calendar` addon's selected calendars; none without it, `day.calendar` false) and come from a per-connection cache (`addons/calendar/today.py`) refreshed in the background every 5 min (the very first request waits at most `NOW_CALENDAR_WAIT_SECONDS`, 1.5 s, then answers without events); weather from a 30-min cache (Home Assistant), never awaited.

```json
{
  "now": "2026-11-14T18:40:00+02:00",
  "lede": "Noa at 19:30, rain after nine.",
  "items": [Item],
  "quiet": [QuietEntry],
  "quiet_total": 7,
  "notifications": {"total": 17, "noise": 11, "needs_you": 1},
  "day": {"slots": [Slot], "calendar": true}
}
```

- `lede`: one line that never contradicts the queue under it. Today's briefing headline (with a final period) while it is fresh — made today, less than `LEDE_BRIEFING_HOURS` (3) ago — and `items` isn't empty; otherwise, from data at hand (no model): "<next event today> at HH:MM", else `null` — never a bare count, the queue header already has it. ", rain likely (60%)" is appended when the cached weather says so (on its own: "Rain likely (60%).").
- `quiet`: pending suggestions that never pop up: every suggestion that came from a notification, call suggestions with `confidence` < `NOW_QUIET_CONFIDENCE` (0.7), and call suggestions beyond the queue's cap. At most 20; `quiet_total` is the full count.
- `notifications`: same numbers as `GET /api/notifications/overview` `counts` (`total` = active, not snoozed; `noise` = `dismiss` bucket + anything from a quiet app; `needs_you`).
- `day.calendar`: false when the calendar couldn't be read (events are then missing from the slots).

### Item

```json
{
  "id": "sug:12",
  "kind": "suggestion",
  "source": "Call · אמא · 20 min ago",
  "title": "להזמין טכנאי למזגן",
  "why": "Due Mon · AC in the living room",
  "primary": Action,
  "secondary": [Action],
  "schedulable": true,
  "ref": {"type": "call", "id": "…"},
  "notification": {…},
  "blocks": [Block]
}
```

- `why` is **optional**: present only when there is something specific to say (see each kind); clients show nothing when it's missing.
- `id` is stable and prefixed by kind: `approval:<session_id>`, `notif:<notification id>`, `sug:<suggestion id>`, `task:<task id>`, `check:<key>`, `review:<review id>`, `budget:<YYYY-MM>`, `money:<transaction id>`, `money_save:<saving key>`, `money_target:<saving key>:<YYYY-MM>`. Send it back as is to `/api/now/act`.
- `kind`: `approval` | `notification` | `suggestion` | `reminder` | `money` | `check` | `review` | `budget`.
- `schedulable`: the card may be dropped on a day slot (`action: "schedule"`).
- `ref.type`: `thread` (approval → open the conversation), `call`, `notification`, `task`, `check`, `review` — what to open or pass as on-screen context (`POST /api/chat/context` accepts `call`, `notification`, `task`, `review`, `suggestion`, `person`).
- `notification` (notification items only): `{id, key, package, app, can_reply, on_phone, actions: [{title, reply}]}` — `key` is the Android shade key, so the phone can reply / open / press a button through its own notification listener. `can_reply` = still in the shade and has a reply button.

### Notification cards: title, source, why

`addons/phone/notifications/format.py::notification_parts` (also used for search results and for the source of notification suggestions):

- **Chats** (the notification has a sender or a conversation — WhatsApp, Slack, SMS): `title` = the newest message (a "Name: " prefix in groups is dropped), `source` = "App · Sender · age", or "App · Sender in Group · age" for groups.
- **Everything else** (mail, GitHub, banks, …): `title` = the first meaningful line of the text (for Gmail, the subject) and the notification's title goes to `source` as the sender ("Gmail · Bright Data · 3 min ago"), unless it reads as the message itself (ends in punctuation, ≥ 5 words, or ≥ 3 words and longer than the text's first line: "Your package has arrived" over "Tap to track") — then it is the `title`. The sender is left out when it's the app's name, a count ("3 new messages") or already in the title. The rest of the text is the `text` block, labelled with the sender.
- **`why`** (from the stored triage answers and the rule; `notification_why`), first that applies: "<sender> says it's urgent" (a person says so), "<sender> is waiting for an answer" (a person asks the user for something; not their own message, not one meant for someone else), "You asked to be interrupted for <app or sender>" (rule `alert`), "You marked <app or sender> as important" (rule `important`), "Security alert", "A payment to handle" / "About your money", "A delivery needs you"; " · time-sensitive" is appended (or "Time-sensitive" alone) when it can't wait until tomorrow. Nothing specific (e.g. not triaged yet) → no `why`.

**Action**: `{"action": "accept", "label": "Remind me tomorrow 09:00", "args": {…}, "client": true}`. `args` is optional (send it as `args` to `/api/now/act`, merged with what blocks contribute). `client: true` means the client handles it itself and never posts it: `open` (expand the card's blocks, or open `ref`; for approvals open the thread) and `reply` (notification: reply through the phone's listener; the web shows it disabled).

Order of `items` (each group capped): approval (1, sessions waiting ≤ 48 h, thread `main` first) → needs-you notifications (newest, the same wording from the same app only once, `NOW_MAX_NOTIFICATIONS` 3) → call suggestions (newest, `NOW_MAX_SUGGESTIONS` 4) → due reminders (most recent firing first, `NOW_MAX_REMINDERS` 4) → unusual charges of the last 3 days (`money`, 3; `insights.unusual`, docs/money.md) → ways to save (`money_save`, 2: due within 14 days, or a bill increase found in the last 7 days; open or doing) and targets (`money_target`, 2: at 80 % of this month's target, last month's result in a month's first 3 days) (docs/money.md, "Savings") → failing checks (3) → weekly review (1). Cards hidden with `snooze` / `schedule` / `ignore` / `dismiss` don't appear until their time.

### Blocks (rendered generically, in order)

| type | fields | notes |
|---|---|---|
| `lines` | `label`, `lines: [{text, who?, hit?}]` | Transcript excerpt (≤ 7 sentences) around the quoted one; `hit: true` on the line(s) the item came from. Transcripts have no speaker labels today, so `who` is usually absent. |
| `chat` | `label`, `messages: [{mine, text, who?}]` | Last ≤ 5 messages of the conversation (this and earlier notifications of the same chat in the last 12 h). `mine` is always false today (the user's own messages aren't captured); `who` for "Name: text" lines in groups. |
| `facts` | `label`, `items: [string]` | |
| `text` | `label`, `text` | |
| `week` | `label`, `cells: [{label, value, ok}]` | 7 cells, oldest first. `ok: null` = today, not known yet. |
| `choices` | `label`, `key`, `options: [{label, value, selected, action_label?, message?}]` | Picking one changes the primary: send the chosen `value` as `args[key]` with the primary action; show `action_label` on the primary button (and `message` is what the confirmation will say). |
| `checks` | `label`, `key`, `options: [{label, value, checked}]` | Send the checked `value`s as `args[key]` (a list) with the primary action. |

### Kinds: what they carry and accept

| kind | primary | secondary | blocks |
|---|---|---|---|
| `approval` | `open` (client) "Review" / "Pick" | — | `title` says the action in words ("Send a WhatsApp message to גל", "Delete the reminder “…”", "Invite noa@… to “Dinner”"); `text` block = the content (label "Message" / "Caption" / "New text" / "Description"), `facts` "Details" = other "Label: value" lines; or `facts` Options for a pick. Same wording as the Telegram Approve message (`core/approval_text.py`); an unknown tool gets its name as the title and its arguments as "Label: value". `why` = "You asked: “…”" when the request is known. |
| `notification` | `reply` (client) when `can_reply`, else `open` (client) | `snooze {minutes: 60}` "In 1 h", `dismiss` | `chat` for chats, else `text` |
| `suggestion` task | `accept` "Remind me …" with `args.triggers` = proposed | `dismiss` | `lines` (or `text` summary / quote), `choices` key `triggers`: proposed (selected), Tomorrow 09:00, When I get home, When I wake (no proposed trigger → "No reminder" first) |
| `suggestion` event | `accept` "Add to calendar · Tue 19:00" | `dismiss` | `lines` / `text` |
| `suggestion` alert (security) | `dismiss` "Got it" | — | as above |
| `reminder` | `done` | `snooze {minutes: 60}` "1 h", `snooze {until}` "Tonight" (before 19:00), `snooze {slot: "home"}`, `snooze {until: tomorrow 08:00}`, `dismiss` | `facts` History (created when / from what, moved N times, due, reminded when), `text` Note |
| `money` (an unusual charge, `ref {type: "transaction", id}`) | `open` (client) "Ask Dana" (chat context kind `transaction`) | `dismiss` (sticks; `snooze` / `schedule` also accepted), with Undo; another user's charge 404, without `money` 403 `not_connected` | `facts` (amount, merchant, when, account, why it stands out) |
| `money` (a way to save or a target, `ref {type: "saving", id: <saving key>}`; "Ask Dana" opens the `saving` context chat) | `open` (client) "Ask Dana" | `dismiss`, with Undo (hides the card only: the saving's status is not changed); another user's key 404, without `money` 403 `not_connected` | `facts` Saving (the saving or the money at stake, the first step) / Target (the target, spent so far, or what was saved against the usual pace) |
| `check` | `open` (client) "Details" | `ignore {days: 7}` | `week` (wake), `facts`, `text` (what it is / how to fix) |
| `budget` (admins, from 80 % of the monthly budget until it is spent; docs/usage.md, `ref {type: "usage", id: <YYYY-MM>}`) | `open` (client) "Details" | `dismiss` "Dismiss for this month" (hidden until the next month, with Undo; a past month 409, without `manage_server` 400) | `facts` This month (spent, budget, forecast) |
| `review` | `apply` (with the `checks` block, key `items`) or `open` (client) when nothing is proposed | `skip` | `facts` This week, `facts` Money (with `money`, when there is something to say), `checks` Proposed (actions other than keep) |

A **due reminder** is an open task whose reminder fired (the caller's `last_trigger_v2_*` mark in `task_trigger_marks`, written by `addons/tasks/triggers.py` for time / place / car / wake triggers) within `NOW_REMINDER_MAX_HOURS` (48) and that nobody touched since (completed, snoozed, edited — `updated_at` later than the firing + 5 min — or dismissed in Now), plus open tasks whose time trigger has passed but hasn't fired yet. Older firings are left to the Reminders screen.

### QuietEntry

`{id: "sug:57", kind: "suggestion", title, source: "Slack · 2 h ago", primary: {action: "accept", label: "Add"}, secondary: [{action: "dismiss", label: "Dismiss"}], ref}` — act on it with its `id` like any card. "Dismiss all": `POST /api/now/act {id: "quiet:all", action: "dismiss"}`.

### Day slots

`day.slots`: every remaining full hour of today, from the next one through the 23:00 slot (`DAY_LAST_HOUR`), then `home` ("At home", only when the caller has a Home zone) and `wake` ("On waking", only with the wake flow: `phone` + `homeassistant`). Late at night: from 23:00 there are no hour slots (only `home` and `wake`); between midnight and 06:00 (`DAY_FIRST_HOUR`) the hour slots start at 06:00 instead of offering the sleeping hours:

```json
{"key": "19", "label": "19:00", "items": [{"title": "Meeting with Noa", "kind": "event", "time": "19:30", "event_id": "…"},
                                            {"title": "…", "kind": "reminder", "task_id": "…", "time": "19:45"}]}
{"key": "home", "label": "At home", "items": [{"title": "Take out the trash", "kind": "reminder", "task_id": "…"}]}
```

Hour slots: timed events from the selected calendars (all-day ones left out) and open reminders with a time trigger in that hour; anything between now and the first slot (the rest of this hour, or the night before 06:00) is listed in the first slot with its own `time`. `home`: open reminders triggered by arriving at the home zone. `wake`: open reminders with a wake trigger.

## `POST /api/now/act`

Request `{id, action, args?}` → `200 {ok: true, message, undo_id?, …}`. `message` is the short confirmation to show ("Reminder set · tomorrow 09:00"). Undo with `POST /api/undo/{undo_id}` (15 min, lost on a server restart; `404` when expired / already used, `409` with `error` when the undo is refused — changed since, or the user can no longer change it (`core.undoable.UndoConflict`); `500` with `error` for any other failure).

Errors: `{ok: false, error}` with `400` (bad action / args, a past hour, client-only action posted), `404` (the card's object is gone), `409` (already accepted / done / applied), `500`.

Extra result fields: `device_keys` (notification dismiss: `["key"]` to cancel in the shade; notification snooze: `[{key, until_ms}]` to snooze there — same as the older endpoints), `task_id` (a reminder was created), `until` (check ignore), `applied` / `failed` (review), `count` (quiet dismiss-all).

Time args, wherever `snooze` / `schedule` take them: `{minutes}`, `{until: ISO}` (no zone = local), `{slot: "<hour>"}` (today at that hour; must be later than now), `{slot: "home"}`, `{slot: "wake"}`; reminders also take `{trigger: {…}}` (a full trigger object as in `tasks.triggers`; `"home"` as a location is mapped to the user's Home zone, `docs/zones.md`).

| id | action | args | does | undo |
|---|---|---|---|---|
| `sug:` | `accept` | `triggers`, `title`, `due_date`, `priority`, `group`, `description`, `start`, `end`, `location` (overrides), or `slot` | Same path as `POST /api/suggestions/{id}/accept`: creates the task (linked to its call / notification) or the calendar event; an alert is dismissed | ✓ deletes the task / the event (through the calendar's own undo when it registered one, else `delete_event`) and puts the suggestion back to pending; refused when the task was completed meanwhile |
| `sug:` | `dismiss` | | `dismiss_suggestion` | ✓ |
| `sug:` | `schedule` | `slot` | Task suggestions only: accept with that slot's trigger | ✓ |
| `sug:` | `snooze` | time | Hides the card until then (suggestions have no snooze of their own) | ✓ |
| `quiet:all` | `dismiss` | | Dismisses every quiet suggestion | ✓ all |
| `notif:` | `dismiss` | | `set_status(dismissed)`; returns `device_keys` | ✓ in Dana only (the phone already cancelled it in its shade) |
| `notif:` | `snooze` | `minutes` / `until` / hour `slot` | Native notification snooze; returns `device_keys [{key, until_ms}]` | ✓ in Dana only |
| `notif:` | `schedule` | hour `slot` | Snooze until that hour | ✓ |
| `notif:` | `schedule` | `slot: home \| wake` | Creates a reminder "Reply to <sender>" (or the notification title) with that trigger, linked to the notification (`task_links` ref `notification`), and archives the notification in Dana (not cancelled in the shade) | ✓ deletes the reminder, restores the notification |
| `notif:` | `open`, `reply` | | `400`: the phone does these | — |
| `task:` | `done` | | Completed + `reminder_clear` push | ✓ reopens (refused if it changed since) |
| `task:` | `snooze` | time, `slot`, or `trigger` | Same update as `POST /api/tasks/{id}/snooze` (triggers replaced, `snooze_count` + 1) + `reminder_clear` push | ✓ restores triggers, count, `updated_at` (the card comes back) |
| `task:` | `schedule` | `slot` | As snooze, message "Moved to …" | ✓ |
| `task:` | `dismiss` | | Out of Now until its next firing; the reminder stays open | ✓ |
| `money_save:` / `money_target:` | `dismiss` / `snooze` / `schedule` | time / `slot` | Hides the card (a target card for its month); the saving item stays as it was | ✓ |
| `check:` | `ignore` / `dismiss` | `days` (7; 0 = stop ignoring) | Hides the card for that long (`ignored_until` in `/api/checks`) | ✓ |
| `check:` | `snooze` / `schedule` | time / `slot` (`wake` → tomorrow 06:00, `home` → 4 h) | Hides the card | ✓ |
| `review:` | `apply` | `items`: checked refs (omitted = the stored proposals) | `weekly_review.apply` with the unchecked ones disabled | **✗ not undoable** (many changes at once: deletes, completions, new tasks) |
| `review:` | `skip` / `dismiss` | | `weekly_review.dismiss` | ✓ back to pending |
| `review:` | `snooze` / `schedule` | time / `slot` | Hides the card | ✓ |
| `approval:` | any | | `400`: approve in the conversation | — |

## Notifications screen

### `GET /api/notifications/overview`

```json
{
  "counts": {"total": 17, "noise": 11, "needs_you": 1, "snoozed": 2},
  "apps": [{"package": "com.whatsapp", "app": "WhatsApp", "rule": "normal", "rule_action": null, "count": 3,
            "needs_you": 1, "noise": 0, "summary": ["Dan is waiting for an answer", "…"], "summary_source": "ai",
            "latest_at": "2026-11-14T18:34:00+02:00"}],
  "quiet_apps": [same shape],
  "rule_suggestions": [{"package": "com.github.android", "app": "GitHub", "action": "quiet", "cleared": 14, "opened": 0,
                        "reason": "You cleared GitHub 14 times this month without opening it."}],
  "summaries_refreshing": false
}
```

- Counts cover active, not snoozed notifications. **Noise** = `dismiss` bucket ("Suggested to dismiss") + everything from a quiet app (or a muted sender).
- `rule`: `alert` (Interrupt me) = rule `alert`; `normal` (Let Dana decide) = no rule or `important`; `quiet` (Keep quiet) = rule `mute`. `rule_action` is the stored rule. Set rules with `POST /api/notifications/rules` (below).
- `summary` points never contain a needs-you tag (see `docs/notifications.md`, AI summaries).
- `apps`: apps with something waiting, needs-you apps first, then by newest. `quiet_apps`: muted apps, including ones with nothing waiting (count 0), so the rule can be changed.
- `summary`: the cached per-app AI summary points (`summary_source: "ai"`, possibly stale; a background rewrite is started as `GET /api/notifications/summaries` does, never waited for); apps without one get the newest notification as one line (`summary_source: "latest"`).
- `rule_suggestions` (≤ 3): apps without a rule that were handled ≥ 10 times in 30 days, ≥ 90 % swiped away (the triage's low-engagement test). An app with a needs-you notification waiting right now is never offered.

### `POST /api/notifications/rules {package, sender?, action: alert|important|mute|none, apply?: true}`

→ `{package, sender, action, reapplied?, moved, message, undo_id}`. `message` is the confirmation ("WhatsApp will stay quiet", "Slack will interrupt you", "Dana decides for GitHub"; "Dan on WhatsApp …" for a sender rule); `reapplied` = active notifications the rule covers, `moved` = how many changed bucket / priority. With `apply` (default) the covered active notifications are re-bucketed as the triage would now, under the rule that applies to each after the change: answered ones through `decide()` on the stored answers (no model call) — so removing or changing a rule puts an un-muted app's notifications back where the triage put them, and `important` → `none` drops them back out of needs-you; ones the model never answered (muted at ingest) go to `normal` and get their first triage. `undo_id` restores the previous rule and every bucket / priority it moved.

### `POST /api/notifications/rule-suggestions {package, accept}` → `{ok, message, undo_id, rule?}`

`accept: true` sets the `mute` rule and re-applies it to what's waiting (same path as the rules endpoint, undo restores the buckets too); `false` remembers the decline (`notification_rule_declines`) so it isn't offered again for 60 days. Both undoable.

### Clearing (existing `POST /api/notifications/status`)

`{ids}` | `{package}` (everything active from that app — "Clear") | `{bucket: "dismiss"}` (the noise, now including quiet apps — "Clear the noise"), plus `status` (default `dismissed`) → `{updated, device_keys, undo_id}`. The phone cancels `device_keys` in its shade; undo restores them in Dana only.

### Suggestion pop-up

The `suggestions` push (full-screen review on the phone) is sent for calls that just ended, and no longer for notification suggestions unless `SUGGESTION_POPUP_NOTIFICATIONS=1`. Those wait in `quiet`.

## Self-check

`GET /api/checks` (every user; the caller's own checks: only those of the addons they have connected (see "applies to" below), the server's own `reminders` only with `see_everyone`; the first admin, with every addon, gets every key as before) → `{checks: [{key, label, ok, detail, since, ignored_until}]}`; `since` is the time of the last good signal the detail refers to (local ISO, or null). `POST /api/checks/{key}/ignore {days?: 7}` (only for the caller; a key they don't see → 404) → `{ok, message, until, undo_id}` (`days: 0` stops ignoring). A check that is not ok and not ignored is a `check:<key>` card in Now.

| key | ok when | source of the timestamp |
|---|---|---|
| `push` | FCM configured, a phone of the caller registered, the last ping attempt to it didn't fail (or the phone fetched since), it checked in within `CHECK_PUSH_FETCH_HOURS` (24) (ok "not connected" without the `phone` addon) | their `phone_push.last_fetch_at`, `system_marks['push:<user>']` (every `push.send` to them) |
| `notification_sync` | a batch from the caller's phone within `CHECK_NOTIFICATION_SYNC_HOURS` (12) (ok "not connected" without the addon) | `system_marks['notification_sync:<user>']` (every batch), else their newest notification |
| `location` | the caller's person entity changed, or a zone transition fired, within `CHECK_LOCATION_DAYS` (3) | `system_marks['ha_event:<user>:person']` (the Home Assistant watcher) |
| `car` | the caller's car connection changed within `CHECK_CAR_DAYS` (7) | `system_marks['ha_event:<user>:car']` |
| `ha_entities` | every entity the caller's watcher watches (person, car sensor) exists in their Home Assistant (ok "not connected" / "nothing selected" without one) | `system_marks['ha_entity:<user>:<role>']` (written at each subscribe and when an entity is added / removed) |
| `ha_watcher` | the caller's watcher is connected, or reconnecting for less than `CHECK_HA_WATCHER_MINUTES` (10) | `system_marks['ha_watcher:<user>']` (written when it connects / disconnects, with the last error) |
| `wake` | a wake of the caller confirmed less than `CHECK_WAKE_DAYS` (3) days ago (shown with `phone` + `homeassistant`) | their newest `wake_log` row; week view from their `wake_log` + wake briefings (a date moved from the old memory without a time shows ✓); their last raw signal in `system_marks['wake_signal:<user>']` |
| `reminders` | the tasks addon's time-trigger job ran in the last 5 min (a server check: only with `see_everyone`; declared by `@addon.selfcheck` in `addons/tasks/hooks.py`) | the job's heartbeat `system_marks['job:tasks.check_time_triggers']` (every pass, `scheduler.last_run`) |
| `triage` | fewer than 3, or under half, of the caller's last 24 h's triages failed (no answer after the last attempt, `docs/notifications.md` → Judges) (ok "not connected" without the addon) | their `notifications.triage_status` |
| `calls` | no recording failed to process in 3 days (silent ones excluded) and one was uploaded within `CHECK_CALLS_DAYS` (7) | `phone_calls` |
| `calendar` | the last read of the caller's calendar (Now's cache) worked (ok "not connected" without the addon) | `system_marks['calendar:<user>']` (written by `addons/calendar/today.py`) |
| `briefing` | the caller's briefing of today exists (checked from 11:00; shown to users who get one) | their `briefings` row |
| `budget` | the monthly budget is not spent (shown with `manage_server`; red "paused by the budget" while background work waits, docs/usage.md) | `core/budget.py` (the month's spend from `usage_events` / `usage_daily`) |
| `transcription` | a transcription model is set (docs/configuration.md → Transcription), or none was asked for since the server started (shown with `manage_server`; red "needs a transcription model" once a transcription was refused) | `llm.transcriber()`, `llm.transcription_refused()` (in memory) |

## Search

`GET /api/search?q=&limit=5` → `{results: [{kind, id, title, subtitle, …}]}`: the caller's own items and those shared with them (task lists, projects, the items of their active shopping list — their own or an edit-shared one); an addon they have not connected adds nothing. Routing extras: `notification` results add `package` (open that app's page), `note` results add `project_id` (the entry's project, `null` when it has none; a project result's own id). Notification `title` / `subtitle` follow the card rule above (what it says / "App · Sender · age"). `kind`: `reminder` (open tasks: title / description), `call` (FTS over caller, title, summary, transcript + LIKE), `person` (names, aliases, relation, phone digits), `note` (knowledge entries the caller can see — own and shared projects; none without the `knowledge` addon: FTS + LIKE; projects as `id: "project:<id>"`), `notification` (active, FTS), `shopping` (the caller's active shopping list; none without the `shopping` addon). At most `limit` (1–20) per kind; best matches first across kinds (exact title > prefix > a word starts with it > contains > body-only match). No model.

## Storage (baseline tables, keyed by user since `m0015_composers_per_user`)

- `now_hidden (user_id, item_id, until, reason, created_at)` — a user's hidden / snoozed / ignored cards (`core/now_hidden.py`).
- `system_marks (key, last_at, ok, last_ok_at, last_fail_at, detail)` — signals with no other timestamp (per-user marks have `:<user id>` in the key).
- `wake_log (user_id, date, at, why)` — one row per user and confirmed wake (the "once a day" record).
- `notification_rule_declines (package, declined_at)`.

## Configuration

Constants in `core/now.py`: `LEDE_BRIEFING_HOURS` (3), `DAY_FIRST_HOUR` (6), `DAY_LAST_HOUR` (23). Environment: `NOW_MAX_NOTIFICATIONS` (3), `NOW_MAX_SUGGESTIONS` (4), `NOW_MAX_REMINDERS` (4), `NOW_QUIET_CONFIDENCE` (0.7), `NOW_REMINDER_MAX_HOURS` (48), `NOW_CALENDAR_WAIT_SECONDS` (1.5), `SUGGESTION_POPUP_NOTIFICATIONS` (0), `CHECK_PUSH_FETCH_HOURS` (24), `CHECK_NOTIFICATION_SYNC_HOURS` (12), `CHECK_LOCATION_DAYS` (3), `CHECK_CAR_DAYS` (7), `CHECK_WAKE_DAYS` (3), `CHECK_CALLS_DAYS` (7).
