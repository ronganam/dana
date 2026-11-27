# WhatsApp (WAHA)

Each user connects their own WAHA session: the `whatsapp` addon
(docs/access.md, "Addons") holds `url`, `api_key` (optional: no `X-Api-Key`
header without one) and `session` as encrypted credentials, the setting
`voice_telegram_chat_id`, and the session's own WhatsApp id in
`addons.account`. The first admin's values come once from env
(`addons/bootstrap.py`; docs/configuration.md); nothing else reads the
old env names.

## The package (`addons/whatsapp/`)

| File | What |
|---|---|
| `addon.py` | the declaration (`on_connect` / `on_credentials_change` learn the account, `blocked` refuses the tools of a connection in an account conflict) |
| `config.py` | kind name, WAHA's default session name, the `voice_telegram_chat_id` setting and `voice_chat_id(conn)` (strict: an int or None, logged) |
| `client.py` | per connection: `config(conn)` → (base url, headers, session), `signature(conn)` (cache key), `fetch_me(conn)` / `get_my_identifiers(conn, client)`, the chat-id → name map (5 min, per connection), `get_message_sender_name`, `download_media_bytes(conn, url)` |
| `resolve.py` | `resolve_chat_id_and_name(conn, name)`: saved contacts → chat id (a message id given as the chat → its chat, `chat_of`) → preferred contacts → WAHA chats / contacts (transliteration, recency, the chat model `purpose="whatsapp_match"`) → number check |
| `contacts.py` | the user's saved contacts (table `whatsapp_contacts`): `resolve_rows` / `search_rows` (nicknames whose value is a chat id), `preferred(user, name)` (category `preferred`), `known_people(user)` (contact rows for the call-analysis prompt; empty without the addon) |
| `accounts.py` | the account of each connection (one connection per account), the routing of inbound events (below), `blocked(conn)` |
| `events.py` | inbound events: `handle_webhook(data)` → `process_event(conn, data)` |
| `messages.py` | one message as a line of a read tool's result (below), the messages query, `recent(conn, chat_id, limit)` (a chat's last messages as plain rows, nothing downloaded: the phone addon's suggestion context) |
| `tools.py`, `latest.py` | the tools (`get_latest_messages` in `latest.py`, registered last so the order is unchanged) |

Everything a tool, an event or a cache touches comes from the user's
connection; caches (name map, "me", dedupe) are keyed by `signature(conn)`.

**Saved contacts.** Migration `m0011_whatsapp_per_user` copied the contact
rows of the old `memories` table (a value that is a WhatsApp chat id, or a
contact category / key) to the first admin, plus the preferred contacts
the resolver had in its code. The rows in `memories` stay for
the About addon's one-time import; the addon reads only its own table.

