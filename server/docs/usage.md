# Usage and cost

What every model and decision-model call costs, where it came from, and what it was for. Web screen **Usage** (rail, or Settings → Usage & Cost on a phone; deep link `/#usage`).

Code: `core/usage.py` (capture, storage, roll-up, reads), `core/usage_api.py` (routes), `static/usage.js` + `usage.css`. Tests: `python test_usage.py`, `python test_multi_user_core.py` (scoping).

## What is tracked

| `kind` | Calls | How it is captured | Price |
|---|---|---|---|
| `llm` | agent chat (every ReAct step), `llm.complete` / `complete_json` jobs | LiteLLM callback | LiteLLM's price table, from the provider's token counts (cached and reasoning tokens included) |
| `transcribe` | `llm.transcribe` (voice notes, calls, voice chat, media) | LiteLLM callback (`kind` set in the call's metadata, since a chat model hears the audio) | same |
| `decision` | TypeSafe notification triage (`integrations/phone/notifications/triage.py`, as the notification's user) | `usage.record` in `triage.ask` | Jev bills **input tokens only**, $0.042 per million, output free (docs.typesafe.ai/models). Its API reports no usage, so input tokens are estimated from the request (`usage.estimate_tokens`, ~4 chars/token, ~1.5 for Hebrew); `TYPESAFE_COST_PER_MTOK` overrides the price |
| `realtime` | OpenAI realtime voice (`core/realtime.py`) | `response.done` and input-transcription events → `usage.record_realtime` | LiteLLM's table for the realtime model: text / audio / image tokens, cached input at its own rate; `whisper-1` per second |

Failed calls are recorded too (`ok = 0`, error text, no cost). A call whose model has no price is **unpriced** (`cost` NULL), never counted as free: the screen shows how many there are and leaves them out of the total.

## What a call was for

Each record has `purpose` (what: a key of `usage.PURPOSE_LABELS`, e.g. `chat`, `call_analysis`, `notification_triage`, `weekly_review`), `source` (what started it: `app`, a device name, `telegram`, `whatsapp`, `phone`, `background`) and `ref` (the item: `session:<id>`, `call:<id>`, `notification:<id>`, `person:<id>`, `app:<package>`).

- `llm.complete` / `complete_json` / `transcribe` take optional `purpose=`, `ref=`, `source=`; without a purpose the job name is used, so nothing is untagged.
- Agent runs: `purpose` chat, `ref` = the run's session, `source` = the sending device (`usage.bind(device)` at the start of each run task in `core/session_runner.py`).
- `usage.scope(source=…, ref=…, purpose=…)` attributes everything inside a block (tasks created inside copy it).
- Context travels in LiteLLM's `litellm_metadata`, which is never sent to a provider.
- **Whose call**: every record has `user_id`, the current user when the call is made (`users.current_id()`, docs/access.md "The current user": the run's user, the request's, or the user a background loop runs as). Model calls carry it in `litellm_metadata` (`usage.meta`) so the callback sees it on LiteLLM's worker; `usage.record(user_id=…)` may name it (`''` = no user). Calls with no user context are stored with `user_id = ''`.
- **A new call site**: pass `purpose=` (and `ref=` when there is an item) and add a label to `PURPOSE_LABELS`; an untagged call shows as "Other (untagged)".

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

Both flush the write buffer first.

## Screen

Range chips, headline cost with the change against the previous period, token / cache / latency stats, cost per day (per week past 100 days), a breakdown by what for / source / model / type (click a row to filter the whole screen by it), and the individual calls (recent or most expensive) with links to the chat, call or person that caused them. Prices that are unknown are called out, not hidden.

## Not covered

Anything that does not bill per call: Home Assistant, Google Calendar, WAHA, FCM, DuckDuckGo search. The Android app has no screen for this yet (it would use the two endpoints above).

## Prices checked

LiteLLM's table was compared with the providers' published prices: Gemini 3.1 Flash-Lite ($0.25 / $1.50 per million in / out), 2.5 Flash ($0.30 / $2.50), 3.1 Pro ($2 / $12), the `-latest` flash alias ($0.75 / $3.75, Flash 3.6 / 3.8) and flash-lite alias ($0.30 / $2.50) all match Google's list. Claude Haiku 5.5 ($0.10 / $0.50, cache reads $0.01), Sonnet 5.5 ($2 / $10, $0.10) and Opus 5.5 ($4 / $20, $0.20) match Anthropic's list; Claude's thinking tokens are billed as output. Jev: $0.042 per million input tokens. Things that will move the numbers: Google raises Gemini 3.6 / 3.8 Flash to $1.50 / $7.50 on 2027-01-01, and routes `gemini-2.5-flash` to 3.5 Flash-Lite on 2026-12-01; the `-latest` aliases follow LiteLLM's table, so keep `litellm` current.
