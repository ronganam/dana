# Shopping list

The grocery list: what to buy at the supermarket, shared live between the
people of a household. Other purchases ("buy a gift for Dan") stay tasks in the
`Shopping` task group (`docs/tasks-and-triggers.md`); Dana routes by the item.

This file is the design of record. The phone, widget and watch screens live in
`android/` (`android/docs/shopping.md`); Raycast in
`extensions/raycast-assistant/`.

## Code

`addons/shopping/`, one module per seam:

| Module | Holds |
|---|---|
| `values.py` | Limits, errors (`ReadOnly`, `NotFound`, `Duplicate`), UTC time helpers |
| `names.py` | The name key, typed-text parsing (`parse_text`), item input checks |
| `lists.py` | Lists, the active list, sharing hooks (invitation notification, leaving a list), the lists payload, people |
| `items.py` | Adding, changing, ticking, removing, clearing, the 12-hour clearing, find by name, search |
| `aisles.py` | The aisle set, the lookup (list choice → shared table → background model call), `settle` |
| `history.py` | What was bought on a list, usuals, suggestions while typing |
| `invites.py` | `use` (with moving items) and `dismiss` |
| `journal.py` | The undo of every change (`core/undo.py`): rows before and after, the re-checks |
| `events.py` | The change transaction that raises the version, stream listeners, the coalesced phone push |
| `screen.py` | The screen payload |
| `api.py` | The HTTP routes |
| `tools.py`, `addon.py` | Dana's tools, the addon and its prompt words |

The web panel, the addon's page (`web=` in `addon.py`, files in `web/`;
`writing-an-addon.md`, "Pages"): `web/shopping-core.js` (the pure part:
view model, pending changes, requests, words, HTML; tested by
`tests/test_web_shopping.mjs` against `tests/fixtures/shopping_web.json`),
`web/shopping.js` (the controller, registered with `Dana.panel`) and
`web/shopping.css` (below, "The web panel").

Tables (the addon's `migrations/m0001_baseline.py`): `shopping_lists` (+ `version`),
`shopping_items`, `shopping_history`, `shopping_aisle_choices` (a list's own
choices), `shopping_aisles` (the shared name → aisle table),
`shopping_invite_answers` (an invitation used or dismissed).

## What it must feel like

1. **Adding is one step from anywhere.** Type a name and press Enter, tap a
   suggestion, or tell Dana. Several lines or a comma-separated line add several
   items. No form, no category to pick, no confirmation.
2. **In the store, one tap per item.** Items are grouped by aisle in walking
   order. A tap ticks the item and it leaves the way. Rows are big enough for a
   thumb.
3. **No housekeeping.** No duplicates, bought items clear themselves, things
   bought before are suggested.
4. **Shared live.** Everyone on the list sees a change within a second, and who
   added what.
5. **The same everywhere.** Web, phone, widget, watch, Raycast and Dana show the
   same order, the same words and the same Undo.

## Lists

- Every user with `shopping` connected owns one list (made on connect and at
  start). A list can be shared with other users through the sharing framework
  (item type `shopping_list`, `read` / `edit`; `docs/access.md` → Sharing).
- **The active list** is the list a user's shopping requests go to: their own,
  or one shared with them with `edit` that they chose. A share never switches
  it by itself (`docs/access.md` → Security rules).
- **Invitation.** When a list is shared with a user, they get an invitation:
  a banner at the top of their Shopping screen and a phone notification
  ("Robin shared their shopping list with you"). `Use this list` makes it their
  active list (when their own list has items to buy, they are asked whether to
  move them over); `Not now` hides the banner. They can switch later from the
  list menu, which appears only when the user can see more than one list.
  An invitation is pending while the list is shared with the user, is not
  their active list and has no answer. `use` needs `edit` (a read-only share
  answers 403 `read_only`); a list not shared with the caller answers 404
  like an unknown one. `use` with `move_items` moves only the caller's own
  items to buy from their own list, with the add rules below (an item already
  there is merged, a bought one there comes back); their bought items stay.
  The whole step (the active list, the answer, every moved item) has one undo.
  A share made from `bin/dana users` (no running server) sends no notification;
  the banner still shows.
- Removing a share, or lowering it to `read`, puts the user back on their own
  list. Removing a share also forgets the invitation's answer, so a new share
  is a new invitation.
- **The Share sheet.** The owner reads who has access and who can be added
  with `GET /api/sharing/shopping_list/{id}` (`users` and `shared_with`) and
  changes it with `PUT` (`docs/access.md` → Sharing); everyone on the list sees
  its people in `list.people` (owner first).

## Items

| Field | Meaning |
|---|---|
| `id` | Given by the server, or by the client (8–64 of `A-Z a-z 0-9 _ -`, e.g. a UUID) so a repeated request after a lost connection adds nothing twice; a client id already used on another list gets a new server id |
| `name` | As typed, trimmed, at most 200 characters |
| `quantity` | Free text, optional: `2`, `1 kg` |
| `note` | Free text, optional: `lactose-free` |
| `category` | An aisle key (below) |
| `checked`, `checked_at`, `checked_by` | Bought, when and by whom |
| `added_by` | The user who added it (`{id, name}` in payloads) |
| `created_at` | When it was added |

**Adding.** Names are compared by a normalised key (Unicode NFKC, case
folded, Hebrew vowel marks removed, dashes and slashes as spaces, other
punctuation removed, spaces collapsed).

- Already on the list to buy: nothing is added; a quantity or note given now
  replaces the stored one. Result `already`.
- On the list as bought: it comes back to buy, with the quantity and note
  given now (none given: cleared), keeping its stored name, and sorts as just
  added. Result `restored`.
- Otherwise it is added. Result `added`.
- An item id the list already has (a repeated request): result `already`,
  nothing changes.

Typed text is split on line breaks and commas. At most 30 items per add; more
is refused (400 `too_many`, with `max`), never cut. A leading
count (`2 חלב`, `3x eggs`) or a leading amount with a unit (`1 kg tomatoes`,
`2 ליטר חלב`) becomes the quantity; a trailing `x2` / `×2` too. Anything else
stays in the name.

**Bought items** move to a "Bought" section, newest first, and leave the list
by themselves 12 hours after they were ticked (when the list is next read or
changed). `Clear bought` removes them at once.

**Changing.** A rename to a name already on the list to buy, or ticking an
item back when the same name is already to buy, answers 409 `duplicate`. A
rename looks the aisle up again; a manual aisle change is remembered for that
name on the list.

**Order.** To buy: by aisle order, then by when it was added. There is no
manual reordering.

## Aisles

A fixed set, in store order. The server sends it with every list, so no client
keeps its own copy.

`produce` Fruit & vegetables · `bakery` Bread & bakery · `dairy` Dairy & eggs ·
`meat` Meat & fish · `pantry` Pantry · `snacks` Snacks & sweets ·
`drinks` Drinks · `frozen` Frozen · `household` Cleaning & household ·
`care` Personal care & pharmacy · `baby` Baby · `pets` Pets · `other` Other

An item's aisle is found in this order:

1. What this list's people chose for that name before (a manual change is
   remembered for the list).
