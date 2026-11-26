# Users, access and addons

Dana serves several people. Each person is a **user**; what a user can do is
the set of **addons** they have connected plus a small **access level**.
Nothing in the code says "the owner": the person who runs the server is simply
the first user, with access level `admin`.

## User

Code: `core/users.py` (the `User` value, lookups, create / update / remove,
tokens, Telegram ids, bootstrap), `core/user_rows.py` (the rows' SQL and the
first user's values), tables from migrations `m0002_users` and
`m0016_user_profile`. Tests: `bin/test test_users`, `bin/test test_dana_cli`,
`bin/test test_prompts_users`.

| Field | Meaning |
|---|---|
| `id` | Stable id (text, 12 hex chars). |
| `name` | Display name; its first word is how prompts name them ("About Maya:"). |
| `access` | `admin` or `member` (see below). |
| `telegram_chat_id` | Telegram chat for proactive messages (NULL: none), used by `notify(user, …)` (see "Proactive messages"). |
| `context` | Their profile for prompts: a short free text (who they are, family, work, who the people they talk about are), read by every prompt that analyses something for them (calls, notifications, people briefs, the briefing, the weekly review, the notification triage state). '' = nothing known. |
| `pronoun` | `he` / `she` / `they` (default `they`): how prompts refer to them, verbs agreeing ("she does", "they do"). |
| `other_names` | Other spellings of their first name (another script), shown in brackets where a prompt reads a transcript: "Robin (רובין)". |
| `prompt_name` | What Dana's own instructions call them (the agent prompt, the shared phone screen); '' = their first name. The first admin's is `the owner`. |
| `languages` | Comma list of language codes (`he,en`), the first their default reply language: the agent's language rule and the transcription prompt are worded for them, and the first one is what their notification summaries, morning briefing, weekly review and people briefs are written in and what the apps listen in first (`core/user_locale.py`, core migration `m0004_user_locale`). '' = the server's `DEFAULT_LANGUAGES` (default `en`). |
| `currency` | ISO 4217 code (`ILS`): what money values mean: all of money (transactions, dashboards, savings, tools: `user_locale.format_money` / `money_text`, money's `home_currency`), knowledge's budgets and prices, `{currency}` in a prompt part. '' = the server's `CURRENCY` (default `USD`). Changed by the user on the web Settings → Profile card (`PUT /api/profile`) or with `bin/dana users update --languages / --currency`. |
| identities | Telegram user ids (`user_telegram`) and tokens (`user_tokens`, with a label, created / last-used) that resolve to this user: one per device, made by pairing (`devices`, docs/devices.md), plus any issued with `bin/dana users tokens issue`. |

- **First user.** With no users and no `API_TOKEN` the server starts in
  **setup mode** (`core/setup.py`, docs/devices.md): it logs a one-time setup
  link and the first admin is created in the browser that opens it (that
  browser becomes their first device). With `API_TOKEN` set (a headless
  install) or `DANA_INSECURE_DEV=1`, `users.bootstrap()` runs at startup (`main.py`, after
  `db.migrate()`) instead: when no user exists it creates one named `OWNER_NAME`
  (default `Owner`), access `admin`, Telegram ids from
  `ALLOWED_TELEGRAM_USER_IDS` (non-numeric entries skipped), proactive chat
  `TELEGRAM_CHAT_ID`, and a profile from `OWNER_CONTEXT`, `OWNER_PRONOUN`
  (default `they`), `OWNER_OTHER_NAMES`, `OWNER_LANGUAGES`, `OWNER_CURRENCY`
  (unset: the server's) and the prompt name `the owner`
  (`user_rows.first_user_profile()`; migration `m0016_user_profile` gives the
  same values to an existing first admin). Those env values are read only
  then; later changes are not applied (startup warns when
  `ALLOWED_TELEGRAM_USER_IDS` lists ids no user has): change a user with
  `bin/dana users update`. "The first admin" is the oldest user with access
  `admin`.
- **Tokens.** A device's token is made when it pairs (`devices.redeem`,
  `POST /api/pair`; docs/devices.md) and removing the device revokes it.
  `API_TOKEN` (env), when set, is always a valid token of the first admin and
  is never stored. Other tokens are made by `users.issue_token(user_id, label)`
  (returns the raw token once; `bin/dana users tokens issue`) and revoked by
  `users.revoke_token(token_id)`. Stored hashed: `token_hash` = SHA-256 of the
  token (Bearer lookups), `session_hash` = SHA-256 of the session cookie made
  from it (cookie lookups). `last_used_at` is written at most once an hour per
  token.
- **Web login** uses the cookie `dana_session` = HMAC-SHA256 of the token
  with the constant `dana-web-session` (`users.session_cookie_value`).
  `/login` accepts any valid token, or a pairing code of kind `browser` (the
  browser becomes a device of the code's user and gets its own token's
  cookie); revoking a token also ends the web logins made with it.
- **Requests** (`core/auth.py`): a Bearer token or the session cookie resolves
  to a user and the middleware sets `request["user"]` (a `core.users.User`).
  Public paths (`/login`, `/api/health`, `/api/app/version`,
  `/api/app/upload`, `/api/app/download/*`, `/api/pair`, `/api/setup`,
  `/api/setup/admin`, `/static/*`, `/sw.js`, `/manifest.json`) need nothing
  and get no user; pairing and setup prove themselves with a code.
- **Inbound webhook.** A request without credentials reaches only the
  inbound webhook path (`/webhook/waha`; `auth.INBOUND_PATHS`), with no
  `request["user"]`; its handler accepts it only when WAHA signed the raw body
  with `WAHA_WEBHOOK_SECRET` (HMAC-SHA512 in `X-Webhook-Hmac`; "Inbound") and
  finds its user from the payload. The `Host` header proves nothing: an
  in-cluster Service name is as easy to send as any other. Home Assistant
  posts nothing any more (`/webhook` is gone: "Home Assistant triggers").
  Every other path needs credentials.
- **No `API_TOKEN`.** While there are no users the server is in setup mode:
  only `/setup`, `/api/setup`, `/api/setup/admin`, `/api/health`,
  `/static/*`, `/sw.js` and `/manifest.json` answer, everything else 503
  `setup_required`. Once the first admin exists, requests need a DB token
  (a device's) or its cookie. For local development only, `DANA_INSECURE_DEV=1`
  skips both: every request is the first admin, the server listens on
  `127.0.0.1` only (`auth.bind_host()`) and the start logs a warning. Without
  either (a test app that skips `main.py`), every request is refused (401).
- **Telegram** (`core/telegram_bot.py::AuthMiddleware`): the sender's Telegram
  id resolves to a user, passed to handlers as `user` in the handler data, and
  the handler runs as that user (see "The current user"). Unknown ids are
  refused with a reply that gives their id and how an admin links it
  (`bin/dana users telegram add <user> <id>`). While no user has any Telegram id
  (`ALLOWED_TELEGRAM_USER_IDS` was empty at bootstrap, `users.telegram_unlinked()`)
  every sender is refused that way; the bot is never open.
- **Profile in prompts** (`core/user_profile.py`): `for_user(user)` gives
  every prompt its words for the user — name, first name, context, `called`
  (first name with `other_names`), pronoun forms (`obj`, `possessive`,
  `reflexive`, `agree(singular, plural)`), `referent` (`prompt_name` or the
  first name), `about()` (the "About <first name>:" block) and
  `mentions(name)` (their context names someone, used by the call labels).
  It reads only the user's own row, so nothing in a prompt comes from another
  user. The first admin's prompts are pinned byte for byte by
  `test_parity.py` and `tests/fixtures/calls/` (with the tests' made-up profile,
  `testenv.py`).
- **Call labels** (`addons/phone/calls/labels.py`): one catalog for
  everyone; `{first}` in a hint is the user's first name.
- **Where the first admin is still looked up** (`users.first_admin()`): the
  env token and local development in `core/auth.py`, the open Telegram bot,
  the server timezone (`core/clock.py`, their Home Assistant), bootstrap and
  migrations, startup logging, and `bin/dana users list` (marks them). Nothing
  else.
- **Lookups are cheap**: an in-memory copy of the three tables, rebuilt
  whenever the database changed since the last lookup (`core.db.ChangeWatch`
  reads SQLite's file change counter), so a change made through
  `core/users.py` or by another process (`bin/dana`) applies to the next
  lookup.

## The current user

Every piece of work has an explicit `User` and runs **as** that user:
`users.acting_as(user)` binds it in a context variable, and deep code reads it
with `users.current()` / `users.current_id()` (None outside any user: system
work). One mechanism, bound in four places:

- the web auth middleware, around every authenticated request (handlers also
  read it explicitly with `core.auth.request_user(request)`, which answers 401
  when there is none);
- the Telegram `AuthMiddleware`, around every handler;
- `core.runs.RunManager.start(user, …)`, around the run's task — so a run
  (message, approve / deny, select, extend, a learned phrase, retry) and every
  tool it executes (`core.addons.registry.execute(user, …)`) keep the user after the
  request ends;
- each background entry point, which takes a `User` and binds it; the
  composers' loops (`wake.wake_watch_loop`, `briefing.fallback_loop`,
  `briefing.meeting_cards_loop`, `weekly_review.schedule_loop`) go over every
  user who has the addons the work needs with `core/composers.for_each` (each
  as that user, one failing not stopping the next); the phone's loops (notification retention,
  digest, re-nudge, triage retry (every 5 minutes: the notifications the judge
  did not answer), call audio retention: `addons.phone.config.for_each_user`)
  each user with `phone`, call processing / reanalysis
  (`addons.phone.calls.processing.process_call`) the call's owner; the
  task time checker
  (`addons.tasks.triggers.time_checker_loop`) binds each user with
  `tasks` in turn, the About learner (`addons.about.learner.run_due` →
  `run_pass(user)`) each user with `about`, the people brief loop
  (`addons.people.briefs.run_briefs` → `refresh_briefs(user)`) each
  user with `people`, the Home Assistant home-map
  refreshes each their connection's user, an inbound WAHA event
  (`addons.whatsapp.events.process_event`) the user of the connection
  whose account it names.

`asyncio.create_task` copies the context, so tasks started from there inherit
the user. Readers: undo (owner of each entry), usage (user of each row),
tools.

## The conversation core

The agent, histories, pending approvals and pruning live in the core
(`core/session_runner.py` owns the agent: `agent()` / `set_agent()`;
`core/session.py`, `core/thread.py`, `core/history.py`), not in the Telegram
bot. `core/telegram_bot.py` is a channel adapter: it turns updates into runs
for the sender's user, renders results and is the sender behind `notify`.
`main.py` builds the core first, then the adapter, then the web server.

- **Sessions** (`sessions`, primary key `(user_id, session_id)`): every user has
  their own sessions and their own `main` row; ids are per user, so the same id
  can exist for two users. An id that exists only for someone else answers 404
  on every session, assistant, stream and debug endpoint
  (`session.owned_by_someone_else`). Telegram chats map to `tg_<chat id>` per
  user, so two people in one group each have their own conversation there.
- **Thread** (`thread_messages.user_id`): the wire id stays `main` and resolves
  to the caller's own thread (window, paging, retention per user).
- **Runs** are keyed `(user, session)`: two users can run in `main` at once.
  Reattaching (`GET /api/sessions/{id}/stream?run_id=`) to another user's run
  answers 404, also on `main`.
- **Pending approvals** live on the user's session row. Over HTTP only the owner
  finds them; in Telegram the buttons work only for the user who triggered
  them — anyone else gets an alert and the message is left alone
  (`session.waiting_for_someone_else`).
- **Undo** entries record their user; another user's id answers 404
  (HTTP) / "can no longer be undone" (Telegram).
- **Learned phrases** (`shortcuts.user_id`, phrase unique per user): learned
  from, matched, listed and deleted per user.
- **Usage** (`usage_events.user_id`, `usage_daily.user_id`): the current user
  at the time of the call; `''` when there is none (background work with no
  user). `/api/usage` and `/api/usage/events` answer for the caller; an admin
  may add `user=<id>` or `user=all` (no-user rows included); a member asking
  for more than their own gets 403 → `docs/usage.md`.

Migration `m0003_user_scope` gives every existing row to the first admin (and
creates the first user from env, as bootstrap would, when an 8.1.0 database has
rows but no user yet).

## Proactive messages

`core/notify.py`: `await notify(user, text, parse_mode=None)` sends to the
user's Telegram chat (`telegram_chat_id`); without a chat, without a registered
channel (tests, no bot) or when sending fails it does nothing and returns
False. The Telegram adapter registers itself (`notify.register_telegram`) when
it is built; the core never imports it. Reminders, the wake briefing, the
weekly review and the notification digest use it. Phone push is per user and
separate (`addons/phone/push.py`: `await push.send(user, type, data)` /
`push.notify(user, title, text, …)` reach only that user's devices; nothing
without the `phone` addon). A user's inbound voice-note transcripts go
to the chat of their `whatsapp` setting `voice_telegram_chat_id`
(`notify.whatsapp_voice(user, chat_id, …)`, docs/whatsapp.md).

## Access level

Covers only what addons cannot express.

| Capability | `admin` | `member` | Enforced by |
|---|---|---|---|
| `ops`: connect the `ops` addon (kubectl, Python) | yes | no | `store.connect` (`Addon(requires="ops")`) → `PermissionError` |
| `manage_users`: other users' addons over the API | yes | no | `?user=<id>` on `/api/addons*` (`core/addons_api.py`); `bin/dana` works on the database and needs shell access to the server instead |
| `see_everyone`: everyone's usage, the server's own self-checks | yes | own usage only; the self-checks of their own addons | `/api/usage` (`user=`), `core/checks.py` (`SERVER_CHECKS`: `reminders`) |
| `manage_server`: the server's log buffer | yes | no | `GET` / `DELETE /api/debug/logs` |
| `chat_profiles`: chat model profiles | all | `fast`, `balanced` | `core/models.py` (catalog, default, fallback) |

Levels are defined in one place (`core/access.py`: `LEVELS`, one
`AccessLevel` row each, `CAPABILITIES` naming the yes/no fields,
`allows(capability)` and `may_use_profile(id)`); adding a level is adding a
row there, adding a capability is a field set on every row. Nothing else
names a level: code asks the user's level for a capability.

## Enforcement

Every way a tool can be listed, started or resumed asks
`core.addons.registry` for the user it runs for; there is no
second list anywhere. A tool the user does not have — because its addon (or
an addon it `needs`) is not connected — gets exactly the answer a tool that
does not exist gets (`registry.ToolNotAvailable`: `Tool 'x' is not
registered.`), so a refusal never says whether someone else has it.

| Surface | Code | What a tool the user does not have gets |
|---|---|---|
| Chat: the tool list | `core/agent.py` `_stream_completion` → `registry.tool_schemas(user)` | not in the request to the model |
| Chat: a call the model makes anyway (made up, remembered from history, injected by untrusted content) | `core/agent.py` `_run_loop` (`_needs_user`, `_changes_something`), `_execute_tool` | never suspends (no approval card, no selection); runs as a read-only call and is refused by `registry.execute`: `tool_end{ok: false, result: "Error executing tool: Tool 'x' is not registered."}`, the model gets that text as the tool result and the run goes on to its final answer |
| Approval resume (HTTP approve / deny, Telegram buttons, the rest of a suspended batch) | `core/session_runner.py` `resume_agent_background`, then `_run_loop` again | checked again at resume: an approve whose addon was disconnected since the card closes with the same refusal, the model finishes the turn, the pending state is gone |
| Realtime voice | `core/realtime.py` `build_session_update_payload` (`registry.realtime_tool_schemas(user)`), the function-call branch; tools of an addon declared `realtime=False` (`gmail`) are left out the same way (`registry.in_realtime`) | not in the session's tools; a function call gets `{"error": "Tool 'x' is not registered.", "status": "failed"}` |
| Learned phrases | `core/shortcuts.py` `learn` / `match`, `core/session_runner.py` `run_shortcut_background` | never learned; never matched, so the message goes to the model as if no phrase matched (whose different answer then clears the phrase, as any different outcome does) |
| Chat: the system prompt's home map | `core/agent.py` `home_map_section(user)` → `registry.prompt_section(user, "homeassistant")` | no section (never another user's map) |
| Chat: the system prompt's About section | `core/agent.py` `about_section(user)` → `registry.prompt_section(user, "about")` | no section (never another user's facts) |
| Telegram `/memory` (command, buttons, typed input) | `core/telegram_bot.py` `handle_memory`, `handle_memory_callback`, `handle_message` | without `about`: "🧠 Memory isn't set up for you: the About you addon is not connected." |
| Server code calling a tool function directly with `store.require(user, name)` (accepting a calendar suggestion over HTTP or Now, its undo) | `addons/phone/calls/actions.py` `accept_suggestion` (from `/api/suggestions/{id}/accept`, Now, the weekly review) | `store.NotConnected` → 403 `{"error": "not_connected", "addon": "<addon>"}` from one middleware (`core/auth.py` `addon_middleware`); handlers with a catch-all `except` re-raise it |
| Endpoints about the whole server | `core/auth.py` `requires(capability)` | 403 `{"error": "forbidden", "needs": "<capability>"}` |

