# Usage and cost

What every model and decision-model call costs, where it came from, and what it was for. Web screen **Usage** (rail, or Settings → Usage & Cost on a phone; deep link `/#usage`).

Code: `core/usage.py` (capture, storage, roll-up, reads), `core/budget.py` (the monthly budget), `core/usage_api.py` (routes), `static/usage.js` + `usage.css`. Tests: `bin/test test_usage`, `bin/test test_budget`, `bin/test test_multi_user_core` (scoping).

## What is tracked

| `kind` | Calls | How it is captured | Price |
|---|---|---|---|
| `llm` | agent chat (every ReAct step), `llm.complete` / `complete_json` jobs | LiteLLM callback | LiteLLM's price table, from the provider's token counts (cached and reasoning tokens included) |
| `transcribe` | `llm.transcribe` (voice notes, calls, voice chat, media) | LiteLLM callback (`kind` set in the call's metadata, since a chat model hears the audio) | same |
| `decision` | TypeSafe notification triage, when `TYPESAFE_API_KEY` is set (`addons/phone/notifications/judges.py`, as the notification's user; the default model triage is an ordinary LiteLLM call under `notification_triage`) | `usage.record` in `judges.ask` | Jev bills **input tokens only**, $0.042 per million, output free (docs.typesafe.ai/models). Its API reports no usage, so input tokens are estimated from the request (`usage.estimate_tokens`, ~4 chars/token, ~1.5 for Hebrew); `TYPESAFE_COST_PER_MTOK` overrides the price |
| `realtime` | OpenAI realtime voice (`core/realtime.py`) | `response.done` and input-transcription events → `usage.record_realtime` | LiteLLM's table for the realtime model: text / audio / image tokens, cached input at its own rate; `whisper-1` per second |

Failed calls are recorded too (`ok = 0`, error text, no cost). A call whose model has no price is **unpriced** (`cost` NULL), never counted as free: the screen shows how many there are and leaves them out of the total.

## What a call was for

Each record has `purpose` (what: a purpose declared by core, `core/purposes.py`, or by an addon's `llm_jobs=`, e.g. `chat`, `call_analysis`, `notification_triage`, `weekly_review`; the Usage screen shows its label, `purposes.label`), `source` (what started it: `app`, a device name, `telegram`, `whatsapp`, `phone`, `background`) and `ref` (the item: `session:<id>`, `call:<id>`, `notification:<id>`, `person:<id>`, `app:<package>`).

- `llm.complete` / `complete_json` / `transcribe` take optional `purpose=`, `ref=`, `source=`; without a purpose the job name is used, so nothing is untagged. A purpose nobody declared raises `purposes.UnknownPurpose`.
- Agent runs: `purpose` chat, `ref` = the run's session, `source` = the sending device (`usage.bind(device)` at the start of each run task in `core/session_runner.py`).
- `usage.scope(source=…, ref=…, purpose=…)` attributes everything inside a block (tasks created inside copy it).
- Context travels in LiteLLM's `litellm_metadata`, which is never sent to a provider.
- **Whose call**: every record has `user_id`, the current user when the call is made (`users.current_id()`, docs/access.md "The current user": the run's user, the request's, or the user a background loop runs as). Model calls carry it in `litellm_metadata` (`usage.meta`) so the callback sees it on LiteLLM's worker; `usage.record(user_id=…)` may name it (`''` = no user). Calls with no user context are stored with `user_id = ''`.
- **A new call site**: pass `purpose=` (and `ref=` when there is an item); a new purpose is declared in the addon's manifest (`llm_jobs={"<purpose>": "fast"}` or `LlmJob("fast", "Label")`), core's own in `purposes.CORE`. An untagged usage row shows as "Other (untagged)".

No prompts or replies are stored, only metadata.

## Cost of tracking

- `usage.record()` appends to an in-memory buffer (~10 µs, never raises, never touches SQLite). `usage.run_loop()` (started in `core/server.py`) writes it every `USAGE_FLUSH_SECONDS` in one transaction on a worker thread, flushes again on shutdown, and keeps rows queued when a write fails (buffer capped at 20 000).
- `usage_events`: one row per call for `USAGE_RAW_DAYS` (60). Older days are folded into `usage_daily` (day × user × kind × purpose × source × model) and the raw rows deleted, every 6 h (`rollup_sync`; a late row for a folded day adds to it). History is kept for good at a few KB a day; 30 000 raw rows are about 2 MB.
- Every row has its local `day` (the `core.clock` zone), so a day means the same in both tables.
- Reads aggregate the two tables in one query; the whole screen reads in tens of ms.

## API

**Whose usage**: both endpoints answer for the caller's own calls by default. An admin (access level with `see_everyone`, docs/access.md) may add `user=<user id>` for one user's or `user=all` for everyone's (calls made for no user included); an unknown user is `404`. A member asking for anyone else's or `all` gets `403 {"ok": false, "error": …}`. Response shapes are unchanged (events have no user field).

`GET /api/usage?range=today|7d|30d|90d|1y|all&kind=&purpose=&source=&model=&user=` →
`{range, from, to, filters, totals, previous, by_day: [{day, cost, calls}], breakdown: {kind|purpose|source|model: [{key, label, calls, failures, cost, unpriced, input_tokens, output_tokens, cached_tokens, avg_ms}]}, raw_days}`.
`totals` has the same fields; `previous` is the same length of time before (null for `all`). Costs are USD.

`GET /api/usage/events?range=7d&sort=recent|cost&limit=50&before=<id>&kind=&purpose=&source=&model=&ref=&ok=0|1&user=` →
`{events: [{id, ts, kind, purpose, purpose_label, source, ref, provider, model, input_tokens, output_tokens, cached_tokens, reasoning_tokens, units, cost, ms, ok, error}], next, raw_days}`. Only calls still held raw; `next` is the cursor for the next page (recent order).

Both flush the write buffer first. `GET /api/usage` also carries `budget` (next section).

## Monthly budget

A server-wide ceiling on what the models cost a month, in USD (`core/budget.py`). Whoever may manage the server (`manage_server`, docs/access.md) sets it on the Usage page; it is stored in `server_settings['budget']` (no env); none set means no budget.

- **The month's spend** is everyone's, system calls included, in the server's zone: `usage.spent_since(<first of the month>)` over both tables plus what is still queued, read at most once a minute (`budget.CACHE_SECONDS`; a limit change and `GET /api/usage` read it again). **Forecast**: the spend so far carried on at the same daily rate to the end of the month.
- **States**: `ok` below 80 %; `warn` from 80 %; `paused` from 100 %, until the month ends (`resets`, the first of the next month) or the limit is raised.
- **At 80 %** admins get a Now card (`kind` `budget`, id `budget:<YYYY-MM>`, the spend, the limit and the forecast in its blocks; `core/now.py`). `POST /api/now/act {id, action: "dismiss"}` hides it until the next month (with Undo); at 100 % the `budget` self-check takes over.
- **At 100 %** background work pauses: every LLM purpose is either background or user-started (`core/purposes.py`: core's `wake_briefing` and `weekly_review`; an addon's `LlmJob(..., background=True)`: `about_learner`, `money_classify`, `people_brief`, `call_transcript`, `call_analysis`, `notification_triage`, `notification_extract`, `notification_summary`, `media_summary`). `core/llm.py` refuses a background purpose in `complete`, `complete_json`, `search` and `transcribe` with `budget.Paused` (so no caller can forget) — except inside an agent run or a `budget.user_started()` block (a person asked: Reanalyze a call, Refresh a person's brief). Chat, voice, transcription of what a person says and every other user-started purpose keep working. The background loops ask `budget.holds(purpose)` before their pass and skip it quietly: the briefing and weekly review are written without the model (their plain versions), notifications stay pending for triage and extraction, money rows keep their deterministic categories, a new call is failed with "Waiting: background work is paused…", and an `@addon.job` pass that hits `Paused` counts as done. The pause is logged once a month, and the `budget` self-check (admins) says "paused by the budget".

`GET /api/usage` → `budget`: `{limit, spent, forecast, state: "ok"|"warn"|"paused", month: "YYYY-MM", resets: "YYYY-MM-DD", can_edit: true}` for `manage_server`, `{state, can_edit: false}` for anyone else (no amounts: the spend is everyone's).

`PUT /api/usage/budget {limit: <USD> | null}` (`manage_server`; anyone else 403 `forbidden`) → the same object; `null` or `0` removes the budget, a negative or non-numeric limit is 400.

## Screen

Range chips, headline cost with the change against the previous period, token / cache / latency stats, **this month** (admins: spent, forecast and the budget with a meter marked at 80 %, its state, and Set / Change / Remove; members see it only when the budget is nearly used or paused, without amounts), cost per day (per week past 100 days), a breakdown by what for / source / model / type (click a row to filter the whole screen by it), and the individual calls (recent or most expensive) with links to the chat, call or person that caused them. Prices that are unknown are called out, not hidden.

## Not covered

Anything that does not bill per call: Home Assistant, Google Calendar, WAHA, FCM, DuckDuckGo search. The Android app has no screen for this yet (it would use the two endpoints above).

## Prices checked

LiteLLM's table was compared with the providers' published prices: Gemini 3.1 Flash-Lite ($0.25 / $1.50 per million in / out), 2.5 Flash ($0.30 / $2.50), 3.1 Pro ($2 / $12), the `-latest` flash alias ($0.75 / $3.75, Flash 3.6 / 3.8) and flash-lite alias ($0.30 / $2.50) all match Google's list. Claude Haiku 5.5 ($0.10 / $0.50, cache reads $0.01), Sonnet 5.5 ($2 / $10, $0.10) and Opus 5.5 ($4 / $20, $0.20) match Anthropic's list; Claude's thinking tokens are billed as output. Jev: $0.042 per million input tokens. Things that will move the numbers: Google raises Gemini 3.6 / 3.8 Flash to $1.50 / $7.50 on 2027-01-01, and routes `gemini-2.5-flash` to 3.5 Flash-Lite on 2026-12-01; the `-latest` aliases follow LiteLLM's table, so keep `litellm` current.
