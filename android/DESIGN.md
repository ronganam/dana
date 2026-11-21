# Dana Design System v2 — "Ink & Paper"

A personal chief-of-staff, not a chatbot. The UI reads like a well-set notebook: serif headlines, mono metadata, hairline rules, one vermilion accent. **Avoid generic-AI tropes**: no purple/blue gradients, no glowing orbs, no "How can I help?" hero, no icon-in-circle empty states, no card-per-row lists.

## Tokens (`ui/theme/`)
- **Palette**: Dark "Ink" canvas `#13100E`; Light "Paper" canvas `#F3EDE1`. One accent: vermilion (`#FF6A3D` dark / `#D1401A` light). Status hues (`Success/Warning/Info/Danger/Neutral`) are semantic only. Never hardcode colours in screens — use `MaterialTheme.colorScheme.*` or the tokens in `Color.kt`.
- **Type**: `headline*`/`display*` = Instrument Serif (editorial titles, big dates, empty-state statements; Latin only). Body/title = Inter. `labelSmall/labelMedium` = DM Mono — use `MonoLabel(text)` (uppercases) for counts, times, kickers, ids. Chat/user content stays Inter (Hebrew/RTL support!).
- **Shape**: 6/10/14/18/28dp. Prefer hairline-ruled rows (`Rule()`) over boxed cards; use `GlassCard` only for genuinely grouped content (forms, settings).
- **Spacing**: `Space.*` (4dp scale), 16dp gutters.

