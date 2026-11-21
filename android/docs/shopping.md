# Shopping (phone, widget, watch data)

The shopping list on the phone: Shopping under More in the shell (it takes over More with its own list and stage), the home-screen widget and its quick-add window, the watch's data and actions. The server contract (screen payload, add rules, aisles, undo, the event stream, the `shopping` push, sharing and invitations) is the server repo's `docs/shopping.md`. The web panel (`static/shopping.js`) is the reference for look and wording.

## Pieces

| Where | What |
|---|---|
| `data/shopping/ShoppingModels.kt` | `ShoppingScreen` (`list, categories, items, counts, usuals, invites, lists`), `Item`, `Usual`, `Invite`, `ListRef`, `Who`, `Answer` (screen + `undo` + `results`); `ShoppingJson` parses the server's payloads |
| `ShoppingText.kt` | The server's text rules on the phone: the name key, typed text → parts (`2 חלב, eggs x12`), paste as one line, at most 30 |
| `ShoppingOps.kt` | `ShoppingOp` (Add / Check / Edit / Remove / ClearBought / Forget, each with its `list_id`), applying ops on screen with the server's add rules (`applyOps`), which answer is kept (`accepts`), when a stream event reloads (`onChanged`), the request of each op (always `?list=`), the queue's JSON |
| `ShoppingView.kt` | Pure view rules: rows by aisle in the server's order, Bought, the linger, header subtitle, list menu, invitation lines, error / undo / add-result words |
| `ShoppingStore.kt` | The state (server answer + in-flight + queued), the disk cache and queue, sending, Undo of queued changes, `forget` |
| `ShoppingSync.kt` | The one store of the process (shared by the screen, the widget, pushes, the watch bridge, `PendingSyncWorker`) |
| `ShoppingSnapshot.kt` / `ShoppingWatch.kt` | The widget snapshot's shopping fields (also the watch's) / the watch's `/shopping_action` |
| `ShoppingShortcut.kt` | The launcher shortcut "Shopping" (dynamic; phone flavor only) |
| `data/network/Me.kt` | `GET /api/me` cached in `GanamPrefs` (`me_id`, `me_name`, `me_kinds`) |
| `ui/viewmodels/ShoppingController.kt` | The place's controller: enabled, the screen, the live stream, add / suggestions, tap-linger, editor, invitations, list switching, share sheet |
| `ui/screens/shopping/ShoppingParts.kt` | Header, invitation banner, usuals, add field, suggestions, aisle header, row, Bought, empty state, editor and share sheet contents |
| `ui/shell/destinations/ShoppingDestination.kt` | The place: cover (one column + bottom add dock + sheets) and wide (side: header / invites / usuals / Bought or the editor; stage: aisles + add dock) |
| `src/phone/.../widgets/ShoppingWidget.kt` | The widget, its tick action, `ShoppingWidgetSync` (redraw + snapshot + watch) |
| `src/phone/.../widgets/ShoppingQuickAddActivity.kt` | The widget's `+` window |

## The place