2. What the model said about that name before (one shared table, name →
   aisle; it holds no user data beyond the name).
3. A background model call (`core/llm.complete_json`, purpose
   `shopping_category`) for the names of one add together. The item shows
   under `other` until the answer arrives, then moves (a normal list change).
   A failed call leaves it under `other`; the next add of that name tries
   again. The call runs as the acting user (their usage row), one per add,
   never blocks the add, and is skipped without a model key (`SHOPPING_MODEL`,
   else `BACKGROUND_MODEL`; `docs/configuration.md`). The answer moves only
   items still under `other` and raises the list's version.

**Waiting items.** The same call also asks about items already on the list to
buy that are still under `other` with no known aisle (no choice of the list,
nothing in the shared table), oldest first, at most 30 (`aisles.waiting`):
items carried over by the migration, or ones whose call failed. So an add of
any name, even a known one, sorts what still waits, and nothing stays under
Other for ever. A name the model itself puts under `other` is stored like any
answer and not asked again. Nothing classifies items at start.

## History and suggestions

Each list remembers what was bought on it (`shopping_history`: name, aisle,
how often, when last). A name enters the history when it is ticked, so a typo
that was deleted is never suggested; ticking it back takes one purchase away.
Names on the list to buy now are never suggested.

- **While typing:** names from the history that match what is typed, most
  bought first.
- **Usuals:** the most bought names (equal counts: the latest bought first) that are not on the list now (to buy or
  bought), as chips; one tap adds one. A name just bought is offered again once
  it has left the list.
- A suggestion can be forgotten (long press / its menu).

## Undo

One mechanism: every change registers an undo (`core/undo.py`) and its
response carries `undo: {id, label}`. Clients show it as a snackbar for a few
seconds and call `POST /api/undo/{id}`. Dana's tools register the same undo.
An undo is refused (409 with the reason; the id is then used up) when the
user may no longer change that list, or an item it would restore was changed
again since. Expired (15 minutes) or used: 404.

