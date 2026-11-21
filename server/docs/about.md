# About you (what Dana knows about each user)

Short plain-text facts Dana keeps about a user — who people are, what "the gate" means, standing preferences — always in that user's system prompt, each one editable and removable on the app's **About you** page. Replaces the old Memory tab. Everything here is **per user** (the `about` addon, built in, no sharing): facts, "do not re-add", the size cap, the prompt block, the learner's bookmarks and back-off. Another user's fact id is "not found" (404 / no match) exactly like an id that does not exist; a user without `about` connected gets no About section, no memory tools and 403 `{"error": "not_connected", "addon": "about"}` on every endpoint below.

Code: the package `addons/about/` — `values.py` (sections, limits, the text rules: clean, duplicate, "do not re-add", secrets, section guess), `facts.py` (each user's facts: store, cap, lookups; rebuilds their prompt block), `prompt.py` (the block and its per-user cache; the addon's prompt section), `legacy.py` (the old `memories` table, the old `/api/memories` shape), `learner.py` (the per-user learner), `api.py` (routes, starts the learner), `tools.py` (the memory tools), `addon.py`, `web/memory.js` + `memory.css` (the web Memory page, on `/api/memories`). Tests: `bin/test test_about` (one user), `bin/test test_about_users` (several users, the migration).

## Facts

Table `about_facts` (the addon's `migrations/m0001_baseline.py`):

| column | |
|---|---|
| `id` | 10 hex chars |
| `user_id` | whose fact it is (every row, active or not); existing rows went to the first admin |
| `section` | `people` · `home` · `habits` · `other` (shown as People / Home / Habits / Other) |
| `text` | one line, ≤ 280 chars |
| `status` | `active` · `forgotten` (the owner removed it, or pressed Undo on a save) · `dropped` (pushed out by the size cap) |
| `source_kind` | `chat` · `call` (learned) · `told` (Dana saved it with `remember_info`) · `owner` (added on the page / old Memory screen) · `edited` (owner edited it) · `imported` (copied from the old `memories` table) |
| `source_ref` | session id / call id / old memory key |
| `source_label` | display text, e.g. "a call with אמא"; else derived from the kind ("a chat", "you told Dana", "you added this", "you edited this", "your old Memory list") |
| `created_at`, `updated_at`, `confirmed_at`, `inactive_at` | UTC ISO 8601 |

- **Duplicates**: a new fact that says the same as one of the user's active facts (same normalised text, or ≥ 75 % of the content words and ≥ 0.8 character similarity) is not added again. Names count: "Dan is Maya's brother" and "Eli is Maya's brother" are two facts. The user's own name is not a content word. Two users may each have the same fact.
- **Forgotten** facts are kept (newest 300 inactive rows per user) so neither the learner nor the tools add them back for that user in any wording (≥ 70 % of content words shared). The user can still add the same text on the page; other users are not affected.
- **Secrets**: the tools and the learner refuse passwords, PINs, one-time / verification codes, card / account-length numbers (13+ digits), API keys and tokens (`values.looks_sensitive`). The user can type anything on the page.
- **Size cap** (per user): a user's active facts are kept under `ABOUT_MAX_CHARS` (4000) and `ABOUT_MAX_FACTS` (80). Over it, facts are marked `dropped` in this order: learned from chats/calls (least recently confirmed first), then imported, then the owner's own (oldest first). The fact just added or edited is never the one dropped. Dropped facts are not "do not re-add": the learner may learn them again if they come up.

## Prompt section

The `about` addon declares `Addon(prompt_section=prompt.section)`; `core/agent.py::about_section(user)` asks `core.addons.registry.prompt_section(user, "about")` on every turn for the run's user ("" without a user, without `about` connected, or when the section fails). The block is placed after the home map and before the date/time line (the agent decides where each addon's section goes; Home Assistant's home map will use the same hook):

```
--- ABOUT ROBIN (what you know) ---
Things you know about Robin. Use them directly: resolve people, nicknames, places and devices from here instead of asking or looking them up, and follow the preferences. Never mention or recite this list unless asked what you know.
People:
- אמא calls most evenings
Home:
- 'The gate' means the front gate
-----------------------------------
```

The name is the user's `name` (`core/users.py`; the first admin's comes from `OWNER_NAME` when it is created, default `Owner`; change it with `bin/dana users update`). The cache holds one block per user: built at startup for every user with `about` (the `load_prompt` job, `hooks.py`), when the addon is connected (`on_connect`) and after every change to that user's facts; `prompt.section(user)` never touches the DB, never blocks and never raises. No facts → `""`. Hard cap `ABOUT_MAX_CHARS + 800` characters (whole lines only).

## Migration from `memories`

