# Dana Changelog

## [Shopping list remake] - 2026-11-16

- Shopping List: sections by aisle from the server, quantity and note on each item, typed text added as items (`2 milk, bread`), Mark Bought / Back on the List, Clear Bought Items, Undo on each change's toast (the server's undo ids), a shared list shows its owner and who added each item; read-only lists offer no changes.
- AI tools: `add-shopping-items` takes each item with its own quantity and note; `get-shopping-list` returns quantity, note, aisle and whose list it is.

## [Rebuild] - 2026-11-11

- Ask Dana streams replies live over the server's SSE run API: reasoning, tool steps, retries, final answer.
- Write actions now wait for Approve / Deny instead of being approved automatically. Choice prompts and step-limit pauses are handled too.
- Model picker (Fast / Balanced / Deep) and a launch argument to start a new chat with a message.
- Reopening a chat reattaches to a run that is still going; 409 "busy" responses attach instead of failing.
- New Search Chats command covering every Dana conversation.
- Manage Tasks: due-date sections, group filter, details pane, snooze, priority and group submenus, edit form, delete confirmation, optimistic updates.
- New Create Task, Shopping List and Tasks in Menu Bar commands.
- Raycast AI tools: ask-dana, respond-to-dana, get-tasks, create-task, update-task, get-shopping-list, add-shopping-items, with confirmations for write tools.
- Updated to @raycast/api 2.x, TypeScript 6 and ESLint 9 flat config.

## [Initial Version] - 2025-07-20

- Chat with Ganam and Manage Tasks commands.