**Reading.** `read_chat_messages` and `get_latest_messages` print every
message through `messages.format_message`: `[time] **sender**: content (id:
`…`)`. The id (WAHA's serialized id, `false_<chat>_<id>`) is the handle for
`forward_whatsapp_message`, `edit_whatsapp_message`,
`delete_whatsapp_message` and `send_whatsapp_message(reply_to=)`. Media is
described, and a caption is kept after it: a voice note transcribed, any
other file as `[File: <name> (<mimetype>)]` (`Image` / `Video` / `Audio` for
those). WAHA is asked to download only `audio/` (`downloadMediaMimetypes`);
other files come with their name and type and no download, and
`read_whatsapp_file` reads one when it matters. The read tools (and
`find_last_chats`, which shows last messages, and `read_whatsapp_file` /
`save_whatsapp_file`, which carry the file's text and name) are
`untrusted_result`: their text is written by other people, so the model
reads it framed (`core.text.untrusted`) and the rest of the turn asks before
any `acts_outside` tool (docs/agent-actions.md, "Text written by others").
The voice-note summary (the Telegram button) frames the transcript the same
way.

**Forwarding.** `forward_whatsapp_message(chat_id, message_id, what)`
(`confirm`) posts WAHA's `/api/forwardMessage` with the connection's session:
WAHA forwards the stored message itself (a PDF, photo, video, voice note or
text), so nothing passes through Dana and no URL is needed. An id without
`_` and `@` is refused before any request (by every tool taking a message id); WAHA's 422 (not in its store) is
reported as "no message with id". The approval card shows the recipient,
`what` (the model's few words) and the source chat read from the id (not for
a `@lid` chat). `send_whatsapp_file` stays for files at a public URL.

**Reading a file.** `read_whatsapp_file(chat_id, message_id)` downloads that
message's media through WAHA (`downloadMedia=true`) and returns its text
(`llm.read_file`, purpose `whatsapp_file`): a PDF or document written out, a
photo described with its text, a voice note transcribed; up to 10,000
characters (longer is cut, with a note). The last 32 texts are remembered per
user and message, so keeping the file right after does not read it again.

**Saving a file to a project.** `save_whatsapp_file(chat_id, message_id,
item_id, note)` (needs the knowledge addon; a write with Undo) downloads the
media the same way and attaches it to the project entry `item_id`
(`addons/knowledge/files.py`, source `whatsapp`); the file keeps its name, or
gets the chat's name and the message's time. Its text is the one
`read_whatsapp_file` just read, else it is read in the background.
Docs/knowledge.md → Sections, decisions and files.

## Inbound events (`/webhook/waha`)

- WAHA forwards `message` events (and `session.status`, when listed: a
  signed-out session then shows as a Reconnect card at once instead of at
  the next check) to `POST /webhook/waha`, **signed**. On the
  WAHA side (its environment, for every session):

  ```
  WHATSAPP_HOOK_URL=http://<dana's address>/webhook/waha   # e.g. the in-cluster Service
  WHATSAPP_HOOK_EVENTS=message,session.status
  WHATSAPP_HOOK_HMAC_KEY=<the same value as Dana's WAHA_WEBHOOK_SECRET>
  ```

  (or per session: `config.webhooks[].hmac.key` in WAHA's session API). WAHA
  then sends `X-Webhook-Hmac` (hex HMAC-SHA512 of the raw body) and
  `X-Webhook-Hmac-Algorithm: sha512`; Dana recomputes it with
  `WAHA_WEBHOOK_SECRET` (`addons/whatsapp/api.py`, constant-time compare)
  and refuses anything else with 403 — an unsigned event, a changed body, a
  forged `Host`. Without the secret every unsigned event is refused (the
  start logs it). With a token only a user with `manage_server` may post
  (testing; anyone else 403), so a user cannot forge an event for another
  user's account. The handler answers at once and processes in the
  background.
- **Media** named by an event is always fetched from the connection's own
  WAHA: its scheme and host, with only the path and query of the event's URL
  (`client.media_url_on_connection`), so the API key never goes to another
  origin. Every WAHA request URL is built with `client.api_url(conn, *parts)`
  (the parts percent-encoded, joined by `addresses.join`).
- **Routing** (`accounts.route`): WAHA's webhook body names the session's own
  account at the top level:

  ```json
  {"event": "message", "session": "default", "engine": "NOWEB",
   "me": {"id": "972501234567@c.us", "pushName": "…"},
   "payload": {"id": "false_…@lid_3A48…", "from": "…@lid", "fromMe": false,
               "hasMedia": true, "media": {"url": "…", "mimetype": "audio/ogg; codecs=opus"}, "_data": {…}}}
  ```

  The event goes to the one connection whose stored account matches `me.id`
  (compared by the number before `@` and any `:device` suffix; `route()`
  returns at most one connection). When none matches, the accounts of
  connections that have none are learned (at most once a minute per
  connection) and the match is tried once more. Still none: logged (event and
  session name, no content) and dropped. Without `me`, the `session` name
  decides only when exactly one connection without an account conflict uses
  it; otherwise dropped.
- **The account** is the `me.id` of the connection's session in WAHA's
  `GET {url}/sessions` (the entry whose `name` is the session). It is learned
  right after the addon is connected or its credentials change (the old
  account is forgotten first), by the `learn_account` job (`hooks.py`:
  connections without one, at start and every minute; WAHA being down only
  delays it), and lazily as above.
- **One connection per account** (`store.claim_account`). Storing a learned account is a claim,
  checked and written in one transaction: when another user's connection
  already holds the same account, this connection gets none and the reported
  account is kept in `addons.account_conflict` (a warning in the log
  names both user ids, no message content). Such a connection is never
  routed to and every WhatsApp tool answers "This WhatsApp account is already
  connected by another user, so WhatsApp can't be used here." without a WAHA
  request (`Addon(blocked=accounts.blocked)`). It is still asked like
  any connection without an account, so the conflict clears by itself when
  its WAHA reports another account (or the holder disconnects); a
  credentials change forgets both and asks again. Admins see it in `GET
  /api/addons/whatsapp?user=<id>` (`account_conflict`, present only
  then) and `bin/dana addons show <user>`. This is what stops a
  member's own WAHA (allowed by `MEMBER_ALLOWED_ORIGINS`) from claiming the
  admin's number, and the admin's session being used under a second hostname
  of the same WAHA (the session check compares address strings).
- **Reconnect** (`client.session_status`): WAHA refusing the API key (401 /
  403 in the check) or the session in `SCAN_QR_CODE` (WhatsApp signed it out)
  or `FAILED`, seen by the check or a `session.status` event, marks the
  connection for reconnecting (`store.needs_reconnect`: a Reconnect card,
  `docs/now.md`, "Reconnect"); the session `WORKING` again, new credentials
  or a passing test clear it.
- **Processing** (`events.process_event`, as the connection's user): only
  incoming 1:1 `audio/*` voice notes (`ptt`) are downloaded from the user's
  WAHA, transcribed with `core/llm.transcribe` (`source="whatsapp"`, usage
  theirs), then delivered to the phone (`events._notify_phone`: the one phone
  seam; phone push is not per user yet, so only a user with the `phone` addon
  gets it) and to Telegram (`core.notify.whatsapp_voice(user, chat_id, …)`,
  the chat of their `voice_telegram_chat_id` setting; none set: logged, not
  sent). This happens before the message is announced, so its event carries
  the transcript (`transcript`, below; remembered for the read tools, so
  reading the chat does not transcribe it again). Groups, outgoing and
  non-voice messages are not transcribed (only announced, below);
  redelivered message ids are deduped in memory per connection. Transcription needs a
  model that hears audio (docs/configuration.md, "Transcription": an OpenAI or
  Gemini key, or a local Whisper server); without one the voice note is
  logged and skipped, the admins' `transcription` self-check says why, and a
  voice note read through the tools reads "not transcribed: the server needs
  a transcription model".
- **Events for watches and suggestions** (`event_types.py`, `Addon(events=)`): every new
  `message` event routed to a connection — 1:1 or group, incoming or the
  user's own from another device — is emitted once as
  `whatsapp.message.received` for that connection's user (an incoming 1:1
  voice note after its transcription): `{message_id, chat (the contact, or the
  group's name), chat_id (the chat's id: `…@c.us`, `…@g.us`
  or `…@lid`; what a reply passes as `chat_id`), sender (display name; "Me" for the
  user's own), is_group, from_me, text (the body or a caption; empty for a
  voice note), reply_to (the quoted message's text, WAHA's `replyTo.body`;
  empty when it quotes none), transcript (an incoming 1:1 voice note's text;
  empty otherwise or without a transcription model), has_media, media_type
  (voice | audio | image | video | sticker | document, or empty)}`. Besides
  watches, the phone addon reads it: while WAHA covers the user, WhatsApp
  suggestions come from these events (bursts per chat, with the chat's recent
  history read through `messages.recent`) instead of the phone's WhatsApp
  notifications (docs/notifications.md, "WhatsApp through WAHA"). Names come from
  the connection's chat-id map (`client.get_chat_id_to_name_map`, cached 5
  min). Redeliveries are deduped by message id per connection (the same set
  as the voice notes', in memory). Watches usually want `from_me eq false`.
  The data is other people's text: the watches engine frames it
  (`core.text.untrusted`), and so does the phone addon's extraction prompt.
  No history: Dana keeps no copy of messages (dry runs see none; the phone
  addon keeps the burst rows it suggests from, like any notification, under
  its retention).
- The Telegram copy carries a `✨ Summarize` inline button (`wa_sum:<hash>` →
  `TelegramBot.handle_whatsapp_summary_callback`), which summarizes the
  transcript on demand with the model (`core/voice.summarize_text`) and edits
  the message in place — only for the user the transcript belongs to (anyone
  else gets "⛔ This transcript belongs to someone else."). Transcripts live
  in an in-memory bounded cache (`TelegramBot.wa_voice_transcripts`, with
  their user), so old buttons report "expired" after a restart. At startup
  the adapter logs, per user with WhatsApp, where their transcripts go
  (`report_whatsapp_voice_chats`; a missing / malformed setting is a warning).
- Tests: `bin/test test_waha_voice` (the pipeline, all network calls mocked),
  `bin/test test_whatsapp_events` (`whatsapp.message.received`: shapes, groups,
  from_me, media, replies, transcripts, dedupe, two users),
  `bin/test test_whatsapp_suggestions` (the phone addon's suggestions from it),
  `bin/test test_whatsapp_users` (two fake WAHA instances: tools, caches,
  contacts, images, reading with ids and file names, forwarding, accounts,
  routing, Summarize per user), `bin/test
  test_security` (one connection per account).

**To verify against the real WAHA before relying on it** (the fake follows
WAHA's documentation, not a live instance): that webhook bodies carry the
top-level `me.id` for the engine in use, and that `GET /api/sessions` lists
the session with `me.id` in the same form (`account_key` tolerates a
`:device` suffix and `@s.whatsapp.net`). Until an account is learned, events
fall back to the session name.
