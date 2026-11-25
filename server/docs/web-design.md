# The web design system

The web UI (`static/`, the addons' `web/` pages and widgets, and the tablets' and TVs' dashboards) shares one look with the
Android apps (`../android/DESIGN.md`): a warm ink canvas, Inter body, DM Mono eyebrows, Instrument Serif titles,
one ember accent, rows on the canvas. It follows the project website's showreel ("06:52", the watch briefing, and
"23:05", Now's "All clear."): calm, airy rows, few colours per screen. This page says where its type, colours and
motion live and how a screen uses them.

## Tokens (`static/styles.css`, section 1)

Every colour is a token on `:root`; a screen never hardcodes one.

- **Themes**: Ink (dark) is the default; Paper (light) follows `prefers-color-scheme: light`. Settings →
  Theme (`static/app-settings.js`) overrides it per browser with `html[data-theme="dark" | "light"]`
  (localStorage `dana_theme`, applied by a one-line script in `index.html`'s head before the first paint).
  The Paper values are written twice in `styles.css` on purpose: under the media query and under
  `[data-theme="light"]`.
- **Surfaces and text**: `--canvas`, `--canvas-soft`, `--canvas-raised`, `--hairline(-strong)`, `--ink`,
  `--body-strong`, `--body`, `--mute`, plus `--scrim` (behind dialogs), `--well` (code, JSON, transcript
  boxes) and `--shadow`. Washes over a surface use `rgb(var(--ink-rgb) / .08)`: light on Ink, dark on Paper.
- **Hues**: seven named hues, the same values on the web, phone, TV and watch:

  | hue | Ink | Paper | used for |
  |---|---|---|---|
  | ember | `#FF6A3D` | `#D1401A` | the accent: primary actions, selection, focus, "needs you", approvals, counts |
  | amber | `#F2A93B` | `#A86A00` | reminders, warnings |
  | sage | `#5FBF8A` | `#2E7D52` | calls, done / success, money in |
  | teal | `#3DBFB3` | `#1F7A72` | messages, notifications, WhatsApp, Telegram |
  | sky | `#5AA7F0` | `#2F6FB5` | calendar, events, info |
  | plum | `#B892F2` | `#7044B8` | people, contacts, memory |
  | rose | `#F0709A` | `#B83A64` | alerts, errors, money out, danger |

  Each hue has four forms: `--<hue>` (bars, dots, icons, fills at full strength), `--<hue>-tint` (the hue
  at 14 % on Ink, 12 % on Paper: chips, pills, selected rows), `--<hue>-text` (text in the hue, 4.5:1 on
  its tint in both themes) and `--<hue>-rgb` for any other alpha. `--accent`, `--accent-tint`,
  `--accent-text`, `--on-accent`, `--accent-gradient` (primary buttons, the FAB) and `--focus-ring` are
  ember. The older semantic names (`--destructive`, `--prio-*`, `--cat-*`, `--listening-green`,
  `--connecting-amber`) point at the hues.
- **Where colour goes**: the current place in the rail / bottom nav (accent pill and a 3 px accent line),
  active chips, selected rows and segmented buttons (accent tint + accent text), navigation counts
  (accent), status pills (tint + hue text: the Addons page's On / Needs setup / Reconnect / Error), avatars (people,
  callers, inbox app tiles) on a hue picked by a stable hash of the name (inbox: messaging teal, calendar
  sky, calls sage, contacts plum), a reminder's group colour as the dot before its group's name, money in sage
  and out / over in rose or amber, an approval's pill filled in ember. A faint static ember glow sits at the top
  of the canvas (`body`, `--glow-a`); it never moves.

## Type

The fonts are self-hosted (`static/vendor/fonts`, declared at the top of `styles.css` and `dashboard.css`,
precached by the service worker; `THIRD_PARTY.md`): no font CDN. Instrument Serif and DM Mono are Latin only, so
Hebrew and Arabic fall back to Inter (a right-to-left lede is set in Inter on purpose).

| role | font | size | where |
|---|---|---|---|
| display | Instrument Serif 400 (`--font-serif`) | 40 px phone / 52 px wide; "All clear." 46 / 64 px, italic | Now's lede, the state line ("All clear.") |
| place title | Instrument Serif 400 | 28 px (`.top-bar-title`); 24–26 px for a briefing headline or a settings card's title (`.settings-card-title`) | every place's header, the Tasks briefing, Settings' cards, a dashboard's big value (`.dw-big`) |
| eyebrow | DM Mono 400 (`--font-mono`), uppercase | 10–10.5 px, letter-spacing .14–.15em (`.dn-eyebrow`) | the screen's top line (the date, a place's kicker), section labels ("NEEDS YOU · 3"), a dashboard widget's title line; always in `--mute` |
| row title | Inter 500 | 15–16 px, `--ink` | a row's title |
| body / muted line | Inter 400 | 13–14 px, `--mute` or `--body` | a row's one line (source · why), notes |

**The header pattern**: every place's top bar is a mono eyebrow over a serif title. The title is
`.top-bar-title`; the eyebrow is the place's kicker (`.ibx-kicker` in `.ibx-titles`, or Chat's
`.top-bar-subtitle` in `.top-bar-title-group`), written after the title in the markup (a screen reader reads the
title first) and drawn above it (`column-reverse`). A place without a kicker shows the title alone. The bar is
`--topbar-height` (68 px) tall. Forms, settings and admin pages get this header and the type, nothing more.