- Shown only when `/api/me` `kinds` has `shopping` (`ShoppingController.enabled`, from the cached `Me` at start, refreshed on start / foreground). A 403 `not_connected` drops the kind. Without it: no nav entry, no shortcut, and a shell on Shopping goes back to Now. The previous (tabbed) layout has no Shopping screen.
- Nav label "Shopping" + the count to buy of the active list. Route `"shopping"` (`EXTRA_OPEN_SCREEN`) from the launcher shortcut, the widget header, a shopping search result and the invitation notification (tag `shopping-invite-…`).
- Keeps the screen on while visible (`view.keepScreenOn`).
- **Touch**: tap a row = tick (light haptic); the row stays a moment (`LINGER_MS` 900, ticked look), then moves to Bought; the snackbar has Undo (`POST /api/undo/{id}` with the write's `undo.id`). Swipe either way = remove with Undo. Long press or the pencil = the editor sheet (name, quantity −/+, note, aisle chips, Remove); it saves on close, a 409 `duplicate` stays in the sheet ("Pita is already on the list."), and closing again with the refusal standing discards the change. **One column**: the check sits at the start and the pencil at the end, and every name, quantity chip and note starts next to the check whatever its language (an absolute `TextAlign` from the layout direction); each text still runs in its own direction (`byScript`), so Hebrew reads right to left inside its line. The same in Bought; only the editor's fields take their text's direction.
- **Add field**: at the bottom (thumb). Enter / the IME action adds the typed text (split like the server), the field stays focused and empty. While it has focus the usuals (empty text) or the suggestions (`GET /api/shopping/suggestions?q=&list=`, debounced; offline: the usuals that match) sit right above it, and on the cover the ask bar hides while the IME is up so the add dock sits on the keyboard. There is no second voice input: the ask bar's mic is the voice path.
- **Invitations**: a banner per invite (Use / Not now); Use with own open items asks to move them ("Move your 2 items over?" → Move them / Leave them), then `POST /api/shopping/invites/{id}` and the answer's line with Undo.
- **Lists**: the ⋮ menu lists `lists`; an editable one switches the active list (`PUT /api/shopping/active`, Undo); a read-only one is looked at with `?list=` ("View only · Robin's list", "Back to your list"; no add field, no ticking, no editor). The share icon (own list) opens the share sheet: `GET /api/sharing/shopping_list/{id}`, each user Not shared / View / Edit → `PUT` with `{user_id, level}`. When shared, the header shows everyone's initials (ink on the highest surface, semibold, so they read at a glance; a row added by someone else carries theirs too).
- **Errors** are said plainly from the server's `error`: `read_only`, `not_connected`, `too_many`, `duplicate` (in the editor), 404 ("That's no longer on the list — someone may have changed it."; a looked-at list that is gone → back to the active one).
- **Layouts**: STACKED (cover) one column with the add dock over the thread peek / ask bar / nav; SPLIT and FULL put header, invitations, usuals and Bought (or the open editor) in the list pane and the aisles with the add dock on the stage.

## Offline and instant

- `ShoppingStore.State`: `server` (the last answer for the **active** list, kept in `filesDir/shopping_screen.json`; a list only looked at is never cached), `inflight` (sent, not answered), `queued` (`filesDir/pending_shopping.json`). What every surface shows is `shown()` = `server` with `queued` + `inflight` applied (`ShoppingOps.applyOps`, the server's add rules: already there → no second row, bought → back with its id, same client id twice → once).
- Sending is serialised (one mutex): each op tries once (6 s); unreachable / 408 / 429 / 5xx → queued, and once anything is queued new ops queue behind it, so the order never changes. Adds carry client ids (`AddPart.itemId`, 8–64 `[A-Za-z0-9_-]`), so an add that reached the server before the connection dropped adds nothing twice when it is sent again. The queue is flushed by `PendingSyncWorker` (WorkManager, network constraint), the server coming back ONLINE, the app coming to the front and the place opening.
- A waiting change the server refuses is dropped: a 404 (its item is gone) silently, anything else with one line (`notices`: "Couldn't sync “Removed Bread”: This list is view only."). A refused immediate change is taken back on screen and said.
- An older answer never replaces a newer one (`accepts` by `list.version`; another list id is always taken), and a read that comes back while a write is on its way is ignored.
- "N changes waiting to sync" under the header while anything is queued; the snackbar of a queued change says "… · will sync".
- **Undo of a queued change** (`ShoppingStore.cancel(opId)`): still waiting → taken out of the queue, never sent (the screen goes back at once); already sent → its server `undo.id` is used (`POST /api/undo/{id}`); neither (too old, never answered) → "Too late to undo that".
- **Account change**: `fetchMe` compares the cached `me_id` with the server's; a different user (or a new server / token in Settings: `MainViewModel.updateServerConfig`) runs `store.forget()` (cache and queue deleted, nothing of the previous user is sent) and reloads.

## Live updates

- While the place is visible (`ON_START` … `ON_STOP`): `GET /api/shopping/events` (with `?list=` while looking at another list). `changed {list_id, version}` reloads when newer (or another list); when an own write is on its way it waits for that answer first (it may carry the version). `lists` reloads the screen; `end` (a looked-at list no longer visible) goes back to the active list. The stream reconnects with a backoff of 1 s doubling up to 10 s, and at once when the server comes back ONLINE. Coming to the front reloads.
- Not visible: the silent push `shopping {list_id, version}` (`PhoneEvents`) → `ShoppingSync.onPush` reloads when the held list is older or another one, which redraws the widget and sends the watch a snapshot. Without FCM (the local env) the widget catches up on the next app start, widget action, or the 30-minute `WidgetRefreshWorker`.

## Widget and quick add

- `ShoppingWidget` (Glance, phone flavor): header "SHOPPING  N to buy" (opens the place), `+` (quick add), then what is left to buy in the server's order under light aisle labels, with quantity; a read-only list says "view only" and shows no ticks and no `+`; "Loading…" / "Nothing to buy." otherwise. It reads `ShoppingSync` (the same cache and queue as the place) **inside** `provideContent` (`collectAsState`), because Glance keeps a session alive after a tap and would otherwise redraw values read once.
- Each row is 40 dp high: a drawn ring (22 dp, `res/drawable/ic_widget_check_ring.xml` tinted soft ink) in a 40 dp target, then the name and its quantity, always from the ring's side (one column, the text in its own direction). A tap anywhere on the row = `ShoppingTickAction` → `ShoppingSync.submit(Check)`: shown at once, queued offline. Every store change calls `DanaWidgets.shoppingChanged` → `ShoppingWidgetSync.changed` (redraw after 120 ms, snapshot + watch after 1.5 s).
- `ShoppingQuickAddActivity`: a sheet over the home screen (not exported, no recents), keyboard up at once; Enter adds and keeps the field, usuals while empty, suggestions while typing, the last three results ("Added Cumin"), Done / Back / a tap outside closes.

## Watch data

The widget snapshot (pref `widget_snapshot`, sent trimmed to the watch on `/wear/dana_snapshot`) carries:

| Field | |
|---|---|
| `shopping_count` | items to buy (the complication) |
| `shopping` | `[{id, name, quantity, aisle, aisle_key}]` to buy in the server's order (60 in the snapshot, 40 to the watch) |
| `shopping_list_id` | the list they are on: send it back with an action |
| `shopping_version` | the list version the items come from |
| `shopping_can_edit` | false on a list shared read-only (no ticking, no adding) |

A tick from the watch: `/shopping_action {action: "check" \| "uncheck", id, list_id, ref}` or `{action: "add", text, list_id, ref}` to the phone → `WearMessageListenerService` → `ShoppingWatch.handle` → `ShoppingSync.submit` (the same path as a tap on the phone: shown at once, queued offline, client ids) → answer on `/response/shopping_action` `{ok, queued, error?, ref}` (`error` is a sentence to show, also "Couldn't do that on the phone." when handling it failed; `ref` is the watch's id for the request, sent back as it came). The phone then sends a fresh snapshot (1.5 s after the change). `ShoppingWatch.answer(store, json)` is the testable core (`ShoppingWatchTest`). The watch's screen, its own queue for when the phone is out of reach, the tile and the complication: `docs/wear.md` (Shopping).

## Gotchas

- Android's regex engine (ICU) rejects the inline `(?U)` flag: the text rules use `[\s\p{Z}]` and `\p{Nd}` (`ShoppingModelTest` guards it). A `PatternSyntaxException` there crashed the place on open.
- `rememberSwipeToDismissBoxState` is saveable: a row that comes back (Undo of a swipe) would restore as dismissed and be removed again. `confirmValueChange` calls `onRemove` once and returns false.
- Tests: `app/src/test/.../shopping/` (`ShoppingModelTest`, `ShoppingOpsTest`, `ShoppingStoreTest` with a scripted server, `ShoppingSnapshotTest`) (`ShoppingWatchTest` for the watch's requests) against `app/src/test/resources/shopping_web.json`, a copy of the server's `test_fixtures/shopping_web.json`: copy it again when the server's payloads change.