Not a tool surface (no change): the selection prompt (`request_user_selection`
is a `core` tool, which everyone has); background loops and inbound webhooks,
which call no tool by name. Chat model profiles: `/api/assistant/models`
lists only the caller's profiles and default; a message asking for one the
caller may not use gets the caller's default, the same path an unknown id
takes (`core.models.resolve_profile(id, level)`); a member's default is the
server default when they may use it, else the first profile they may use.
Tests: `bin/test test_access`.

Admin-only endpoints: `GET`/`DELETE /api/debug/logs`
(the process log, everyone's activity).
`GET /api/checks` and `POST /api/checks/{key}/ignore`: every user gets the
checks of their own addons, the server's own (`reminders`) only with
`see_everyone`, and ignores only for themselves (a check they don't see → 404).
Unchanged: `/api/debug/sessions*` (already the caller's own), `/api/usage*`
(already scoped), `/api/app/version` and `/api/app/download/*` (public by
design), `/api/app/upload` (guarded by `APP_PUBLISH_TOKEN`, not a user),
`/api/health` (public liveness). Everything else is a user's data or
addon and answers for the caller: the Now screen, its check cards,
`/api/checks`, `/api/search`, `/api/briefing/*`, `/api/review/*`,
`/api/signals` (the caller's own events and overview); `/api/phone/*` and `/api/notifications/*`, the caller's
phone and Inbox; `/api/zones` and `/api/wizard*`, the caller's own zones and
first-run wizard (every user, no addon needed); `/api/ha/*` (including `POST /api/ha/zones/import`) and the HA
camera proxies, the caller's Home Assistant (the proxies also need
`cameras`); `/api/calendar/*`, the caller's Google Calendar; `/api/frigate/*`,
the caller's Frigate; `/api/me` and `/api/addons*`, the caller's own
(another user's with `manage_users`). Server-wide: `/api/llm` (the model
provider and its key) needs `manage_server`; `/api/household/members*` and
`/api/demo*` need `manage_users` (`GET /api/household` shows a member only
themselves).

## Security rules

Rules that keep one user from reaching another user's data or the server's
network through something they control. What the server stores, what reaches
the model provider and what an installed addon is trusted with: the threat
model in [`SECURITY.md`](../../SECURITY.md). Tests: `bin/test test_security`
(each rule has its own section), plus the HTTP-reachable ones in
`tests/local_env/e2e.py` ("security").

- **Push registrations.** A push registration (an FCM token or a UnifiedPush
  endpoint) belongs to the device that sent it; there is one per address, so
  the same address from another device moves it. Endpoints are https only
  (http only with `DANA_INSECURE_DEV=1`), never redirected, and only their
  host is logged.
- **The demo view.** The `dana_demo_view` cookie makes requests act as a demo
  user only when the signed-in user has `manage_users` and the named user is
  a demo user; `/api/demo*` always acts as the signed-in user. Demo users are
  members, never admins, and are left out of `users.list_users()` (so no
  background work, push or sharing reaches them).
- **Pairing and setup.** A pairing code is 8 characters, one-time, valid for
  five minutes and stored only as a hash; it makes a device of the user who
  started it (someone else's only with `manage_users`) and nobody else.
  Wrong codes are limited per client and in all (`ratelimit.PAIRING_FAILURES`,
  429 `rate_limited` past either); a code of another kind is refused and
  stays usable; codes and tokens are never logged. The setup code exists only
  while there are no users, is checked in constant time and rate-limited the
  same way, and is spent by the first admin (`already_set_up` after). Each
  device has its own token and push registration, so removing a device ends
  its sign-in and its pushes at once (docs/devices.md).
- **Debug export** (`GET /api/debug/sessions/{id}`, the Telegram debug file;
  `core/debug_env.py`). The `environment` section is filled only for a user
  with `manage_server`; everyone else gets `{}` (same payload shape). For an
  admin a value is masked (`******`) when its name has `key`, `token`,
  `password`, `passwd`, `secret`, `auth`, `credential`, `private`, `cookie`,
  `session` or `service_account` in it, or ends in `_json`; and
  when the value holds a PEM block (`-----BEGIN`), is a URL with a user name /
  password, or parses as JSON with a secret-looking key anywhere (a service
  account's `private_key`).
- **Addons an admin changes.** `Addon(change_requires="manage_users")`:
  only a user with that capability may connect, update or disconnect the addon,
  for themselves or for another user (`?user=`); everyone else gets 403
  `{"error": "forbidden", "needs": "manage_users"}` (`store.Forbidden`).
  `bin/dana` and startup act as the server (`by=None`). Today:
  `cameras` (the Frigate address is the only thing it holds).
- **Addresses** (`core/addons/addresses.py`, `Field(type=ADDRESS)`:
  Home Assistant `host`, WAHA `url`, cameras `url`). For everyone: `http` /
  `https`, a host, no user name / password, no query, no fragment, no `//`,
  `.` or `..` path segment; stored normalised (scheme and host lower case,
  default port dropped, no trailing slash). A user without `manage_users` may
  use only an origin (scheme + host + port) that an admin's own connection of
  the same addon uses, or one listed in `MEMBER_ALLOWED_ORIGINS`
  (docs/configuration.md); anything else is 400 `invalid` on the field.
  `test` and `options` run only on a stored connection whose addresses are
  valid (else 400 `invalid`). Every request URL is the base joined with a
  fixed path (`addresses.join`: the result keeps the base's scheme, host and
  port; a path with a query, fragment, `//` or `..` is refused), ids in a path
  go through `addresses.segment`, and Home Assistant entity ids
  (`domain.object_id`), service names and camera names are checked strictly
  before any request (400).
- **Names.** `Field(type=NAME)` (WAHA `session`): letters, digits, `_`, `-`,
  `.`. A user without `manage_users` may not use a WAHA session (on the same
  WAHA address) that another user's connection already uses. This compares
  address strings, so the same WAHA reached under a second hostname passes —
  the account rule below is what holds then.
- **One connection per WhatsApp account** (`addons/whatsapp/accounts.py`,
  `store.claim_account`, migration `m0019_whatsapp_account_claims`). The
  account a connection's WAHA reports (`me.id`) belongs to the connection that
  claimed it first; the check and the write are one transaction. A connection
  whose WAHA reports an account another user's connection holds (a member's
  own WAHA lying, the admin's session under another hostname) gets no
  `account`; the reported one is kept in `addons.account_conflict`. It
  is never routed to (`route()` returns at most one connection; an event
  without `me` only to a session's single connection without a conflict), and
  its tools answer "This WhatsApp account is already connected by another
  user…" without reaching WAHA (`Addon(blocked=)`). It keeps being asked
  like any connection without an account, so it recovers by itself once its
  WAHA reports another account (or the holder disconnects). An admin sees the
  conflict: `account_conflict` in `/api/addons` for that user (present
  only then), `bin/dana addons show`, and a warning in the log
  naming both user ids (no message content).
- **Telegram chats.** `Field(type=TELEGRAM_CHAT)` (WhatsApp
  `voice_telegram_chat_id`): stored as a number. A user without
  `manage_users` may set only one of their own Telegram ids (their private
  chat with the bot); a group chat (a negative id) is set by an admin.
- **User-triggered fetches** (`core/outbound.py`). A download a user starts
  (knowledge link media) by a user without `manage_server` must be `http` /
  `https` on a host whose every address is public (no loopback, private,
  link-local, CGNAT, reserved: no pod network, cluster services, node, cloud
  metadata or home LAN); a name that does not resolve is refused. The URL is
  checked before the download; yt-dlp then resolves the name again and
  follows redirects itself, which cannot be checked from Dana, so for such a
  user it runs with site extractors only (`--use-extractors
  default,-generic`), which do not fetch arbitrary pages. The URL is always
  passed after `--` (never read as an option). For a knowledge entry what
  counts is who chose the link: `entries.url_set_by` records the user who
  last set it (saving the entry, or changing its URL — a change that sends
  the same URL back does not count; migration `m0018_entry_url_set_by`,
  existing rows: the creator), and the download is restricted whenever that
  user (unknown counts) or the one who asked for it lacks `manage_server` —
  so an admin pressing Retry on a link a member put on her entry still gets
  the restricted download. An undo of a URL change puts back who had set the
  old one. A link set by a user without `manage_server` is also checked when
  it is set (`core/outbound.check_url_form`: http(s), a host, no literal
  non-public address, no `localhost`; names are judged at download time) and
  refused with 400 `{"error": "invalid", "field": "url"}` / a tool error.
  Admins' own links: unchanged.
- **The WAHA webhook** (`/webhook/waha`) is accepted only when WAHA signed
  the raw body with `WAHA_WEBHOOK_SECRET` (`X-Webhook-Hmac`: hex HMAC-SHA512,
  compared in constant time; `X-Webhook-Hmac-Algorithm` must be `sha512`), or
  from a user with `manage_server` (testing); anyone else gets 403, a forged
  `Host` header included. Without the secret every unsigned event is refused. Media named by an event is
  fetched from the connection's own WAHA (its scheme and host; only the path
  and query come from the event), so the API key never goes elsewhere.
- **Shopping** (`docs/shopping.md`). The active list is the user's own
  choice (see "Sharing" → Shopping); a share never changes where their tools
  write. An invitation answered for a list not shared with the caller, and
  `?list=` naming one, answer 404 like an unknown list; `use` needs `edit`.
  `move_items` moves only the caller's own items to buy from their own list.
  The event stream follows only lists the caller can see and ends (`end`)
  when that stops.
- **Undo re-checks access.** An undo of a task (tool undo, Now's undo, a call
  suggestion's, a person link's) checks again that the user can still edit
  the task's list (`tasks.service.check_still_writable`); a shopping undo on a
  list its user can no longer edit is refused the same way ("You can no
  longer change that shopping list."), and one whose item was changed since
  is refused too. The undo of a task
  change (`update_task`) goes through the tasks service like a new edit
  (`tasks.service.restore_task`): write access on the list the task is in
  now and, when the change moved it, on the list it goes back to; the owner
  follows the list it goes back to (a moved task undone is its list owner's
  again, never a task of the mover's inside someone else's list). Either
  check failing refuses it ("You can no longer change this task…") and
  leaves the task as it is. Every other move follows the same rule through
  `save_task` (HTTP `PUT /api/tasks/{id}`, the weekly review's `move_group`);
  nothing else moves items between containers (entries never change project,
  shopping items change list only through the caller's own invitation `use`
  with `move_items`, from their own list, Now only changes status / triggers in
  place, created-item undos delete only while the task is unchanged —
  `group_id` included — and still writable).
- **Phone devices** are keyed `(user_id, device_id)` (migration
  `m0017_phone_devices_per_user`): registering a device id another user has
  makes the caller their own row; a row moves to another user only when the
  same FCM token is presented (the same app signed in as someone else).
- **Telegram selections** (the multi-select prompt's toggles) are kept per
  `(user, session)`; a button pressed by someone else gets the "not yours"
  alert.
- **SQL scoping** (`bin/test test_sql_scoping`). Every SQL string in
  `server/` (not migrations or tests) that names a table with a `user_id`
  column mentions `user_id`, interpolates a scope helper (`visible_sql`,
  `owned_sql`, `calls_sql`…: a `*_sql` / `*_where` function whose source
  mentions `user_id`), or carries `# scoped: <reason>` on its line or the line
  before (by id after an access check, a server-wide job, an owner join…). A
  new unscoped statement fails the test until it is scoped or says why.
- **Text written by others** (`bin/test test_untrusted_tools`;
  docs/agent-actions.md, "Text written by others"). Every tool whose result
  carries third-party text (web pages, emails, chat messages and file names,
  notifications, suggestions made from them, calendar invitations, call
  transcripts, people's briefs and names, knowledge entries from outside,
  bank text) declares `untrusted_result=True` in its decorator; the test
  fails for an official tool that talks about messages, mail, notifications,
  calls or the web and neither sets it nor is on its commented allow-list.
  The model reads such text framed by `core.text.untrusted`, which is also
  used wherever others' text enters a prompt without a tool (the shared
  screen, context chats, a forwarded Telegram message, the background
  prompts). A turn that carries it, from its start or after such a result,
  asks before every `acts_outside` tool; every tool that sends to other
  people is `confirm` and asks in every turn. The taint is per run (read
  from that run's messages and flag), so one user's run never taints
  another's. It is a speed bump for the model, not a sandbox: reversible
  writes still run, and household members' shared text is not marked.
- **ops** (kubectl and Python on the server, `addons/ops/`). Admin only and
  never turned on by itself (not a default, not given by startup): an admin
  turns it on. `run_kubectl` runs only read-only verbs (`get`, `describe`,
  `logs`, `top`; flags `-n`, `-A`, `-o json|yaml`, `-l`, `-c`, `--tail`) on the
  resources it knows (`RESOURCES` in `addons/ops/tools.py`, each with its API
  group) with the pod's service account, which needs `get` / `list` on them
  (and on `metrics.k8s.io` for `top`); secrets only by name (a secret's values,
  `-o` or `describe`, are refused before any request); logs are the last 200
  lines unless `--tail` says otherwise, and every result is cut at 10,000
  characters with a note (logs and tracebacks keep their end). `run_python`
  runs in a thread (the event loop keeps going),
  always asks first (`confirm`) and is **not a sandbox**: a child process in
  a temporary working directory with CPU / memory / file-size limits,
  Python's isolated mode and a minimal environment (`PATH`, `HOME`, `TMPDIR`,
  locale: no `CREDENTIALS_KEY`, API keys or tokens), but the server's user,
  file system and network.

## Addon

One user's own connection to one service. Never shared between users: two
people using the same Home Assistant each add their own token.

Code: `addons/` (one package per addon), the framework in
`core/addons/`, the startup import in `addons/bootstrap.py`,
table `connections` from migration `core/db/migrations/m0004_integrations.py`.
Tests: `bin/test test_addons`.

An **addon** is a package `addons/<name>/` with `addon.py`
(the declaration) and `tools.py` (its tools; other files where the service
has a natural seam: `homeassistant/client.py` + `home_map.py`,
`whatsapp/client.py`). The addon declares:

- **credentials**: fields needed to connect (empty for built-in services).
  Stored encrypted (see "Credentials").
- **settings**: per-user choices (which Home Assistant entities are "me", the
  Telegram chat voice transcripts go to, …), plain JSON, with declared defaults.
- a field's **type** (`Field(type=…)`): `TEXT` (default), `ADDRESS` (a
  service's base URL), `NAME` (an identifier such as a WAHA session),
  or `TELEGRAM_CHAT` (a chat id); `store` validates and normalises them for every
  change, with the member rules in "Security rules". A bad value raises
  `InvalidValue` (with `.field`; HTTP 400 on that field). A credential is
  write-only (`Field.secret`) unless it is an `ADDRESS`: an address is not a
  secret, so the API returns its value to the connection's own user (and an
  admin acting for them) like a setting; storage is the same.
- **who may change what**: `store.field_rule(addon, user, field, by)`
  and `store.addon_rule(addon, by)` say, before any value is typed,
  whether `by` may change a field / the addon for `user` (`can_change`), why
  not (`why_not`: "Set by an admin") and one line on what a value may be
  (`rule`). The store's own checks (`_checked_value`, `change_requires`) go
  through the same function, so what a client is told and what is refused
  cannot drift (`test_security.py` tries every field a payload marks locked).
- optional **change_requires**: a capability the user *making* the change
  needs (`cameras`: `manage_users`), else `store.Forbidden` (403). Every
  `store.connect / update / disconnect` takes `by=` (the acting user; None
  for the server itself, startup and `bin/dana`).
- optional **validate**: `Addon(validate=)`, a rule across fields and
  users called with `(user, changes, credentials, settings, by)` before a
  change is saved; it raises `InvalidValue` (WhatsApp: a session another user
  has; shopping: an active list that is not their own or edit-shared).
- optional **blocked**: `Addon(blocked=)`, a function of the user's
  connection returning why its tools cannot run now (or None);
  `registry.execute` answers `"Error: <reason>"` without calling the tool
  (WhatsApp: the account its WAHA reports is held by another user's
  connection).
- **requires**: an access-level capability needed to connect it (`ops`:
  `AccessLevel.ops`, admins only), or none.
- **tools**: the addon owns its tools. A tool receives the user's connection as
  its first argument `conn`; that argument is not part of the schema the model
  sees. Schemas come from type hints (Pydantic) and Google-style docstrings.
- optional **prompt parts**: `Addon(prompt_parts=(PromptPart(slot,
  text, rank, needs), …))`, the addon's sentences in Dana's fixed instructions
  (`core/agent.py::instructions(user)`). The template names the slots
  (`tools`, `read_examples`, `id_examples`, `prompt_ids`, `undoable`,
  `runs_at_once`, `done_examples`, `send_via`, `action_examples`,
  `routing_heading`, `routing`, `choice_examples`, `guides`); a slot is the
  joined parts of the addons the user has connected, ordered by `rank` then
  `ADDON_ORDER` (`registry.prompt_parts(user, slot)`); `needs=("calendar",)`
  keeps a part out unless that addon is connected too; `{user}` in a part is
  the user's `referent`. Adding an addon adds its parts in its own `addon.py`,
  never in `agent.py`. A user without an addon gets none of its words (no tool
  names, no rules about it); the first admin with every addon gets the old
  text byte for byte.
- optional **prompt section**: `Addon(prompt_section=)`, a function of
  the `User` returning the addon's cached, per-turn part of the system prompt
  (never blocking; "" for nothing). The agent asks
  `registry.prompt_section(user, name)` for the run's user, which answers ""
  without a user, without the addon connected or when the section fails, and
  places each addon's section itself (`homeassistant`: their home map, then
  `about`: their facts, both before the date/time line).
- optional **check**: `Addon(check=)`, an async function of the
  `Connection` returning None when the service answers with these
  credentials, else a short reason without secrets ("Home Assistant refused
  the token (HTTP 401)."). `POST /api/addons/{name}/test`. Implemented
  for `homeassistant` (`GET /api/config`), `whatsapp` (`GET /sessions`: the
  session exists and is `WORKING`), `calendar` (the calendar list) and
  `cameras` (Frigate `GET /api/version`).
- optional **options**: `Addon(options=)`, an async function of the
  `Connection` returning `{setting: [{value, label}, …]}`, candidates for its
  settings read from the service. `GET /api/addons/{name}/options`.
  `homeassistant` (`addons/homeassistant/options.py`): `phone_battery_sensor`
  (sensors of device class `battery`), `tv_entity` (media players).
- **inbound handlers** (webhooks, phone uploads) and **background jobs** (run
  once per user who has the addon connected, as that user) — not part of the
  framework: each addon runs its own per user (`tasks` time checker, `about`
  learner, `people` briefs, `homeassistant` home maps,
  `whatsapp` inbound events routed by account, `phone` notification
  retention / digest / re-nudge / triage resume, call processing / resume /
  audio retention).

```python
# addons/whatsapp/addon.py
whatsapp = Addon("whatsapp", credentials=(Field("url", ...), ...), settings=(...))

# addons/whatsapp/tools.py
@whatsapp.tool(confirm=True)
async def send_whatsapp_message(conn: Connection, chat_id: str, text: str) -> str: ...
```

`@addon.tool(...)` takes `is_write`, `confirm` (a bool or a function of the
call's arguments), `acts_outside` (docs/agent-actions.md),
`needs=("otheraddon", …)` and `available=` (a function of the `User`: the tool
is offered and runs only while it returns True — checked at listing, in
`has_tool` and at execution, a refusal like any other; a rule that raises
counts as False; `share_item` uses `sharing.tool_available`). `Addon(…,
on_connect=)` runs a function of the `User` right after `store.connect` (a
failure is logged, the connection stays): `tasks` seeds the default lists,
`shopping` makes the user's own list, `whatsapp` learns its account;
`Addon(…, on_credentials_change=)` runs one after `store.update` changed
the credentials (`whatsapp`: forget the account, learn it again);
`Addon(…, on_change=)` runs one after every connect, update or
disconnect. With these, a change through the API
needs no restart: the connection cache, the tool list and the prompt change
at once, the WhatsApp account is
learned, default task lists and the shopping list are made
(`test_addons_api.py`). A change made by `bin/dana` (another
process) reaches the server on its next lookup (`core.db.ChangeWatch`); the CLI runs `on_connect`
itself. Importing the `addons` package registers
every addon (explicit imports; `ADDON_ORDER` in `addons/__init__.py` fixes
the order). Adding an addon: the package, one import line and its name in
`ADDON_ORDER`.

API (`core/addons/`):

| | |
|---|---|
| `store.connect(user, name, credentials=, settings=, account=)` → `Connection` | refuses an addon the user's level does not allow (`PermissionError`), a second one (`ValueError`), unknown fields, missing required credentials |
| `store.update(user, name, credentials=, settings=, account=)` → `Connection` | merges; a setting set to None goes back to its default |
| `store.disconnect(user, name)` → bool | |
| `store.get(user, name)` → `Connection \| None`, `store.require(user, name)` → `Connection` (`NotConnected`) | what a direct caller of a tool function passes as `conn` |
| `store.connected_addons(user)`, `store.is_connected(user, name)`, `store.list_connections(user)`, `store.users_with(name)` | |
| `registry.tool_schemas(user)`, `registry.realtime_tool_schemas(user)`, `registry.tools_for(user)`, `registry.has_tool(user, name)` | |
| `await registry.execute(user, name, args)` | the only way to run a tool by name |
| `registry.needs_confirmation(name, args, untrusted=)`, `registry.is_write_tool(name)`, `registry.acts_outside(name)`, `registry.returns_untrusted(name)`, `registry.in_realtime(name)` | properties of the tool, the same for everyone (`returns_untrusted`: its result is text written by other people; `in_realtime`: its addon is offered in realtime voice) |
| `registry.prompt_section(user, name)` → str | the addon's system-prompt section for the user ("" without the addon connected, without a section, or on failure) |
| `registry.prompt_parts(user, slot)` → list of str | the parts of the user's connected addons for one slot of Dana's instructions |

`Connection` = `user`, `addon`, `credentials` (decrypted), `settings` (defaults
filled in), `account`. Lookups read an in-memory copy of the table
(decrypted values included), read again whenever the database changed since
the last lookup (`core.db.ChangeWatch`), so a change made through `store` or
by another process (`bin/dana addons connect`) applies to the next lookup.

Rules:

- A user's tool list is the union of the tools of their connected
  addons, minus tools whose `needs` are not all connected, in a stable
  order: `ADDON_ORDER`, then the order the tools are defined in (so the tool
  block stays cacheable).
- The same check runs again at execution: a tool that belongs to an
  addon the user does not have is refused, whoever asks for it (the
  model, the realtime voice relay, a learned phrase, an approval being
  resumed, a Telegram command). The refusal is the error a tool that does not
  exist gets (`Tool 'x' is not registered.`), so it does not reveal whether
  someone else has it. There is no way to run a tool without a user. Where
  each surface checks: "Enforcement".
- A tool that needs a second addon declares it
  (`@people.tool(needs=("tasks",))`: `link_task_to_person`;
  `accept_call_suggestion` needs `tasks`); it is offered and runs only when the
  user has every addon it needs. It gets the connection of its own addon.
- One connection per addon per user (primary key `(user_id, addon)`).
- Every user has `core`, with no row; it cannot be connected or disconnected.
  A new user has only `essentials` until addons are connected for them.
- Tool names, descriptions and argument schemas are the same for everyone;
  the first admin's list is pinned by `test_parity.py`.

### Addons

| Addon | Service | Credentials | Settings (default) | Notes |
|---|---|---|---|---|
| `core` | time, selection, web search | none | none | every user has it |
| `tasks` | task lists, tasks, triggers | none | none | built in |
| `shopping` | shopping lists | none | `active_list` (none: their own list): the list their tools and apps work on | built in; one list per user (made on connect), the *active list*: see "Sharing" → Shopping and `docs/shopping.md`; the name → aisle table is shared reference data (names only) |
| `knowledge` | projects and entries | none | none | built in; projects per user with `project` sharing, entries follow their project (docs/knowledge.md) |
| `about` | About-you facts and learner | none | none | built in, no sharing; facts, "do not re-add", the size cap, the prompt section and the learner per user (docs/about.md) |
| `people` | people, briefs | none | none | built in, no sharing; people, their numbers (unique per user), briefs and the call card per user; calls / notifications linked only from the user's own (both by their `user_id`, `activity.calls_sql` / `notifications_sql`) (docs/people.md) |
| `phone` | the Android phone: push, notifications Inbox, calls and suggestions, app icons (files per user under `app-icons/<user id>/`) | none (each device pairs and gets its own token; its push registration is keyed by that device) | none | built in. Devices, push events (one `seq` numbering for the server, each event fetched only by its user), notifications (FTS, rules, summaries, declines, action requests, digest state), calls (uploads, recordings, transcripts, labels, FTS search, processing / reanalysis / backfill as the owner), call and notification suggestions (only their owner's: listed, accepted, dismissed, edited, undone, popped up), context chats on them, the self-check marks `push` / `notification_sync` / `triage`, the calls check and the loops per user; `/api/phone/*`, `/api/notifications/*`, `/api/calls/*` and `/api/suggestions/*` for the caller, 403 `not_connected` without the addon (docs/notifications.md, docs/calls.md) |
| `homeassistant` | Home Assistant | `host` (an address), `token` | `phone_battery_sensor` (the dashboards' Batteries widget default), `tv_entity` (the Android TV media player `control_android_tv` uses when not given one) + `tv_channel_command` (an ADB command with `{channel}` for its `watch_channel`, e.g. a TV provider app's deep link; unset: no channel changes); no other defaults | no limits on Dana's side: whatever the token can do. Everything per connection: REST / WebSocket clients, zones, states cache, home map. Tools, `/api/ha/*` (zone import included), the HA camera proxies (which also need `cameras`): the caller's connection, 403 `not_connected` without it |
| `whatsapp` | WAHA | `url` (an address), `api_key` (optional), `session` (a name, not one another user's connection uses) | `voice_telegram_chat_id` (none): the user's voice-transcript chat (their own Telegram id unless an admin sets it) | account: the session's WhatsApp id, learned from WAHA (routes inbound events, see "Inbound"). Tools, name maps, "me", dedupe, saved contacts (`whatsapp_contacts`) and the images its tools save per connection / user (docs/whatsapp.md). No HTTP endpoint of its own besides the inbound webhook |
| `calendar` | Google Calendar | `refresh_token` (from the Google sign-in, "Google account") | `calendar_id` (`primary`): where new events go, `selected_calendars` (none): ids of the calendars shown and searched (none chosen: `calendar_id`) | OAuth client id/secret are server-wide. The tools take a calendar by name or ID (default: `calendar_id` for writes, the selected ones for `search_calendar`), times as `YYYY-MM-DDTHH:MM` (the user's zone) or `YYYY-MM-DD` (all day; an end names the last day); `search_calendar` defaults to today + 30 days (with a query: a year back and ahead) and cuts descriptions; `update_event` with only a new start keeps the event's length. Tools, `/api/calendar/*`, undo of events, accepting an event suggestion and the composers' calendar reads (Now, briefing, meeting cards, weekly review, the `calendar` self-check) use the acting user's connection; credentials (access token) and today's events cached per connection; 403 `not_connected` without it. No sharing on Dana's side: calendars Google shares with a user show through their own account (docs/now.md) |
| `contacts` | Google Contacts | `refresh_token` (from the Google sign-in) | none | a copy of the user's contacts, synced in the background (`CONTACTS_SYNC_MINUTES`) and deleted on disconnect; search / read tools, `add_contact` / `update_contact` ask first; fills the user's people and names unknown callers (docs/contacts.md) |
| `gmail` | Gmail | `refresh_token` (from the Google sign-in) | none | account: the mailbox address. Chat tools only (`Addon(realtime=False)`: not offered or run in realtime voice, which has no Approve step); sending asks first, other changes have Undo; reading tools are `untrusted_result`; nothing is stored (docs/gmail.md) |
| `telegram_account` | the user's own Telegram account (Telethon) | `session` (from the QR sign-in, `/telegram/connect`) | none | account: the Telegram name and @username. The app (`TELEGRAM_API_ID` / `TELEGRAM_API_HASH`) is server-wide. Read-only tools, `untrusted_result`, chat only (`realtime=False`); nothing is marked read; one client per connection (docs/telegram-account.md) |
| `cameras` | Frigate | none | `url` (an address; none) | changed only by a user with `manage_users` (for anyone); no tools, only the camera endpoints (`/api/frigate/*`, `/api/camera/{entity}/proxy`), each for the caller's own Frigate, 403 `not_connected` without the addon; the two that go through Home Assistant also need `homeassistant`; caches per connection (docs/dashboard.md) |
| `money` | transactions, dashboards, savings and tools in the user's currency; statement files imported (CSV, OFX, QIF) or bank / card logins brought by a provider addon (e.g. `money_il` from the catalog, with its worker) | none of its own: each bank / card login is a row of `money_sources`, its credentials a Fernet token under the same `CREDENTIALS_KEY`, decrypted only in `addons/money/sync.py` to start the provider's sync, never returned by an API or tool, never logged | `MONEY_SYNC_TIMES` is server-wide | built in for nobody: connected per user; sources, accounts, transactions, rules, syncs and code requests per user (another user's source / request id answers 404); disconnecting deletes all of them and the user's browser profiles. SMS sign-in codes are never stored or logged: the phone posts them to `/api/money/code-requests/{id}`, and a Telegram message of only 4–8 digits is taken as the code (before the agent, history or logs) only while that user has a pending request (docs/money.md) |
| `ops` | kubectl, Python | none | none | `admin` only; never turned on by itself; `run_python` asks first and is not a sandbox ("Security rules") |

Server-wide configuration stays in env: model keys and model names, the
Telegram bot token, the FCM sender key, the Google OAuth client, TypeSafe,
realtime voice, the Telegram app of `telegram_account`, timezone, and every tuning value. Only "whose account, which
entities" lives in an addon.

Each addon reads its configuration only from the connection (credentials and
settings); nothing is read from env for an addon (`docs/configuration.md` →
The minimal first run).

### Credentials

Stored in the `connections` table only as a Fernet token of their JSON
(`core/addons/crypto.py`); settings are plain JSON. The key is
`CREDENTIALS_KEY` when set (kept in your secret store), else the key file
`<data dir>/credentials.key`. At startup (`addons.bootstrap.bootstrap()`,
`main.py`, after `users.bootstrap()`):

- **No key yet** (neither set) and nothing encrypted: the key file is
  generated (mode 0600, created exclusively) and the start logs where it is
  and to back it up with the database.
- **A key** that is malformed, or not the one the stored credentials were
  encrypted with, or **no key** while credentials are stored: the server
  refuses to start and the message says what to do (including how to
  generate a key). The env value wins over the file.

Then, once (`system_marks['addons:first_admin_defaults']`), the first admin
gets every `default_on` addon their level allows (`tasks`, `shopping`,
`knowledge`, `about`, `people`, `phone`), so one they turn off later is not
turned on again. Nothing else is connected at startup and nothing is read
from env: addons with an account are connected on the Addons page, with
`bin/dana addons connect` or the API. `bin/dana users add` gives a new
user the same defaults. Core migration `m0003_drop_env_imports` removed the
old one-time env import's tables (a database that had imported counts as
having had its defaults).

### Google account

One Google sign-in connects every addon that uses the user's Google account
(`addons/google_account/`: `oauth.py`, `api.py`; tests: `bin/test
test_google_account`). It is not an addon: each Google addon keeps its own
row with the credential `refresh_token`, declares the scopes its tools need
(`oauth.register(name, scopes)`, next to its `Addon(...)`) and
`connect_url=oauth.CONNECT_PATH`, and builds its Google client from
`oauth.credentials(conn)` (cached per user and addon; a new token starts
over). The OAuth client (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`) is the
server's.

- **Sign-in**: `GET /api/google/connect` sends the caller to Google's
  consent page for the scopes of every Google addon their access level allows
  plus `openid email`, with `access_type=offline` and `prompt=consent` (a
  refresh token every time) and a one-time `state` kept in memory for 10
  minutes, bound to that user. 503 `google_not_configured` without the
  server's OAuth client.
- **Coming back**: `GET /api/google/callback` needs the same signed-in user
  and their unused `state` (else `expired`), exchanges the code and stores
  the refresh token and the account's email (`addons.account`) on each
  Google addon **whose scopes were all granted**: an addon not connected is
  connected, a connected one gets the new token and keeps its settings, a
  addon whose scopes were not granted (or that the user's level cannot have)
  is left as it is. Then it redirects to `/?google=<result>#addons`
  (`connected` · `denied` · `expired` · `failed`), which the page shows.
- **Redirect address**: `PUBLIC_URL` + `/api/google/callback`, else the
  address the request came to (`X-Forwarded-Proto` for the scheme). It has to
  be one of the OAuth client's authorised redirect URIs in Google Cloud.
- **Disconnecting** a Google addon removes only that addon's row; the others
  keep working. The token can still be typed by hand (`refresh_token` on the
  addon), as before.
- `GET /api/google/status` → `{configured, redirect_uri, addons: [{name,
  connected, account}]}` for the caller.
- The sign-in is the caller's own: `/api/addons` carries `connect_url`
  only for the caller's own addons, never with `?user=`.

### Home Assistant

Home Assistant is a smart-home control only: it is no source of any signal
(wake, screen, car, location and sleep come from Dana's phone app,
`docs/signals.md`). Its state changes can be watched (`docs/watches.md`).

**State changes** (`addons/homeassistant/states.py`, the event in
`event_types.py`). The addon's one service keeps a WebSocket per user
connection (`get_states`, then `subscribe_events` for `state_changed`),
started for each user who has the addon active with a token, looked at again
every 15 s and at once after a connect / update / disconnect (`on_change`):
a disconnected user's subscription stops, a new host or token starts a new
one, a failure connects again after 1 s doubling to 5 min (the longest after
a refused token, which also marks the connection for reconnecting). It emits
`homeassistant.state.changed` (`entity_id, name, domain, old_state,
new_state, unit, device_class`, states as text) only when the state itself
changed, never to or from `unavailable` / `unknown` (`old_state` is the last
good state Dana reported, so an entity that comes back unchanged emits
nothing), and a numeric state at most once a minute per entity unless it
moved to another tens (52 → 49); a held value is emitted when its minute is
up, from the last reported state, so no threshold crossing is lost. The
event's known values (`EventType.values`) are the user's entity ids as last
read (`"id (name, device class)"`: the phone battery sensor setting first,
labelled "the user's phone battery", then the home map's and the dashboards'
Home Assistant widgets' entities, then by domain; none before the first
read); its history is the last 5,000 events Dana emitted, in memory since
the server started.

**Tools** (`addons/homeassistant/tools.py`). `get_ha_exposed_entities(search?)`:
the voice-exposed entities with live state, only those whose id, name, alias
or domain contains `search` when given, cut at 10,000 characters with a note.
`call_ha_service` checks the entity id before any request and says the
entity's new state when Home Assistant reports it. `control_android_tv`
checks its action and arguments first (nothing is sent for a wrong one),
asks Home Assistant for the TV's own entities one at a time (never every
state), turns the TV on and waits for ADB only when it is off, and puts a
YouTube / Netflix search into the ADB command as one quoted shell word. The
WebSocket registry read gives up after 20 s (`client.REGISTRY_TIMEOUT`);
every REST call has its own timeout.

**Timezone.** One for the server: `core/clock.py` resolves `TIMEZONE` → the
first-run wizard's server setting → `TZ` → UTC, once at startup.

### Inbound

- **WAHA** posts to `/webhook/waha` (accepted only when signed with
  `WAHA_WEBHOOK_SECRET`, or from a user with `manage_server`; everyone
  else 403, see "Security rules"). The event is routed to the user
  whose WhatsApp addon has the account the event names (`me.id`) and
  processed as that user (`addons/whatsapp/accounts.py`, `events.py`).
  The account is learned from WAHA (the session's `me` in `GET /sessions`):
  at connect / a credentials change, by a loop for connections without one
  (startup, then every minute; WAHA down only delays it) and lazily when an
  event matches no account (refresh, then retry once). An account belongs to
  at most one connection (the first to claim it; see "Security rules"), so
  an event goes to one user at most. Events for an unknown account are
  logged without content and dropped; an event without `me` goes to the one
  connection without an account conflict using its `session`, else it is
  dropped → docs/whatsapp.md.
- **Phone** uploads, notification batches, device registration and event
  fetches carry the user's API token. A device row is the caller's own
  (`(user_id, device_id)`); registering never moves another user's row
  unless the stored FCM token is presented. Push goes only to that user's devices
  (`addons/phone/push.py`); a notification batch is stored, triaged and
  synced back as that user.

## Sharing

Sharing is not an addon of its own. It lives inside the official addons, on
their items: a task list, a shopping list, a knowledge project. About-you
facts and people are not shareable. Built: task lists, shopping lists, projects.

- Every item has an owner (the user who created it). The owner can give
  another user `read` or `edit` on it; only the owner can share, unshare,
  rename or delete the item.
- One helper backs all three services (`core/addons/sharing.py`, table
  `item_access`: item type, item id, user, level, granted by, granted at; from
  migration `m0006_item_access.py`), with one question the services ask:
  "which items of this type can this user see / edit?" Lists and lookups
  return the user's own items plus the ones shared with them; a write to a
  `read` item is refused (403); an item the user cannot see answers as if it
  did not exist (404, "not found").
- The other user needs the same addon connected to see a shared item (a
  project shows up through their own `knowledge` addon). A share made
  before that is kept and shows once they connect it.
- What is inside a shared item follows the item: tasks in a shared list,
  items on a shared shopping list, entries in a shared project. A task added
  to a shared list belongs to the list's owner and records who added it;
  deleting a shared list moves its tasks to the owner's General list and ends
  its shares; unsharing hides the list and its tasks again. Moving a task to
  another list (tool, HTTP, weekly review, and the undo of a move) needs
  `edit` on both lists and gives the task to the target list's owner.
- **Names.** When a name is ambiguous between an own item and a shared one,
  the user's own item wins (lookups, `add_task(group=…)`, `share_item`, which
  only finds the caller's own items).
- **Shopping** (`docs/shopping.md`). Each user with `shopping` has their own
  list (`addons/shopping/lists.py`, table `shopping_lists` from
  migration `m0007_shopping_lists.py`; made by the addon's `on_connect` and at
  every start; the first admin's is the list that existed before, item ids
  and order kept). The shopping tools, `/api/shopping*` and search work on
  the user's *active* list (`lists.active_list(user)`), which is their own
  choice: the `shopping` setting `active_list` (unset: their own list). It
  may name only their own list or a list shared with them with `edit`; a
  share never changes it by itself (the recipient gets an invitation: a
  banner and a phone notification), and when the chosen list's share is
  removed or downgraded to `read` they are back on their own list (the
  setting is cleared). Choosing: the invitation's `use`, `PUT
  /api/shopping/active {list_id}` (→ that list's screen + undo; 404 for a
  list they cannot see, 403 `read_only` for a `read` one), the chat tool
  `choose_shopping_list(list_name)` (offered only when the server has more
  than one user; without a name it lists the lists; undoable), an admin with
  `PUT /api/addons/shopping?user=` or `bin/dana addons
  update USER shopping active_list=<id>`. `GET /api/shopping/lists`
  (`{active, lists: [{id, name, owner, access, active}]}`, also how a client
  finds its list id for `/api/sharing`) shows which is active; any visible
  list, `read` ones included, can be read with `?list=<id>`. This is how a
  household keeps one list: the owner shares it with `edit`, the others use
  it. The list payload carries `access` and `people`; tool output adds
  "(shared by …)". An item id that is not on the list answers "not found" /
  404. `share_item` takes `item_type: 'shopping_list'` (any `item` names the
  caller's one list).
  - **Undo.** Every change registers its own undo (`core/undo.py`), refused
    once its user can no longer edit that list or when an item it restores
    was changed since. There is no "undo last" and no operations log.
- **Projects** (`addons/knowledge/projects.py`, `entries.py`; owner
  columns from migration `m0008_knowledge_per_user.py`, the first admin's
  existing project and entries kept with their ids and FTS index). A project
  belongs to its creator; its entries belong to the project's owner and
  record who created them (an entry without a project belongs to its
  creator). Own and shared projects show in every listing, tool, search (the
  `search_knowledge` FTS, the search box) and endpoint; `edit` may add,
  change and delete entries, `read` refuses with "This project is shared with
  you read-only, so nothing in it can be changed." (HTTP 403 `read_only`);
  only the owner changes (renames, archives), deletes or shares the project
  (HTTP 403 `owner_only`). A stored media file (`GET /media/{name}`) is served
  only to someone who can see an entry with it. Tasks that carry a project
  stay governed by their own list (a project never grants access to a task,
  nor a task to a project). Link processing runs as the entry's owner.
  `share_item` takes `item_type: 'project'` (title or id; a title clash
  resolves to the caller's own project). Payloads add `shared_by` / `access`
  to shared projects and their entries → docs/knowledge.md.
- **Reminders.** A task's triggers fire for the user who created the task,
  also on a shared list, while they can still see it; the reminder goes to them.
- **How to share.** One tool, `share_item(item_type, item, user, level)` in
  the `essentials` addon (`level`: `read`, `edit` or `none` to stop sharing;
  `confirm=True`, asks before it runs). It is offered (and runs) only when the
  server has more than one user and the caller has an addon with a shareable
  item type connected, so a single-user server's tool list does not change.
  An unknown or ambiguous user name returns an error listing the other users'
  names; a user is matched by full name, or a unique first name / start of a
  name. API: `GET` / `PUT /api/sharing/{item_type}/{id}` (owner only; `PUT
  {user_id, level}`) → `{item_type, id, title, owner: {id, name},
  shared_with: [{user_id, name, level}], users: [{id, name}]}`; unknown type
  or an item the caller cannot see 404, a shared (not own) item 403
  `owner_only`, the addon not connected 403 `not_connected`, a bad level / user
  / sharing with yourself 400. Shared items carry `shared_by` (the owner's
  name) / `access` (`read` | `edit`) in API payloads and a "shared by …" note
  in tool output only when they are shared, so payloads for unshared items are
  unchanged.

### The sharing helper (for each service)

```python
from core.addons import sharing

sharing.register(sharing.ItemType(
    name="task_list", addon="tasks", label="task list",
    owner_of=groups.owner_of,      # (item_id) -> owner user id | None (None: no such item)
    find_owned=groups.find_owned,  # (user, name or id) -> id of an item that user owns | None
    title_of=groups.title_of,      # (item_id) -> display name | None
    on_change=None,                # optional (item_id, user_id, new level | None) after a change
))
```

Register at import of the addon's module that owns the items (the
`share_item` tool's item types and the API come from the registry). Then:

| | |
|---|---|
| `sharing.access(user, type, id)` → `"owner" \| "edit" \| "read" \| None` | None without the addon connected or with no access |
| `sharing.can_see(user, type, id)`, `sharing.can_edit(user, type, id)` → bool | |
| `sharing.shared_with(user, type)` → `[Share]` (newest first), `sharing.shared_ids(user, type)` → `{id: level}` | items shared with the user (empty without the addon) |
| `sharing.shares(type, id)` → `[Share]` (oldest first) | who an item is shared with |
| `sharing.set_level(owner, type, id, user_id, level)` → previous level | `level` `read` / `edit` / `none`; raises `KeyError` (type), `LookupError` (owner cannot see it), `sharing.NotOwner` (a `PermissionError`), `ValueError` (bad level, unknown user, yourself) |
| `sharing.forget(type, id)` | the item is gone: drop its shares |
| `sharing.tool_available(user)`, `sharing.item_types()`, `sharing.item_type(name)`, `sharing.shareable_addons()` | |

`Share` = `item_type, item_id, user_id, level, granted_by, granted_at`.
Services do the rest: their lookups use `shared_ids` to add shared items,
their writes check `access`, and payloads add `shared_by` / `access` only for
shared items.

## What is per user

Sessions and the `main` thread (the wire id stays `main`), runs, pending
approvals, undo entries, learned phrases, usage rows, About-you facts and the
learner, people, tasks, shopping lists, projects, calls, notifications and
rules, phone devices and events, zones, the first-run wizard's progress,
briefings, weekly reviews, Now, live activities (`docs/live.md`: rows keyed by user, add-on and key; hooks run only for add-ons active for that user), tokens of no device (listed and revoked on the Devices page by their owner or a `manage_users` admin), self-checks of that user's addons. Another user's ids answer 404.

In detail: sessions and `main`, runs, pending approvals, undo entries,
learned phrases, usage rows and the Now screen's approval cards (see "The
conversation core"); task lists, tasks, task links (through their task),
trigger marks, the time checker and reminders; shopping lists, their items,
history, aisle choices and invitation answers; projects, entries, their FTS search,
media files and link processing; About-you facts, their prompt section and
the learner; people, their numbers, briefs, the call card and the brief loop
(calls and notifications linked into them only from the user's own); their
phone (devices, push events and the push / notification-sync / triage
self-checks; the notifications Inbox: notifications, FTS search, rules and
declines, AI summaries, action requests, escalation rate limit, notification
suggestions, the digest and its state, retention, re-nudges, the agent tools,
Now's needs-you cards and notification counts, search results; calls:
uploads, recordings, transcripts, labels, FTS search, processing and
reanalysis as the owner, call suggestions, pop-ups, context chats, audio
retention, startup resume, the backfill, the calls self-check); their Home
Assistant (tools, endpoints, zones, home map); their WhatsApp (tools, caches, saved contacts, inbound
voice notes and their Telegram chat); their Google Calendar (tools,
`/api/calendar/*`, the selected calendars, event undo, today's events in Now,
the briefing, meeting cards, the weekly review, the `calendar` self-check);
their Frigate (every camera endpoint and its caches). Saved images
(`/api/images/*`) belong to the user they were saved for. The composers
(`core/composers.py`): the Now screen (items per addon, hidden cards
`now_hidden`, act / undo, day slots, the lede), the wake flow (`wake_log` per
user and date, the raw signal mark, the check in flight; needs `phone` and
`homeassistant`), the briefing (`briefings` per user and date; for `phone`,
or a Telegram chat and an addon with something to say), meeting cards
(`calendar` + `phone`), the weekly review (`weekly_reviews.user_id`;
`tasks`), search (own and shared items), the self-checks (per user, ignore
per user), context chats and the dashboards (a device token reads only its own; a user who may manage the device reads and edits it, a managing admin gets the layout with each widget's data withheld as `private`; any other id answers 404) — each from
the user's connected addons only (a missing addon adds nothing; an action
needing it is 403 `not_connected`; the polled `/api/briefing/today` and
`/api/review/latest` answer null). Every prompt is written for its user from
their profile (`core/user_profile.for_user`, see "User"); Dana's instructions
hold only the parts of the addons they have (see "Addon").

Proactive messages go through one function, `notify(user, …)`: their Telegram
chat if they have one today; their phone through `addons/phone/push.py`
(`send(user, …)` / `notify(user, …)`), which reaches only their devices.

## Managing users

Three ways, the same rules: `bin/dana` on the server (everything, any
user, works on the database directly), the HTTP API (a user's own
addons; an admin's for anyone) and the web UI's **Addons** page
built on that API (Settings → Addons). Members, their levels and their
invites (a phone pairing QR and a browser code) are on Settings → Household
(`docs/household.md`); tokens, Telegram ids and sharing have no screen: the
CLI (and the API for sharing) cover them.

### bin/dana

Plain argparse (`cli/`), `--help` on every command; run where the server's env is
(`DATABASE_PATH`, `CREDENTIALS_KEY`), in production in the pod:
`kubectl exec -n <namespace> deploy/<deployment> -- bin/dana users list`.
It refuses to run on a database with pending migrations (start the server
once). The running server sees a change on its next lookup. Tests:
`bin/test test_dana_cli`. The commands that run the server's addons (`new`,
`install`, `remove`, `disable`, `enable`) are in `../docs/addons.md`.

```
users list                              users: access, Telegram ids, tokens, addons
users add NAME [--access] [--context] [--pronoun] [--languages] [--currency] [--telegram-chat]
users update USER [--name] [--access] [--context] [--pronoun] [--languages] [--currency] [--other-names] [--prompt-name] [--telegram-chat]
users remove USER [--delete-data]       without the flag: says what would be deleted, removes nothing
users tokens issue USER [--label]       prints the token once
users tokens list [USER]
users tokens revoke TOKEN_ID
users telegram add USER TELEGRAM_ID
users telegram move TELEGRAM_ID USER
users telegram remove TELEGRAM_ID [--allow-no-ids]
users sharing [USER]                    who has access to what
addons list                             every addon: source, version, state, fields; the ones that failed
addons show USER                        a user's connections (credential values never shown)
addons connect USER ADDON [--credentials-file PATH | --credentials-stdin] [SETTING=VALUE ...]
addons update USER ADDON [--credentials-file PATH | --credentials-stdin] [SETTING=VALUE ...]
addons disconnect USER ADDON
```

- `USER` is a user id or name (full name, or a unique start of one, any case).
- Credentials only from a file or stdin, as `name=value` lines, never on the
  command line (they would land in shell history and `ps`). `SETTING=VALUE`
  is read as JSON when it parses (numbers, `null`, lists), else as text;
  `SETTING=` puts the setting back to its default.
- Stored secrets are never printed: tokens only once, at `tokens issue`;
  credentials only as set / not set.
- The access level is respected (`ops` only for an admin), as in
  `store.connect`. The last admin cannot be removed or made a member.
- `telegram remove` refuses to remove the last Telegram id of all (the bot
  would then refuse everyone) unless `--allow-no-ids`.
- **Removing a user** (`remove USER --delete-data`): their row and everything
  they own go — sessions, thread, learned phrases, tokens, Telegram ids,
  addons (credentials included), task lists and tasks, shopping list,
  projects and entries, About facts, people, phone devices / notifications /
  calls (recording files and saved images deleted from disk), briefings,
  reviews, Now state, and the shares they gave or were given. Kept: usage
  rows (the server's cost history; their `user_id` stays as it was) and what they added to other users' shared lists or
  projects (it belongs to the list's / project's owner; `created_by` keeps
  their old id). Other users' tasks pointing at a removed project lose the
  link. Without `--delete-data` it lists the counts and removes nothing.

### The Addons page

`static/addons.js` + `static/addons.css`, the panel
`#panel-addons` in `static/index.html`, opened from the Settings tile
(or `/#addons`); tests: `bin/test test_web_addons` (its pure part
against payloads captured from the API in `tests/fixtures/addons_web.json`,
regenerated with `WRITE_WEB_FIXTURE=1 bin/test test_addons_api`, which
also fails when the API's shape drifts from the file) and the "Addons
page" section of `tests/local_env/e2e.py`.

- **Header**: who is signed in (`/api/me`: name, access level). A user with
  `manage_users` gets a picker of every user (`/api/users`); picking someone
  else shows a banner ("You're editing Maya Levi's addons") and every
  request carries `?user=`.
- **List**: services you connect with an account or address (an addon with
  credentials or an address setting: Home Assistant, WhatsApp, Google
  Calendar, Cameras) first, official addons after. Each row: name,
  description, state from the server only (connected / not connected / needs
  attention: an `account_conflict` or a stored address that is no longer
  valid, `problem`), the account (the WhatsApp number) or the service's host,
  and "Number used by another user" on a conflict.
- **Detail** (inline expand, works at phone width): a secret credential is an
  empty password input with *set* / *not set* beside it (empty keeps the
  stored value; an optional one has "Remove the saved …"); an address is a
  normal input holding the current value; settings are text inputs, Telegram
  chats number inputs with the field's `rule`, list settings
  (`selected_calendars`) comma-separated; an addon with `options` offers its
  candidates (`/options`) as suggestions while free text stays possible.
  An addon with `connect_url` (the Google addons) has "Connect with Google" /
  "Sign in with Google again" (a link to that path) and a line saying one
  sign-in connects them all; coming back, `?google=<result>` is shown as a
  line under the header and dropped from the address.
  Save sends only what changed (`PUT`, a 400 shows under its field), Test
  connection (`POST …/test`), Disconnect behind a confirm that says what
  stops and that nothing else is deleted. A service not connected shows its
  fields and Connect; a official addon without fields has Connect /
  Disconnect on its row.
- **Read-only parts**: an addon or field with `can_change: false` has no input
  or button, only its value (secrets: set / not set) and `why_not` ("Set by
  an admin").
- **Honest states**: loading, load failed (Try again), saving, saved,
  testing, the test's result, the 503 `credentials_key` explained in a
  sentence. The service worker never caches `/api/me`, `/api/users` or
  `/api/addons*`.
- **No secret on the page**: secret values are never in a payload, so never
  in the HTML; what is typed lives in the input and in memory until sent and
  is dropped after a successful save (the form is drawn again, inputs empty)
  and when leaving the screen; nothing goes to `localStorage` or the log.

### HTTP API

`core/addons_api.py`, tests: `bin/test test_addons_api`.

| | |
|---|---|
| `GET /api/me` | `{id, name, access, capabilities: [...], addons: [...], languages: [...], currency}` — the caller, their level's capabilities, their connected addons (`essentials` first), their languages (the first is the default reply language) and currency (their own, else the server's: `core/user_locale.py`) |
| `GET` / `PUT /api/profile` | the caller's languages and currency: what counts, what they chose (`own`), the server's `defaults`; `PUT {languages, currency}` changes them (`[]` / `""`: back to the server's; a malformed code 400 `invalid` with `field`) (`core/profile_api.py`; the web Settings → Profile card) |
| `GET /api/users` | `{users: [{id, name, access}]}` — every user, for a caller with `manage_users` (the Addons page's picker); nothing else about them; 403 `forbidden` without the capability |
| `GET /api/addons` | `{user_id, addons: [<addon>, ...]}` — every addon the user may connect (not `essentials`; `ops` only with the `ops` capability) |
| `GET /api/addons/{name}` | `<addon>` |
| `PUT /api/addons/{name}` | body `{credentials?: {name: str \| null}, settings?: {name: value \| null}}` → `<addon>`; 201 when it connects, 200 when it updates. Merges: a credential given replaces the stored one (`null` removes an optional one), a setting given replaces its value (`null` = default) |
| `DELETE /api/addons/{name}` | `<addon>` with `connected: false`, plus `disconnected` (false when it was not connected) |
| `POST /api/addons/{name}/test` | `{name, checked, ok, reason}` — `checked: false, ok: null` for an addon without a check; `reason` is short and has no secrets |
| `GET /api/addons/{name}/options` | `{name, settings: {name: [{value, label}, ...]}}` — 404 `no_options` for an addon without any, 502 `unavailable` when the service cannot be read |

`<addon>` = `{name, description, connected, requires, can_change,
why_not, credentials: [{name, description, required, set, type, secret,
value?, can_change, why_not, rule}], settings: [{name, description, default,
value, type, can_change, why_not, rule}], account, check, options}`, plus
`account_conflict` (only while another user's connection holds the account
the service reports) and `problem: {field, message}` (only while a stored
address / name is not valid). Secret credentials are write-only: only whether
each is `set` comes back, never the value; an address credential
(`secret: false`: Home Assistant `host`, WAHA `url`) also carries its
`value`. `type` is the field type (`text`, `address`, `name`,
`telegram_chat`). `can_change` / `why_not` on the addon and
on each field say whether the caller may change it for this user and, when
not, why ("Set by an admin": `cameras` for a member, an address while no
admin allowed any origin of the addon, a Telegram chat for a member without a
Telegram id); `rule` is one line on what a
value may be ("Only an address an admin has allowed for this service.").
They come from `store.addon_rule` / `store.field_rule`, the functions the
store's checks use. An address is still never shown to another member: the
payload is only ever built for the connection's own user or an admin acting
with `?user=`.

Errors: 400 `{"error": "invalid", "field", "message"}` (a body that is not a
JSON object, an unknown top-level key, field, credential or setting, a
missing required credential, a credential that is not text, a value its
field type or the addon's rule refuses: "Security rules"; for `test` /
`options`, a stored address that is not valid); 403
`{"error": "forbidden", "needs": "<capability>"}` (an addon the target's level
may not connect, `?user=` without `manage_users`, an addon whose
`change_requires` the caller lacks); 403 `not_connected` for
`test` / `options` on an addon that is not connected; 404
`unknown_addon` (unknown addon, or `essentials`), `user_not_found`; 503
`credentials_key` when `CREDENTIALS_KEY` is missing (a save) or cannot
decrypt the stored credentials (a read). `?user=<id>` acts on
another user (needs `manage_users`; the target's level decides what they may
connect).

### How to

- **Add a user and pair their devices.** `bin/dana users add "Maya Levi" --pronoun she --context
  "Maya is a nurse in Haifa. Noa is her sister."`, then pair her first device:
  an admin starts a pairing for her (`POST /api/devices/pairings {"kind":
  "phone", "user_id": "<her id>"}`, which answers the QR string and the code)
  and her phone scans it in the app (a browser types the code on `/login`);
  she pairs her other devices herself from her Devices page (docs/devices.md) and, for Telegram, `bin/dana users telegram add maya
  <her Telegram user id>` plus `update maya --telegram-chat <chat id>` for
  proactive messages. She starts with only `essentials`.
- **Connect an addon from the page.** Settings → Addons (an admin first
  picks the user at the top), open the addon and follow its numbered steps:
  what to do outside Dana, then the sign-in button or the fields (each with
  where to find it), then Connect. Connect runs the connection check by
  itself and says what to fix when it fails; then pick the settings from the
  suggestions. Built-in addons (Tasks, Shopping, …) are one "Turn on". A
  field marked "Set by an admin" is one an admin sets for you.
- **Connect an addon with the CLI or the API.** Official addons need nothing:
  `bin/dana addons connect maya tasks` (her default lists are made
  at once). A service: put the credentials in a file (`host=…`,
  `token=…`), `bin/dana addons connect maya homeassistant
  --credentials-file ha.txt person=person.maya`, delete the file; or
  `PUT /api/addons/homeassistant` with her token. `POST
  /api/addons/homeassistant/test` says whether it answers, `GET
  /api/addons/homeassistant/options` lists candidates for her
  settings. No restart.
- **Share an item.** Ask Dana ("share the Groceries list with Maya, she can
  edit") — the `share_item` tool — or `PUT /api/sharing/{item_type}/{id}`
  (see "Sharing" → How to share). `bin/dana users sharing [USER]` shows who has
  access to what.

## Limits

- One connection per addon per user.
- One timezone for the whole server.
- No screen for users or sharing (the CLI and the API cover them);
  addons have the Addons page.
