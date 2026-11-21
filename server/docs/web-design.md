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
| sheets and dialogs | the scrim fades in (`dn-fade`), the card slides up (`dn-sheet`); toasts rise in (`dn-toast`) |
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