`memories` is not per user, and other code still keeps its own rows there (below). Its real memories belong to one user: the one marked by the `about_state` row `legacy_memories`, which Dana 9.0 gave to the first admin of a database that existed before users (a database created later has no such user; it never had real memories). Only that user gets the copy, the "older notes" in `recall_info`, old keys in the Telegram `/memory` pickers and the deletion of an old key (`forget_info`, `DELETE /api/memories/{key}`); everyone else gets nothing from `memories` (`addons/about/legacy.py`).

On every start (`legacy.migrate_legacy()`, the `load_prompt` job in `hooks.py`), `memories` rows that are real memories become that user's `imported` facts, once per key (`legacy_key` is unique, so a fact the owner removed is not re-imported). The `memories` rows themselves are left untouched. Not copied:

- `category = 'system'` rows: wake state that `core/wake.py` and `core/checks.py` read and write (the notification digest's old `notif_digest_*` rows were moved to `notification_digest_state` by Dana 9.0);
- `selected_calendars` (the calendar picker's old state; Dana 9.0 moved it into the first admin's `calendar` addon and removed the row);
- credential-looking rows (key or category mentions password / token / secret / api key / credential / pin / otp, or the value looks sensitive). These stay answerable by `recall_info` as "older notes".

Text: `Key words: value` (e.g. `wife_phone` → "Wife phone: 0501234567"), or just the value when it already contains the key words. Section from the old category (contact(s)/family → people, preference(s) → habits, otherwise guessed from the text).

## The model's tools (`addons/about/tools.py`)

Same names as before, backed by facts. They work on the connection's user's own facts, run at once (registered without `is_write`, so no approval under any registry version; `remember_info` is `acts_outside`, so it asks in a turn carrying a shared phone screen) and record an undo (`core/undo.py`, the user's own), so the agent attaches the Undo button to the tool call.

| tool | |
|---|---|
| `remember_info(fact, section?, replaces?)` | Stores a fact (`told`). The docstring tells the model to save durable facts on its own, one short sentence each, and never secrets / one-time details. `replaces` (id or text) rewrites an existing fact instead. Refuses duplicates ("Already known"), forgotten facts and secrets. Undo → the fact becomes `forgotten`. |
| `forget_info(fact)` | By id or text (ambiguous → the candidates with ids). Marks it `forgotten`; undo restores. An old `memories` key (the Telegram `/memory` picker still lists them to their owner) deletes that row, with undo — only for the owner of the old memories. |
| `recall_info(query)` | Searches facts (with ids) plus unmigrated old notes (their owner only). |
| `list_memories(section?)` | Facts by section, with ids. |

The old `key` / `value` / `category` arguments are still accepted (Telegram `/memory`, old history) without being shown to the model. Telegram `/memory` (commands, buttons, typed input) runs these tools as the sender's user, so it works on the sender's own facts.

## Learner

The `learn` job (`addons/about/hooks.py`, an `@addon.job`) runs **per user**: every user with `about` connected gets a pass 2 min after start, then one every `ABOUT_LEARN_INTERVAL_MINUTES` (the job looks every minute whether the user's pass is due, `learner.run_if_due(user)`); each pass runs as its user (`users.acting_as`: the model call's usage row is theirs), one user after another, and one user's failure never stops the others. Each pass:

1. **Collects** what finished since that user's bookmark (`about_state` row `(user, 'learner')`):
   - the user's own conversations (`sessions` of that user, `main` included) quiet for `ABOUT_LEARN_QUIET_MINUTES` and not running / waiting for approval. Only the messages after the last one it read (stored as a hash per session, so the 40-message Telegram window works) — user and assistant text, tool results left out;
   - the user's calls with `status = 'ready'` (title + summary, no transcript); calls still `processing` hold the bookmark until done. The one place that reads calls is `learner._calls_of(conn, user, after)` (`phone_calls.user_id = ?`: only the user's own calls).
   - First run looks back `ABOUT_LEARN_LOOKBACK_DAYS` (14).
2. **Skips** trivial items without a model call: no user text, under 12 characters, or a single short message whose only tools were home control / time / shopping (`TRIVIAL_TOOLS`); low-importance, failed or summary-less calls.
3. **Asks once**: up to `ABOUT_LEARN_MAX_ITEMS` (8) items and 14 000 characters (each item ≤ 2 500) in **one** `complete_json` call (job `briefs` → `BRIEFING_MODEL` → `BACKGROUND_MODEL`; an `about` job is used instead if one is added to `core/llm.py`). The prompt holds the user's name, their current facts (with ids and kinds), their "do not re-add" list, the space used and strict rules: most conversations teach nothing; keep only what will still be true in a month (who people are, what names refer to, standing preferences, routines); no one-time events, plans, credentials, health or financial details, nothing about third parties beyond recognising them; one short line in the owner's language.
4. **Applies** at most 5 adds/updates, re-checking length, secrets, duplicates and "do not re-add". Updates are allowed only on facts learned from chats/calls (never the owner's own). New facts carry their source (`a chat`, `a call with <caller>`).
5. Saves the bookmark only after a successful pass. Items over the batch cap wait for the next pass.

**Cost bounds**: one model call per pass at most, none when nothing non-trivial is new; ≤ 8 items / 14k characters of input per call; ≤ 5 changes per pass. **Failure**: that user's bookmark stays, and their next pass waits interval × 2^failures (max 6 h, counted per user); a success resets it. **No API key** for the model: passes are skipped with one warning. `ABOUT_LEARN=0` turns the loop off.

## HTTP API (`addons/about/api.py`)

Bearer auth as everywhere; every route answers for the request's user, who needs `about` connected (else 403 `not_connected`). Another user's fact id answers 404, their undo id 404. Undo ids go to `POST /api/undo/{id}` (→ `{ok, label}`, 404 when expired after 15 min or used).

`GET /api/about`
```json
{
  "sections": [
    {"key": "people", "label": "People", "facts": [
      {"id": "3f9c0a1b2c", "text": "אמא calls most evenings", "source": "a call with אמא",
       "source_kind": "call", "source_ref": "<call id>",
       "learned_at": "2026-11-14T09:12:00+00:00", "updated_at": "2026-11-14T09:12:00+00:00"}
    ]},
    {"key": "home", "label": "Home", "facts": []},
    {"key": "habits", "label": "Habits", "facts": []},
    {"key": "other", "label": "Other", "facts": []}
  ],
  "used_chars": 23,
  "max_chars": 4000
}
```
All four sections are always present, in this order; hide empty ones. Facts are oldest first. Show `source` as "learned from …".

`POST /api/about/facts` `{"text": "...", "section": "people"}` (`section` optional, guessed when omitted) → **201** `{"ok": true, "fact": {...}, "created": true, "undo_id": "..."}`; **200** with `created: false`, `undo_id: null` and the existing fact when it is already known; **400** `{"ok": false, "error"}` for empty / too long text or an unknown section.

`PUT /api/about/facts/{id}` `{"text"?, "section"?}` → `{"ok": true, "fact": {...}, "undo_id"}` (source becomes "you edited this" / `edited`); 400 bad input or nothing to change; 404 unknown or not active.

`DELETE /api/about/facts/{id}` → `{"ok": true, "undo_id": "..."}` (the fact becomes `forgotten`); 404 unknown or already removed.

`fact` objects in POST/PUT replies: `{id, text, section, source, source_kind, source_ref, learned_at, updated_at}`.

### Old `/api/memories` (current Android and web Memory screens)

Served from the caller's facts, in the old shape (handlers in `addons/about/api.py`, calling `legacy.py`; 403 `not_connected` without `about`):

- `GET /api/memories` → `[{"key": "fact_<id>", "value": <text>, "category": <section>, "updated_at", "source"}]`, sorted by section. System rows and other old `memories` rows are no longer listed.
- `POST /api/memories {key, value, category}` → key `fact_<id>` edits that fact of the caller (category = a section moves it); any other key (also another user's `fact_<id>`) adds a fact for the caller (`"Key words: value"`, section from category, source `owner`). Reply `{"status": "created", "key": "fact_<id>"}`.
- `DELETE /api/memories/{key}` → `fact_<id>` forgets that fact of the caller (`{"status": "deleted", "key", "undo_id"}`); any other key deletes that old `memories` row as before, only for the owner of the old memories; 404 otherwise.

## Configuration

| Variable | Default | |
|---|---|---|
| `ABOUT_LEARN` | `1` | learner on/off |
| `ABOUT_LEARN_INTERVAL_MINUTES` | `30` | minutes between a user's passes (doubled per failure of that user, max 6 h) |
| `ABOUT_LEARN_QUIET_MINUTES` | `20` | a conversation counts as finished after this long without changes |
| `ABOUT_LEARN_MAX_ITEMS` | `8` | conversations + calls per model call |
| `ABOUT_LEARN_LOOKBACK_DAYS` | `14` | how far back the first pass looks |
| `ABOUT_MAX_CHARS` | `4000` | size cap of a user's active facts (prompt block ≤ this + 800) |
| `ABOUT_MAX_FACTS` | `80` | count cap of a user's active facts |

The name in the prompt block and the learner prompt is the user's `name`, the learner's pronoun theirs (`core/user_profile.py`; `OWNER_NAME` only names the first admin when it is created).

Model: `BRIEFING_MODEL` → `BACKGROUND_MODEL` (see `docs/configuration.md`).
