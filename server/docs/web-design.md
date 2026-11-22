# The web design system

The web UI (`static/`, the addons' `web/` pages and the bedroom dashboard) shares one look with the
Android apps (`../android/DESIGN.md`): a warm ink canvas, Inter body, DM Mono labels, one ember accent,
hairline rows. This page says where its colours and motion live and how a screen uses them.

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
  (accent), status pills (tint + hue text: the Addons page's On / Needs setup / Error), avatars (people,
  callers, inbox app tiles) on a hue picked by a stable hash of the name (inbox: messaging teal, calendar
  sky, calls sage, contacts plum), a reminder's group colour as a 3 px start bar on its row, money in sage
  and out / over in rose or amber, the approval box in ember. A faint static ember glow sits at the top of
  the canvas (`body`, `--glow-a`); it never moves.

## Navigation (`static/app-places.js`, `static/app-nav.js`)

The shell draws the navigation from **places**: the lead places Now and Chat, then every addon page with a
`Nav` (the main places, in `Nav` order), and the core group (Settings, Addons, Usage, Debug for a user with `manage_server`, and the
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

- **Header**: the date in DM Mono caps ("SUNDAY · 22 NOVEMBER", from the payload's own clock), the `lede` in
  Instrument Serif (a right-to-left lede in Inter: the serif is Latin only). Without a lede: "Nothing needs
  you right now." when nothing waits, else a line from the time of day.
- **All clear** (no cards): a sage hue mark and an italic serif "All clear.", above the day.
- **Day**: a horizontal strip from `day.slots`: a Now cell (accent "NOW", the time in italic serif, an
  ember line), the hours that hold something, one narrow cell for each run of empty hours, then "At home" /
  "On waking" when they hold something. Items: a dot in their hue (reminders amber, events and the rest
  sky), time, title; past ones dimmed, the next one in ink and accent. An item with a `<type>_id` opens
  the page that opens that type. Nothing in any slot: no strip; `calendar: false` adds "Calendar couldn't
  be read".
- **Needs you**: the count in the accent, then a hairline row per card in server order: a 3 px start bar and
  a dot in the kind's hue (approval ember, notification teal, suggestion plum or sage for a call's,
  reminder amber, a charge rose, other money sage, check rose, review and any other kind sky), `source` in
  mono caps (each " · " part isolated, so a Hebrew name keeps its place), the title, `why`, the blocks
  folded behind More (every block type of `docs/now.md`; `choices` change the primary's label), the
  primary filled and the secondary actions quiet. An open row sits on `--canvas-soft`.
- **Acting**: optimistic: the row completes and collapses (`Dana.motion.complete`), the request runs
  meanwhile; the answer's `message` shows in a toast with Undo when it has an `undo_id` (`POST
  /api/undo/{id}`, then a refresh brings the card back); an error puts the card back where it was with the
  message on it. Client actions: `reply` is drawn disabled ("Reply on phone": the web cannot reach the
  phone's shade); `open` goes to an approval's thread, the weekly review, a context chat for a charge or a
  saving, else unfolds the blocks, else opens the page for the card's `ref.type`.
- **Pages by ref type**: a page says what it opens with `Dana.panel({opens: ["task"]})`
  (`writing-an-addon.md`); the shell finds it with `panelOpening(type)`, so Now names no page. Tasks opens
  `task`, Calls `call` (with the id), Inbox `notification`, Calendar `event`.
- **Can wait**: folded "Can wait · N" (`quiet_total`); open, compact rows with their actions and "Dismiss
  all" (`quiet:all`) when there are several.
- **Notifications**: one line "17 notifications · 1 needs you · 11 noise" that opens the page for
  `notification` (Inbox).
- **Freshness**: refreshed when it opens, when the window gets focus or the tab becomes visible, and every
  60 s while it is the screen on show (never in the background). A refresh keeps the rows on screen as they
  are (their open state and choices too), redraws a changed card in place with `Dana.motion.flash`, and only
  new cards rise in (`.dn-new`); counts roll (`Dana.motion.number`). When a refresh fails the last data
  stays and "Updated 2 min ago" shows by the date.
- **Motion**: the screen's first data arrives with `Dana.motion.arrive` (the first eight rows staggered);
  nothing else moves on its own.
- **Keyboard**: j / k (and the arrows on a row) move between cards, Enter on a row = its primary (or More
  when it has none the web can do), e = More. Each row is a region labelled by its title; user text has
  `dir="auto"`.

The pure part (hues, the date line and the lede's fallback, the day strip's cells, the primary's label and
body, what a client action opens, removing / restoring a card, reconciling a refresh, the notifications
line, "Updated … ago", keyboard steps) is `DanaNow` in `static/app-now-core.js`, tested by
`tests/test_web_now.mjs`.

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

## The bedroom dashboard (`static/dashboard.css`, `static/dashboard.js`)

A low-power target: the wall tablet's WebView. It has the Ink tokens and the hues (written as plain hex and
rgba, no `rgb(var() / a)`), Ink only, no glow. Colour: today's date and the selected AC mode in ember,
calendar sources on the hues, the briefing in amber, the live / connecting / frozen badge in sage / amber
/ rose. Motion only for presses (scale 0.97) and values that change after their first fill
(`setValue`: temperature, humidity, batteries crossfade, `.dash-changed`); no entrance, stagger, shimmer or
glow. The clock is set directly.