**Older undo ids.** Every undo id stays valid on its own for its 15 minutes;
a newer change does not cancel an older one. Pressing an older undo after a
later change to the same item is refused (the item was changed since); an
older undo of a different item still works. A client that shows one snackbar
at a time simply replaces the id it holds.

## Live updates

Every change raises the list's `version` in the same transaction (an aisle
answer and a change of who the list is shared with too).
`GET /api/shopping/events` is a server-sent stream:

```
event: changed
data: {"list_id": "499dfb668e3b", "version": 7}

: ping

event: lists
data: {}

event: end
data: {}
```

- `changed` `{list_id, version}`: first with the current version, then on
  every change of the followed list. A client that sees a newer version reads
  the list again.
- `lists` `{}`: the caller's own view changed (an invitation, a share, their
  active list); read the screen again.
- Without `?list=` the stream follows the caller's active list and switches
  with it (a `changed` for the new list, then `lists`). With `?list=` it
  follows that list and sends `end` and closes when the caller can no longer
  see it.
- A comment line every 25 seconds. A disconnected client's listener is removed
  when its write fails or the request ends.

Phones that are not showing the list (widget, watch) are told by a silent push
of type `shopping` (`{list_id, version}`) to every person on the list who has
`phone` (the owner and everyone it is shared with), at most one per user per
10 seconds: the first at once, later ones folded into one trailing push with
the newest version.

## API

Every endpoint works on the caller's active list; `?list=<id>` names another
list they can see. Without `shopping`: 403 `not_connected`. A write on a list
shared read-only: 403 `{"error": "read_only"}`.

| Request | Does |
|---|---|
| `GET /api/shopping` | The whole screen: `{list, categories, items, counts, usuals, invites, lists}` |
| `GET /api/shopping/items` | The items only (a plain array; the installed Android widget reads it, and `PATCH {checked}`) |
| `POST /api/shopping/items` | Add: `{text}` or `{items: ["milk", {name, quantity, note, id}]}` → `results: [{id, name, status}]` |
| `PATCH /api/shopping/items/{id}` | Change any of `checked`, `name`, `quantity`, `note`, `category` |
| `DELETE /api/shopping/items/{id}` | Remove |
| `POST /api/shopping/clear-bought` | Remove every bought item |
| `GET /api/shopping/suggestions?q=` | Names from the history matching `q` (no `q`: the usuals) |
| `DELETE /api/shopping/suggestions` | `{name}`: forget a name |
| `GET /api/shopping/events` | The live stream |
| `GET /api/shopping/lists` | The lists the caller can see and which is active |
| `PUT /api/shopping/active` | `{list_id}`: choose the active list (own or `edit`; read 403, unseen 404) → the new list's screen + `undo` |
| `POST /api/shopping/invites/{list_id}` | `{action: "use" | "dismiss", move_items}` |

`list`: `{id, name, access, owner: {id, name}, people: [{id, name, access}], version}`
(`access` is `owner`, `edit` or `read`). `categories`: `[{key, name}]` in
aisle order. `items`: to buy in display order, then bought, each
`{id, name, quantity, note, category, checked, checked_at, checked_by, added_by, created_at}`
(`checked_by` / `added_by`: `{id, name}` or null). `counts`: `{to_buy, bought}`.
`usuals`: `[{name, category, times}]`. `invites`: `[{list_id, name, owner, access}]`.
`lists`: `[{id, name, owner, access, active}]`. Every write answers with the
same fields plus `undo` (or `null`) and, for an add, `results`; an invitation
answer and `PUT /api/shopping/active` answer with the caller's active list.

Errors: `{error, message?}` — 400 `invalid`, 400 `too_many` (`max`), 403
`read_only`, 403 `not_connected`, 404 `not_found` (also another user's item or
list), 409 `duplicate`.

Sharing itself uses `GET / PUT /api/sharing/shopping_list/{id}`.

## The web panel