## Adaptive layout (`ui/adaptive/Adaptive.kt`) — Galaxy Z Fold target
Real windows (360 dpi): cover ≈ 555×876 dp, inner ≈ 1088×821 dp (821×1088 rotated). Check every new screen at all three (emulator: `wm size 1248x1972` / `2448x1848` / `1848x2448` + `wm density 360`). Use three panes only with ≥ ~900 dp of content width; split a detail pane into story | tinted aside from ~600 dp of pane width (People).
`windowTier()` from live width: COMPACT <600 (cover screen/phone: bottom bar, single pane) · MEDIUM 600–839 (unfolded portrait: rail + list + content) · EXPANDED ≥840 (unfolded landscape/tablet: rail + list + content + context pane). Use `PaneRow` / `Pane(width=…)` with `PaneWidth.list/listNarrow/context`. The activity handles fold/size configChanges itself, so **state must survive a fold/unfold** (hoist in ViewModel / `rememberSaveable`, don't rely on recreation). Never assume a fixed width; panes must degrade gracefully tier by tier.

Per-screen pane plan (EXPANDED → MEDIUM → COMPACT):
- **Reminders** (main-screen shell): list (tinted) | editor | thread, under the day strip → list | editor, thread as a sheet → list → editor drill-down.
- **Memory**: Categories | Memory list | Memory detail → list | detail → list.
- **Projects** (a wide place): side list (projects, the open project's sections) | project or section page (two columns; the compare table across the width, option names fixed) | entry pane beside it from a 760dp stage, else over it → one level at a time on the cover (projects → project → section → entry), Back pops a level.
- **Money** (a wide place, under More): side pane (header, month, banners, views) | the view (two columns from 600dp) | transaction / saving pane beside it from a 760dp stage → cover: header, views strip, one column, the item full screen.
- Hebrew titles and names take their own width (not a full-width box) so they stay at their start beside a number, like the web's `<bdi dir="auto">`; `MonoLabel` is one line, so anything that can run long gets wrapping text.
- **Calls**: Call list | Call detail (summary, transcript) | Actions/people/tasks extracted → list | detail → drill-down.
- **People**: People list | person (summary + history) | At a glance (facts, reminders, notes) → list | person (one column) → drill-down.
- **Weekly review**: summary + section jumps + Apply (side) | items → one column with a sticky Apply bar.

## Components (`ui/components/DesignSystem.kt`)
`ScreenTopBar(title, kicker=…)` (serif title, mono kicker), `ScreenTopBarSlots`, `MonoLabel`, `Rule`, `EmptyState`, `SectionLabel`, `StatusPill`, `GlassCard`, `InkChoice` (outlined 10dp choice chip; accent outline when selected, `solid` = filled primary action). Navigation: Material 3 `NavigationBar` (compact) / `NavigationRail` (≥600dp).

## Power-button assistant (`GanamVoiceInteractionSession`, `FloatingAssistantOverlay`)
Quick, glanceable, one-handed: a bottom sheet anchored to the thumb zone over the current app, not a full chat window. Large live transcript, one primary voice control, compact result cards, easy dismissal, works on cover screen and unfolded inner screen (560dp, centred).
It is the shared thread (device `phone`), never a new session: the same `UndoRows` as the thread under replies, the "Dana asks first" card only for tools that still ask, "Dana · learned phrase" on shortcut replies, and a BARE "Open the thread in Dana →" after the first turn. It scrolls to its end when a turn finishes.
**Screen** (`ui/components/assistant/ScreenControl.kt`, phone only): a 44dp chip left of the input, in thumb reach and tappable while listening. It is off every time the sheet opens: hairline outline with a `CropFree` icon. On, it is filled vermilion with a check. It must never be ambiguous. When it is on, a status line sits above the dock: a 3dp accent bar, a 56dp-high thumbnail, the MonoLabel "Screen goes with this message", "App · host or title", one line for anything missing ("Text only · screenshots are off in settings" with a BARE Settings button, "Text only · this app blocks screenshots", "Screenshot only · no text from this app") and a × to leave it out. When it is unavailable, the chip is muted and reads "Screen off" or "No screen". A tap explains why in one line: "Screen sharing is off in the assistant settings." with a PRIMARY "Turn on" (opens the system assistant switches), or "This app doesn't let assistants see its screen." The chip is hidden when the sheet was opened from Dana itself (widget, Dana in front) and on kiosk/TV. Sent messages carry a MonoLabel caption "Screen · App" in the accent colour, plus "· text only" when no image went. The approval card reads "Asking because the screen was shared." when the server asks because of it.


## Pane spec (all multi-pane screens — keep columns identical across tabs)
`[ global header (ScreenTopBar: kicker + serif title + actions) ]` over `[ SIDE 280dp tinted | MAIN flex | (MAIN flex) | ASIDE 288dp tinted ]`.
Widths via `sidePaneWidth()` / `asidePaneWidth()` (248/256dp under 960dp wide). Panes have no headers of their own (use a `MonoLabel` row). Only SIDE and ASIDE are tinted; the main/detail panes never are. The nav rail appears from 720dp; below that a bottom bar. 960dp+ is the Fold unfolded landscape: SIDE + MAIN + ASIDE.

## Main screen shell (`ui/shell/`, phone; spec: `docs/design/dana-mock.html`)
Tiers come from window width, independent of `windowTier()`: **STACKED** < 600 dp (cover) · **SPLIT** 600–899 (unfolded portrait) · **FULL** ≥ 900 (unfolded landscape).
- FULL: `[ list SIDE (tinted) | day strip across stage + thread ]` over `[ list | stage | thread ASIDE (tinted) ]`; nav at the bottom of the list column, ask bar at the bottom of the thread column. A place with no stage spans list + stage and gets a title cell top-left.
- SPLIT: day strip on top, list (tinted) | stage, thread as a sheet, ask bar + nav at the bottom.
- STACKED: one column (list, or the stage when something is picked), thread as a sheet over it, a one-line thread peek + ask bar, then a slim row of the four places (hidden while the keyboard is up).
- Nav: mono uppercase labels, a 2dp accent line above the current place, counts in accent. No icons.
- Rows: hairline-ruled; selected = canvas background + 3dp accent bar at the start. Titles decide direction per line (Hebrew right-aligned).
- Now rows (`ui/screens/now/`): a 30dp leading tile (`NowTile`, same rounded square as the Notifications app tiles; 38dp on the stage, 24dp on quieter suggestions) says where the item came from: the app's real icon for anything from a notification, else a muted outline glyph for the kind (call, reminder, check, review, notification, calendar, alert); approval and alert glyphs are the only accent ones. Then mono source kicker, semibold title, muted why (buttons align under the text, the open evidence panel stays full width); cover rows expand in place and swipe (right = primary, left = dismiss, the revealed side says which). Unfolded the stage holds evidence then a "Decide" rule with the choices/checks and a SOLID primary. Check rows lay out in their label's own direction. Empty Now = italic serif "Nothing needs you." + one muted line, no illustration.
- Notifications: a filled noise box (SOLID "Clear the noise · N"), an outlined "Dana noticed" box, app rows with a 30dp icon tile (2-letter fallback), mono flags in accent, summary points per line by script; the app page uses `InkChoices` for the three-way rule.
- Search (`SearchPane`): mono group labels over hairline rows; the last row "Ask Dana: “…”" in accent with ↵.
- Thread (`ThreadPane`): quiet separators (mono "13:10 · watch" between hairlines) where a new conversation started, "Loading earlier…" at the top, "Nothing said yet." when empty. Under a reply: `InkChoice` "Undo" + the label; afterwards a plain line "Undone." / "Too late to undo that." (muted detail), never a red error. Learned-phrase replies get a mono "learned phrase · no model" caption. Tools that ask: a "Asks first" chip, "Dana asks first", Cancel / Go ahead.
- On screen: unfolded, a mono "On screen · title" line with an accent start bar and a 40dp × above the ask field; on the cover the same line is pinned at the top of the thread sheet.
- About you (More): one centred column (≤ 680dp), facts as editable lines saved on blur/Done with a muted source caption, × to forget, "N of M characters", learned phrases, self-check rows with a status dot. Every change offers Undo.
- Caller card (plain Views over the dialer): the same tokens, Ink or Paper from the theme setting; body lines start on their own script's side; 560dp centred when unfolded.
- Day strip: an 84dp Now cell (italic serif time), hour cells that fit the width (2–8), At home, On waking; events on a surface chip, reminders in muted text; the hovered cell is tinted while dragging.