## Rows, not boxes; one visible action

A list of things to act on (Now, Can wait, the Inbox's suggestions, Tasks, Needs attention) is rows on the
canvas, never cards:

- **A row**: an 8 px dot in the kind's hue (Tasks: the priority checkbox), the title (Inter 15–16 px / 500, ink)
  and one muted line (source · why, or the group and the triggers in words). No border around it, no filled
  background, no start bar; a hairline between rows; 12–18 px of vertical padding. A selected row sits on a faint
  accent tint, a hovered one on a faint ink wash.
- **One visible action**: only the primary shows, as a quiet pill (`.dn-pill`: accent tint and accent text;
  `.dn-pill.solid`, an ember fill, only for approvals; `.small` in compact rows). Every other action (snooze
  times, dismiss, edit, "Reply on phone", the details) is in the row's **"⋯" menu** (`.dn-overflow`, which opens
  `Dana.menu`; pages get it as `api.menu`). On a phone the "⋯" stays at the row's end and the pill goes under the
  text. Undo toasts and every existing action stay; only where they show changes.
- **The menu** (`Dana.menu(button, items, {label})` in `static/dana.js`, `.dn-menu` in `styles.css`): items
  `{label, onSelect, disabled, hint, danger, icon}` and `null` for a line. It opens under the button (above when
  there is no room), end-aligned; `role="menu"`, the focus on its first item, the arrows / Home / End move, Enter
  or Space picks, Escape (back to the button), Tab or a click outside closes; the button carries
  `aria-haspopup="menu"` and `aria-expanded`. One menu at a time; it rises in, instantly with reduced motion.
- **Greetings**: the briefing and the weekly review's banner on Tasks (the `today` piece, `static/review.js`)
  are rows too: an eyebrow ("THIS MORNING", "WEEKLY REVIEW"), the serif headline, the points as one muted line,
  Review as the pill, hide as a quiet ×.

## Navigation (`static/app-places.js`, `static/app-nav.js`)

The shell draws the navigation from **places**: the lead places Now and Chat, then every addon page with a
`Nav` (the main places, in `Nav` order), and the core group (Settings, Needs attention (with the count of failing checks not ignored), Addons, Devices, Household for a user with `manage_users`, Usage, Debug for a user with `manage_server`, and the
Dash link to `/dashboard`), drawn quieter and last. The shell names no addon page: which pages appear, and
in what order, comes from `GET /api/addons`.

- **Primary places**: up to four main places the user pins. Until they choose, Now and Chat, then the pages
  whose `Nav(..., primary=True)` asks for it (Tasks, Inbox), filled by `Nav` order up to four. A choice saved
  before Now existed gets Now in front once (`dana_nav_places_<user id>_now` marks it; removed afterwards,
  it stays removed).
- **Phone** (below 768 px): the bottom bar holds the primary places and **More**. More opens a bottom sheet
  (a dialog over the scrim) with every other place in a grid (icon, label, badge) and the core group as
  quieter rows under "Settings and tools".
- **Wide**: the rail holds the primary places, a hairline, then the rest while they fit, and the core group
  at its foot in shorter, muted rows. When the rest do not fit, the ones that do not go under a **More** row
  that opens the same grid as a flyout beside the rail (its foot level with More, moved up to stay on
  screen); on a window too short for the core group as well, it goes under More too. What fits is worked
  out from the rail's height (`railPlan`, with the row heights in `RAIL_SIZE`, matching `styles.css`) and
  again when the rail's height changes. When everything fits there is no More: a small Edit row
  (sliders icon) opens the flyout in Edit.
