# People, caller card, briefing, weekly review, widgets

Phone-side of the server features in `server/docs/people.md`.

## Caller card (`callcard/CallerCard.kt`, phone flavor only)

- `CallerCardReceiver` (declared in `src/phone/AndroidManifest.xml`) listens for `PHONE_STATE`. RINGING, or OFFHOOK without a ring (an outgoing call), shows the card. OFFHOOK after a ring collapses it to its header, kicker "On the call". IDLE hides it and sends `WearCards.clear("caller")`.
- The card comes from `GET /api/people/lookup?phone=`, which reads the DB only and never calls a model. There's a 4 s timeout. A cache per last 9 digits (pref `caller_card_cache`, 60 numbers) shows instantly, and the network answer replaces it.
- `CallerCardOverlay` uses plain Views in a `TYPE_APPLICATION_OVERLAY` window. Colours are the Ink & Paper tokens (`ui/theme/Color.kt`), dark or light from `ThemeSettings` (System follows the night mode); body lines start on their own script's side; 560dp centred when the screen is ≥ 600dp wide. You can drag it; its Y position is saved in `caller_card_y`. Tapping it opens the person in the shell (`EXTRA_OPEN_PERSON_ID` → More → People → person; Back goes person → People → More); ✕ closes it. Android hides app overlays while Settings (and some system screens) are in front, so it doesn't show over them. The same card is sent to the watch (`WearCards.send(kind = "caller")`).
- It needs `READ_PHONE_STATE` + `READ_CALL_LOG` (for the number) and "Display over other apps". Settings → *Caller card* has the on/off toggle (pref `caller_card_enabled`, default on), lists what's missing (`CallerCards.missing`) and offers grant buttons.

## Wake signal (`data/wake/UnlockWatcher.kt`)

- An `ACTION_USER_PRESENT` receiver is registered at runtime, both from the notification listener (which Android keeps bound) and from `DanaApp`. Between 05:00 and 12:00, an unlock enqueues `UnlockReportWorker`, which posts `POST /api/phone/unlock {at}` (unique work, network constraint, retried up to 3 times, and dropped if more than an hour late).
- Unlocks are reported at most once every 2 min. Once the server answers `done`, nothing more is sent that day (pref `wake_done_date`). The server confirms from the screen history; see the server's `docs/people.md` → Wake detection.
- Each unlock is logged (`adb logcat -s UnlockWatcher`).

## Pushes (`DanaCards.kt`, dispatched by `push/PhoneEvents`)

- `briefing`: an InboxStyle notification on channel `dana_briefing`. It's cached in pref `briefing_cache`, shown on the watch and in the widgets.
- `context_card`: the person's card 10 min before a meeting with them (channel `dana_people`, times out after 45 min). Also sent to the watch.
- `weekly_review`: "Weekly review is ready" (channel `dana_review`), which opens the review screen (`EXTRA_OPEN_REVIEW`).

## Screens

- **People** (`ui/screens/people/`: `PeopleScreen.kt` list + shell, `PersonPage.kt`, `PeopleParts.kt`; `PeopleController`): list with search, Recent / Been a while / A–Z tabs and pull-to-refresh. Recent shows pinned faces on top, then **Needs you** (open tasks, drifting, birthday within a week), then This week / This month / Earlier; long-press a row to pin, call or hide. The person's page: header with pin / edit / hide, Call · WhatsApp · Ask Dana tiles (`startContextChat("person", id)`), facts (last in touch, rhythm, calls / chats), the brief + refresh, open items, reminders (tap the circle to complete), notes and a day-grouped history (message bursts from the same chat on one day are merged, links stripped). Hebrew lines lay out RTL by script (`DirText` / `DirBullet`). Cover screen: drill-down. Inner screen: list | person, and from ~600 dp of pane width the page splits into story | tinted At a glance aside. It's on the wide rail; on phones it opens from the People button on Reminders and Calls (route `people`).
- **Weekly review** (`ui/screens/review/ReviewScreen.kt`, `ReviewController`, route `review`): items grouped by type, each with why it's here (Overdue / Snoozed a lot / …), facts, Dana's reason and one-tap choices (New date · Done · Move · Delete · Leave it; Accept / Dismiss; Remind me). New date / Remind me show quick dates (Tomorrow, Mon, In a week, Pick a date…); a proposal whose date is missing or past starts at tomorrow. Move shows the groups. Choosing Leave it turns the item off. Apply posts only the user's edits (`ReviewItem.toJson`). On the inner screen the summary, section jumps and Apply / Skip sit in a side pane. You can also skip, or press "Review now" when there's no pending review.
- Deep-link extras in `MainActivity.handleDeepLink`: `EXTRA_OPEN_PERSON_ID`, `EXTRA_OPEN_REVIEW`, `EXTRA_OPEN_SCREEN` (a route).

## Home-screen widgets (`src/phone/.../widgets/`, Glance 1.1, phone flavor only)

- The widgets are Ask Dana, Today, Waiting on you, People (pinned), Inbox and Shopping (the Shopping widget and its quick-add window → `docs/shopping.md`). Receivers and `res/xml/widget_*.xml` live in the phone source set. Kiosk / TV get a no-op `DanaWidgets` from `src/nopush`.
- One snapshot (pref `widget_snapshot`) feeds every widget. `WidgetRefreshWorker` rebuilds it every 30 min, and also on app start, after a widget action and on a briefing push (`DanaWidgets.refresh`). It fetches briefing, tasks, suggestions, pinned people, inbox counts + overview and calendar in parallel and refreshes the shopping store (`ShoppingSync`, whose fields it merges with `ShoppingSnapshot`), keeps the old value for any call that fails, and sends a trimmed copy to the watch (`WearCards.sendSnapshot`).
- Ticking a task, accepting or dismissing a suggestion, removes the item from the snapshot first (the optimistic update), then calls the server (tasks go through `TaskActionQueue`, so they work offline), then refreshes. A shopping tick goes through `ShoppingSync` (shown at once, queued offline) and every change of that store redraws the Shopping widget and re-sends the snapshot to the watch.
- Ask Dana → `AskDanaActivity`, which calls `DanaVoiceInteractionService.openSession()` (`showSession`) when Dana is the default assistant, and otherwise opens the chat.