A page of the web app (`panel-shopping`): a shopping-cart button in the rail
and the bottom bar, with the count to buy as a quiet badge (kept fresh every
2 minutes while the page is not open), listed by the shell only for a user
with `shopping` on (`GET /api/addons`). `/shopping` (the addon's route in
`api.py`) serves the app, which opens the page named by the path (a bookmark,
the manifest's "Shopping" shortcut); switching to it sets the address to
`/shopping`, leaving it sets `/`.

Top to bottom: the header (the title, one quiet line: `7 to buy`,
`7 to buy · Shared with Maya`, `Robin's list · 7 to buy`,
`View only · Robin's list`; the people as initials when shared; Share for the
owner when there is anyone to share with; a menu only when it offers
something: the lists when the user sees more than one, Clear bought); an
invitation banner per pending invitation; the sticky add bar; the usuals; to
buy by aisle; Bought (collapsed, with Clear).

- **Adding.** Enter adds what was typed (the server splits and parses it) and
  keeps the input focused and empty. Pasting several lines puts them in the
  input as one comma-separated line; Enter adds them. Suggestions
  (`?q=`, debounced) show under the input; ↑ / ↓ choose one, Enter adds the
  chosen one, else the typed text. A chosen name or a usual is sent as
  `{items: [{name}]}`, never parsed for a count. The snackbar says `Added
  Milk` / `Added 3 items`, or what was not obvious (`Milk is already on the
  list`, `Bread is back on the list`).
- **Ticking.** A tap anywhere on a row ticks it; it shows ticked at once,
  stays in its aisle for 0.9 s (a second tap puts it back), then moves to
  Bought. A tap on a bought row puts it back to buy.
- **Editing.** The row's edit button (always shown on touch; on hover / focus
  with a mouse) opens a sheet (from the bottom on a phone, from the side on a
  wide screen): name, quantity (− / + while it is empty or a number; empty
  counts as 1), note, aisle chips, Remove. Enter or Done saves the changed
  fields, a tap outside saves, Escape cancels; a 409 is said in the sheet.
- **Usuals.** A tap adds the name; right click or a long press offers
  "Don't suggest again".
- **Invitations.** `Use this list`; when the user's own list (on screen) has
  items to buy, the banner asks inline "Move your 3 items over?" (`Move them`
  / `Leave them`). A read-only invitation offers `Look at it` (the list with
  `?list=`) instead. `Not now` dismisses.
- **Lists.** The menu switches the active list (`PUT /api/shopping/active`)
  to the user's own or an `edit` list; a `read` list is looked at with
  `?list=` and never becomes active. A read-only list has no add bar, no
  ticking and no edit buttons.
- **Share sheet** (owner): the people with `Can edit` / `View only` and
  Remove, and the other users to add (as `edit`), through
  `/api/sharing/shopping_list/{id}`.
- **Undo.** Every change that answered with an undo shows one snackbar (six
  seconds, the newest replaces the last) with Undo (`POST /api/undo/{id}`,
  then the screen is read again); a refused undo is said with the server's
  reason.

**State.** The screen shown is the last screen the server answered with the
user's pending changes applied on top (`applyOps`): every tap shows at once;
a write's answer (the full screen) replaces the server state when its
version is not older; a failed request drops its change, so the screen falls
back by itself, and the snackbar says why (typed text goes back into the add
bar). Writes always name the list on screen (`?list=`).

**Live.** One `EventSource` on `/api/shopping/events` (with `?list=` for a
list looked at) while the panel is shown: `changed` with a newer version than
the screen's reads the screen again (after the user's own writes land when
some are on their way); `lists` reads it again; `end` goes back to the active
list. The screen is also read when the tab becomes visible. Rows are keyed:
a reload changes only rows whose content changed, so focus, scroll, the open
sheet and the add bar's text stay; a row that arrives on the list already
shown flashes once.

**Text direction.** Names, quantities and notes each have `dir="auto"`; the
row's layout does not follow them (the check target is always at the start
of the row, the edit button at the end, every name starts next to the check
target), so a mixed Hebrew / English list stays one column for the thumb.

**Keyboard.** `/` or `a` focuses the add bar; on a row Space / Enter ticks,
`e` edits, Delete removes, ↑ / ↓ (or `j` / `k`) move; Escape closes a sheet or
menu. Every control has a visible focus ring and a label; motion is short and
off with `prefers-reduced-motion`.

The service worker caches the panel's files and never `/api/shopping*`.

## Dana's tools

| Tool | Does |
|---|---|
| `get_shopping_list` | The list by aisle, with each item's id, quantity and note, and what was bought |
| `add_shopping_items` | Add items, each with an optional quantity and note |
| `update_shopping_items` | Mark items bought / back to buy, remove them, or clear the bought ones; items by name or id |
| `edit_shopping_item` | Change one item's name, quantity, note or aisle |
| `choose_shopping_list` | Only on a server with more than one user: show the lists, switch the active one |

Items are found by name without reading the list first: the normalised name,
else the one item whose name contains the words. A name that matches nothing,
or several items, is reported back with the candidates.

## Not part of it

- No prices and no product catalog.
- No Telegram shopping group. In Telegram, people add items by telling Dana in
  their own chat.
- No "undo last" command and no operations log; Undo belongs to each change.