- **Current place and badges**: the current place has the accent pill and `aria-current="page"`; when it
  lives under More, More gets the pill. More's badge adds up the counts of the badges under it (99+ past 99;
  a badge with words only makes a dot; quiet only when every one under it is quiet).
- **Edit** (in the sheet / flyout): the primary places in order, each with move up, move down and remove
  (the last one stays), then the other places, each with add (disabled while four are pinned), and
  "Default places". Buttons only, so touch and keyboard work alike; the keyboard stays on the row it acted
  on and a status line announces the change. The choice is saved at once in the browser (`Dana.store`,
  key `dana_nav_places_<user id>`: a JSON list of ids). Dana keeps no per-user web preferences on the server,
  so the choice is per browser. Ids the user no longer has are skipped (none left: the defaults); a new
  page joins the rest by its `Nav` order.
- **The dialog**: `role="dialog"`, `aria-modal`, labelled by its title; focus moves to the current place or
  the first tile, Tab stays inside, Escape, the close button and the scrim close it, and focus returns to
  the button that opened it. Layout uses logical properties (the rail's line, badges and the flyout follow
  `dir="rtl"`). The sheet slides up and fades in (`dn-sheet`) over a fading scrim (`dn-fade`) and leaves
  the same way reversed; the flyout rises in (`dn-rise`); reduced motion makes both instant.

The pure part (places, defaults, a saved choice, what fits, what is under More, More's badge, pin / unpin /
move) is `static/app-places.js`, tested by `tests/test_web_places.mjs`.

## Now (`static/app-now.js`, `static/app-now-core.js`)

The screen the app opens on (`/`, `/#now`; `/#chat` opens Chat; the rail's brand opens Now). A core screen
(`CORE_SCREENS`), drawn only from `GET /api/now` (`docs/now.md`); every card renders from its fields and
blocks, so an addon's own cards (any `kind`) need nothing here.

- **Header**: the date as the mono eyebrow ("SUNDAY · 22 NOVEMBER", from the payload's own clock), the `lede` in
  Instrument Serif (a right-to-left lede in Inter: the serif is Latin only), then the **day track**: a 4 px
  segment per hour of the waking day (06–23, `DanaNow.dayTrack`), the hours that have begun faint sky (30 %),
  the later ones sky when their slot holds something and a faint wash when free, and a 2 px ember now-mark
  between them. Without a lede: "Nothing needs you right now." when nothing waits, else a line from the time of
  day.
- **All clear** (no cards): the header centres in the canvas under a slightly warmer static ember glow: the date
  eyebrow, the italic serif "All clear." with an 8 px sage dot under it, the track, and the server's lede (when
  there is one) as a quiet muted line under it.
- **Live** (`static/app-live.js`, `static/app-live-core.js`; `docs/live.md`, "Clients"): above the header, when
  the user has live activities, "LIVE" (after a small sage dot) and one compact card each, the one place Now uses
  cards (something running right now, not a decision): the soft canvas, a hairline, 14 px corners; a 36 px icon tile
  on the tone's tint, the title (Inter 15 / 500), the text, the context in 12 px mute, the chip as a mono pill in the
  tone; the timer at the end as a mono eyebrow over a 22 px DM Mono value with tabular digits; the bar under the text
  (4 px segments in the tones, faint ahead of the value, 8 px points, the tracker icon in a 22 px ring); then Earlier /
  Next as outlined buttons with "1/2" between, a server action as the pill and Stop quiet at the end. The tones are
  the `--tone-*` tokens (neutral sky, good sage, warn amber, bad rose, muted the mute ink). An ended one is
  transparent with a dashed hairline, "DONE" and a ×. Only new cards rise in; a timer changes its text and nothing
  else moves.
- **The day's items**: under the header, "TODAY" and one quiet wrapped line per item (`day.slots`, empty hours
  left out): a dot in its hue (reminders amber, events and the rest sky), the time in mono (or "AT HOME" /
  "ON WAKING"), the title; past ones faint, the next one in ink with its time in the accent. An item with a
  `<type>_id` opens the page that opens that type. Nothing in any slot: no items; `calendar: false` adds
  "Calendar couldn't be read".
