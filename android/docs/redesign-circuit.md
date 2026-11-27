# Phone redesign: "Circuit" (Nov 2026)

The owner picked this design after six rounds of mockups (the Design canvas "Dana Android Concepts", Round 6).
It is the target for the phone app. This file is the build spec; screens land in phases, each shipped as a
dev build (`bin/deploy-app.sh --dev`).

## Language
- **Night violet, dark-first.** The app opens dark; a light theme stays available and follows the same
  roles (lavender paper instead of night).

  | Token | Dark value |
  |---|---|
  | Canvas (base) | `#14101C` |
  | Surface | `#1C1726` |
  | Surface high | `#241E30` |
  | Surface highest | `#2E2740` |
  | Ink | `#F2EEF8` |
  | Ink soft | `#B9B0C9` |
  | Mute | `#8C849C` |
  | Accent (Dana and actions) | `#B9A5FF` |
  | Filled button | `#C9B8FF` with dark text |
  | Needs you | `#FFB27A` (warm) |

  Warm is used **only** for "needs you" and late, never as decoration. A green appears only where something
  must read as done.
- **Line with nodes** is the structural motif, never ornament. It shows a watch's circuit, a run's steps, Now's
  timeline, a reminder's moments, and the tool steps in a conversation. Node shapes mean the same thing
  everywhere:

  | Shape | Means |
  |---|---|
  | Circle | Source or moment |
  | Slot (rounded bar) | Rule |
  | Diamond | AI check |
  | Rounded square | Action or done |

  Stroke weights and spacing are also shared.
- **Type**: Roboto Flex (Google Fonts downloadable font, with system sans as the fallback). Sizes: display
  34–40 for screen titles, title 22, body 16, supporting 14, and 12 only for axis and time labels. Times use
  tabular figures.
- **Surfaces**: tonal groups with no borders and 20–28 dp radius. Rows inside a group are joined (2–3 dp gaps,
  small inner radius). Layout uses 16 dp margins and an 8 dp rhythm. Every row aligns to the same columns:
  leading node or icon, content, then trailing time, state or action.
- **Motion only where something is live**: a pulse travels a running line, and a node waiting for you breathes.
  Nothing else moves. Honour the system's reduced-motion setting.
- **Not "vibecoded"**:
  - no tiny uppercase labels;
  - no chips on every row;
  - no glow dots;
  - no stat walls;
  - no cute copy.

  Sentences are plain and short.

## Navigation
- Folded: a bottom bar with Now · Reminders · Watching · Inbox · More, with a pill indicator. Unfolded: a rail
  with the same places, and the conversation pane beside the content.
- Inbox is today's Notifications place. Projects moves into More.
- Ask Dana is a tonal mic button in the top-right header slot of every folded place. Chat keeps its docked
  ask bar, and the rail has a mic at the top.

## Screens (phases)
1. **Foundation**: tokens, type, shapes, the node and line components (`ui/components/circuit/`), the nav
   change, and the Watching place stub.
2. **Watching** (`/api/watches`, `/api/watches/{id}/trace`, `/api/watches/pipeline/stream`, `PATCH` to pause,
   `/api/watches/dry-run` with `POST /api/watches` to create; agent runs and their Approve / Deny come from live
   activities):
   - **The list**: the circuits, grouped, with the waiting run's card expanded under its watch and the running
     run's steps.
   - **One watch**: its circuit large, today's counts, checks grouped by outcome with their reasons, Pause, and
     change it in words.
   - **New watch**: start from words, then show the compiled circuit, run the 14-day dry run, and turn it on.
   - **Unfolded**: each watch's line gets a track of today's events.
3. **Now**:
   - **Needs you**: approvals, escalated messages and late reminders, the waiting one first.
   - **Going on**: live activities (runs with their step line, coding sessions, trains).
   - **Next**: a vertical timeline with time and place nodes, built from the day slots and reminder moments.
   - **Calm state**: when nothing needs you, Now says so and shows a day line of what Dana handled.
4. **Reminders**:
   - **The list**: groups by the moment a reminder reaches you (Late · On waking · Today · Places & car · No
     moment), drawn as nodes on a line, with Dana's snooze suggestion inline.
   - **Moment picker**: a sheet.
   - **Unfolded board**: tray | next 24 h as a line of moment nodes | anytime. Drag a reminder onto a node to
     set its trigger.
5. **Conversation**: tool results as cards with their step line, and sends that ask first in the warm card. The
   same cards appear in the long-press assistant overlay and the incoming-call card.
