# Dana for Raycast

Raycast extension for **Dana**, the self-hosted assistant. It talks to the server in this repo over the same REST and
Server-Sent Events APIs the web UI and the Android app use.

## Commands

| Command               | What it does                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Ask Dana**          | Chat with Dana. Replies stream in live, with her reasoning and each tool she runs. Actions that change things (smart home, WhatsApp, tasks…) wait for you to **Approve** or **Deny**. Choice prompts and step-limit pauses are answered from the action panel. Pick the model (Fast / Balanced / Deep) from the search-bar dropdown. Takes an optional argument, so `Ask Dana  turn off the lights` starts a fresh chat right away. |
| **Search Chats**      | Every conversation with Dana, from Raycast, the web, the phone and Telegram. Open one to continue it, or delete it.                                                                                                                                                                                                                                                                                                                 |
| **Manage Tasks**      | Open tasks grouped by Overdue / Today / Tomorrow / Upcoming. Complete, edit, snooze (time, wake-up, car), set the priority, move to a group, delete. Filter by group or show completed tasks.                                                                                                                                                                                                                                       |
| **Create Task**       | Form with title, notes, due date, priority and group ("Let Dana Choose" lets the server pick the group).                                                                                                                                                                                                                                                                                                                            |
| **Shopping List**     | The shopping list in sections by aisle, with each item's quantity and note (a shared list shows its owner and who added what). Type to search; press ↵ to add what you typed: `2 milk, bread` adds two items, the amount parsed. Mark bought or back on the list, remove, clear bought items, copy the list. Every change's toast offers **Undo** (⌘Z).                                                                             |
| **Tasks in Menu Bar** | Off by default. Shows how many tasks are overdue or due today; click a task to complete it. Refreshes every 10 minutes.                                                                                                                                                                                                                                                                                                             |

## Raycast AI

The extension is also an AI extension: mention `@dana` in AI Chat or Quick AI.

| Tool                                                                                                  | Confirmation                                              |
| ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `ask-dana`: send any request to Dana (smart home, calendar, WhatsApp, calls, notifications, memories) | none (Dana asks for approval herself, see below)          |
| `respond-to-dana`: approve or deny what Dana asked for, pick one of her options, or let her continue  | shows the exact action and its arguments before approving |
| `get-tasks`, `get-shopping-list`                                                                      | none                                                      |
| `create-task`, `update-task`, `add-shopping-items`                                                    | yes                                                       |

When Raycast AI uses `ask-dana` and Dana wants to run a write action, the tool reports what she is waiting for. Raycast
AI then calls `respond-to-dana`, and Raycast shows its confirmation with the action's details. These AI conversations go
to a single Dana chat called `raycast-ai`, which you can also open from **Search Chats**.

## Setup

Preferences (Raycast Settings → Extensions → Dana):

- **Server URL**: e.g. `https://dana.example.com`
- **API Token**: the server's `API_TOKEN`. Sent as `Authorization: Bearer …`.
- **Default Model**: model profile for new chats.
- **Show Dana's reasoning and tool steps**: show the thinking and tool lines above each reply.

## Development

Needs Node 22.22.2 or newer (`.nvmrc` pins 22). The `ray` CLI from `@raycast/api` 2.x fails to load its commands on older Node.

```bash
nvm use          # or: brew install node@22 / fnm use
npm install
npm run dev      # ray develop: loads the extension into Raycast with hot reload
npm run build    # ray build: type-checks and builds into dist/
npm run lint     # ray lint: manifest, ESLint, Prettier
```

`npm run build` and the ESLint/Prettier steps of `npm run lint` also run on Linux. Testing in Raycast needs macOS or
Windows.

`npm run lint` checks that `author` in `package.json` is a real Raycast Store handle (`ronganam`); to publish your own copy, put yours there. `npm run dev` works without it.

### Layout

```
src/
  ask-dana.tsx, chat-history.tsx, manage-tasks.tsx, create-task.tsx, shopping-list.tsx, menu-bar-tasks.tsx   commands
  components/   Chat (streaming chat), ChatHistory, TaskForm, SelectionForm
  lib/api.ts    HTTP client: Bearer auth, SSE reader, typed endpoints
  lib/chat.ts   session history → turns, live run event reducer, markdown rendering
  lib/ai.ts     helpers for the AI tools (run a request to the end, describe what Dana waits for)
  tools/        Raycast AI tools
```

The chat follows the server's run model (`core/runs.py`). Each request is a _run_ whose events (`text_delta`,
`reasoning_delta`, `tool_start`, `approval_required`, …) stream as SSE and end with an authoritative `state` frame.
Closing Raycast never cancels a run. Reopening **Ask Dana** reattaches with `GET /api/sessions/{id}/stream`, and a
`409 session_busy` attaches to the run that is already going.