- **Needs you**: "NEEDS YOU · N", then one row per card in server order (see "Rows, not boxes"): the dot in the
  kind's hue (approval ember, notification teal, suggestion plum or sage for a call's, reminder amber, a charge
  rose, other money sage, check and reconnect rose, review and any other kind sky; a notification shows its app's
  icon instead, 20 px with a 5 px radius, over its letter on the teal tint, which stays when the icon fails:
  `DanaNow.appIcon`, `docs/notifications.md` "App icons"), the title, one muted line
  (`source`, each " · " part isolated so a Hebrew name keeps its place, then `why`), the primary as the pill
  (`choices` change its label) and the "⋯" menu: the secondary actions, "Reply on phone" drawn disabled, then
  "Show details" / "Hide details" when the card has blocks. The details (every block type of `docs/now.md`) open
  in place under the row's text.
- **Acting**: optimistic: the row completes and collapses (`Dana.motion.complete`), the request runs
  meanwhile; the answer's `message` shows in a toast with Undo when it has an `undo_id` (`POST
  /api/undo/{id}`, then a refresh brings the card back); an error puts the card back where it was with the
  message on it. Client actions: `reply` is drawn disabled ("Reply on phone": the web cannot reach the
  phone's shade); `open` goes to an approval's thread, the weekly review, a context chat for a charge or a
  saving, else unfolds the blocks, else opens the page for the card's `ref.type` (a self-check's card, `ref`
  `check`: Settings → Needs attention on that check). A check's fix (`docs/now.md`, "Fixes"): `connect` opens
  the addon's connect flow on the Addons page, `screen` that screen, `url` an https link in a new tab; a
  server fix (`fix`) is an ordinary action.
- **Pages by ref type**: a page says what it opens with `Dana.panel({opens: ["task"]})`
  (`writing-an-addon.md`); the shell finds it with `panelOpening(type)`, so Now names no page. Tasks opens
  `task`, Calls `call` (with the id), Inbox `notification`, Calendar `event`.
- **Can wait**: folded "Can wait · N" (`quiet_total`); open, compact rows (dot, title, source, a small pill and
  the "⋯" menu) and "Dismiss all" (`quiet:all`) when there are several.
- **Notifications**: one quiet line "17 notifications · 1 needs you · 11 noise" after a teal dot (ember when
  one needs you) that opens the page for `notification` (Inbox).
- **Freshness**: refreshed when it opens, when the window gets focus or the tab becomes visible, and every
  60 s while it is the screen on show (never in the background). A refresh keeps the rows on screen as they
  are (their open state and choices too), redraws a changed card in place with `Dana.motion.flash`, and only
  new cards rise in (`.dn-new`); counts roll (`Dana.motion.number`). When a refresh fails the last data
  stays and "Updated 2 min ago" shows by the date.
- **Motion**: the screen's first data arrives with `Dana.motion.arrive` (the first eight rows staggered);
  nothing else moves on its own.
- **Keyboard**: j / k (and the arrows on a row) move between cards, Enter on a row = its primary (or its menu
  when it has none the web can do), m = the menu, e = the details. Each row is a region labelled by its title;
  user text has `dir="auto"` and lines up with the row's other text.

The pure part (hues, the date line and the lede's fallback, the day track, the day's items, the primary's label and
body, what a client action opens, removing / restoring a card, reconciling a refresh, the notifications
line, "Updated … ago", keyboard steps) is `DanaNow` in `static/app-now-core.js`, tested by
`tests/test_web_now.mjs`; the Live strip's (the shown choice, next / previous, the timer, the bar's geometry, where a
tap goes, the card's HTML) is `DanaLive` in `static/app-live-core.js`, tested by `tests/test_web_live.mjs`.

## Needs attention (`static/attention.js`, `static/attention.css`)

A core screen (`attention`), opened from Settings, the navigation's core group, a check's Now card (on that
check, brought into view and marked) or `/#attention`, drawn from `GET /api/checks` (`docs/now.md`,
"Self-check"): the failing checks in server order (connections to reconnect first) as rows on the canvas with a
rose dot (grey when the card is ignored), a mono meta line (what it concerns, "since …", "card hidden until …"),
the title, `why`, then the fix as the pill (its label) and "Not today" (a reconnect: a day) / "Ignore for a
week" / "Show on Now again" as a quiet text button beside it. A server fix shows
"Working…" and then its answer on the row. Nothing failing: Now's italic serif "All clear." with its sage dot, centred, over a muted line. The place's badge is the count of failing checks not ignored, read at start, on window
focus, after each fix and every five minutes while the tab is visible.

The Addons page shows an addon whose connection needs reconnecting with the rose "Reconnect" status, the
reason on its row, and on its view the reason in the note with the Reconnect button: the addon's sign-in
link, its fields to enter again (focuses the first), or the screen its `reconnect_fix` names.

The pure part (rows, badge count, "since", what a fix does, the HTML) is `DanaAttentionCore`, tested with the
Addons page's Reconnect by `tests/test_web_attention.mjs` against `tests/fixtures/attention_web.json`.

## Motion (`static/styles.css`, section 14; `Dana.motion` in `static/dana.js`)

Durations `--dur-fast` 120 ms, `--dur-base` 200 ms, `--dur-slow` 320 ms; easings `--ease-standard`,
`--ease-decel` (arriving), `--ease-accel` (leaving). Only opacity and transform animate (one exception: the
completed row collapsing); no blur or backdrop-filter, no animated shadow, nothing infinite outside an
active state (the listening ring, the streaming caret and typing dots, a live dot, a skeleton's shimmer
while it loads). `prefers-reduced-motion: reduce` sets every duration to 1 ms and `Dana.motion` skips its
work.

| pattern | how |
|---|---|
| switching screen | `.screen-panel.active` fades and slides 12 px from the side it lies on in the navigation (`app-nav.js` sets `--dn-dir`) |
| rows arriving | `Dana.motion.arrive(el)`: rows marked `.dn-row` (and the shell's own rows) added during the next 1.2 s fade and rise 8 px, the first eight 30 ms apart. The shell calls it on a screen's first opening only, never on a refresh |
| press | `:active` scales to 0.97 on buttons, chips, rows and nav items |
| completing | `Dana.motion.complete(row)` adds `.dn-completing` (the page draws its check and strike-through; Reminders: the check fills with the group's hue, the strike grows), then fades and collapses the row; resolves when gone (~400 ms) |
| sheets and dialogs | the scrim fades in (`dn-fade`), the card slides up (`dn-sheet`), and both leave reversed (`dn-fade-out`, `dn-sheet-out`); the navigation's flyout rises in (`dn-rise`); toasts rise in (`dn-toast`) |
| chat | a message not drawn before rises in (`.dn-new`; a session's history and older pages come in quietly); the streaming caret and typing dots are ember; the mic orb is drawn once when idle and breathes, with a pulse ring, only while listening (`app-voice.js`) |
| numbers | `Dana.motion.number(el, text)` rolls a changed value in (navigation badges) |
| skeletons | `.dn-shimmer`: one band sweeps across while loading |
| updated data | `Dana.motion.flash(el)`: an accent tint over the element fades out in ~600 ms (a reminder made active again, a shopping item arriving from the live stream) |

An addon page gets the same helpers as `api.motion` (`docs/writing-an-addon.md`).

## Dashboards (`static/dashboard.css`, `static/dashboard.js`, the addons' widget scripts)

A low-power target: a wall tablet's or TV's WebView (`docs/dashboard.md`). It has the Ink tokens and the hues
(written as plain hex and rgba, no `rgb(var() / a)`), Ink only, no glow. Each widget is a card (`.dw`, a
title line `.dw-head` as a DM Mono eyebrow unless the renderer is `bare`) on a four-column grid; widgets build on the
shared pieces of `dashboard.css` (`.dw-big` a large value in Instrument Serif, `.dw-sub`, `.dw-list` / `.dw-item` hairline rows,
`.dw-dot`, `.dw-end`, `.dw-pill` / `.active`, `.dw-bar`, `.dw-empty`, `.dw-note` for a widget that has nothing
to show, `.dw-rtl` for Hebrew or Arabic text) and keep their own CSS small and prefixed. Colour: today's date
and a selected mode in ember, calendar sources on the hues, the briefing in amber, the camera's live /
connecting / frozen badge in sage / amber / rose, a low battery or overspending in rose. Motion only for
presses (scale 0.97) and values that change after their first fill (`api.setValue`: the value crossfades,
`.dash-changed`); no entrance, stagger, shimmer or glow. The clock is set directly.

The editor (Devices → a tablet's or TV's dashboard button, `static/dashboard-editor.js`) is a plain list in
the shell's modal: one hairline row per widget (its icon, title and description, a size select, settings,
up / down, remove), the preset select above it and Add below it; a widget whose addon is gone is marked in
rose until removed.
