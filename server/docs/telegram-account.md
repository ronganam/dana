# Telegram account (your own chats and channels)

The `telegram_account` kind reads a user's **own** Telegram account, read-only:
their channels, groups and private chats, to list and summarize what is
unread. It is not Dana's Telegram bot (`core/telegram_bot.py`), which only
sees chats with the bot.

It uses Telegram's user API (MTProto) through Telethon, signed in as the user
the way a second Telegram Desktop is.

## Pieces (`integrations/telegram_account/`)

| File | What |
|---|---|
| `config.py` | the kind name, the credential `session`, the server's Telegram app (`TELEGRAM_API_ID` / `TELEGRAM_API_HASH`) |
| `kind.py` | the declaration: credential `session`, `connect_url=/telegram/connect`, `connect_label="Connect Telegram"`, `realtime=False`, `on_change` drops the user's clients, the prompt guide |
| `client.py` | `make_client` (the one place Telethon is built; tests replace it), `acquire(conn)`, `dialogs`, `messages`, `check`, `explain` (errors in words), plain `Chat` / `Message` data |
| `login.py` | the QR sign-in, per user, in memory |
| `api.py` | the connect page and the sign-in routes |
| `tools.py` | the three tools |

**The app and the session.** An MTProto client needs an *app* id and hash
(my.telegram.org → API development tools). They identify Dana, not a person,
so they are server-wide env values (docs/configuration.md). Each user's
signed-in session (a Telethon `StringSession`: full access to the account) is
the credential of their connection, encrypted with `CREDENTIALS_KEY` like any
credential, never returned by an API, tool or log. No env import: only the QR
sign-in connects it.

## Sign-in (QR)

The Integrations page shows **Connect Telegram** (`connect_url`, the caller's
own only), which opens `/telegram/connect` (`static/telegram-connect.html`).
The page starts a sign-in and polls it:

- `POST /api/telegram/login` connects a fresh client (with updates: the QR
  login completes on Telegram's `UpdateLoginToken`) and asks for a login
  token. 503 `telegram_not_configured` without the server's app; 502 when
  Telegram cannot be reached.
- `GET /api/telegram/login` → `{status, configured, qr_svg?, expires_in?,
  account?, error?}`. `status`: `none` · `starting` · `qr` · `password` ·
  `connected` · `expired` · `failed`. The QR code (`tg://login?token=…`, as
  SVG) is scanned from the phone: Settings → Devices → Link Desktop Device. A
  token lives about 30 seconds and is renewed until 5 minutes have passed
  (`expired`).
- With two-step verification the scan ends in `password`; `POST
  /api/telegram/login/password {password}` finishes (`error: "bad_password"`
  and still `password` while it is wrong; `not_waiting` without such a
  sign-in).
- `DELETE /api/telegram/login` cancels.
- On success the session is stored on the user's connection (connected, or
  updated when it already was), `account` = the account's name and
  `@username`, the user's reading clients are dropped and the sign-in client
  disconnects. One sign-in per user at a time; another user's is never
  visible.

Disconnecting on the Integrations page removes the stored session. The
session stays listed in Telegram's Devices as "Dana" until the user removes it
there.

## Reading

- `acquire(conn)` keeps one connected client per (user, session), created on
  first use and dropped after 10 minutes unused or when the connection
  changes (`on_change`). Reading clients take no updates: nothing runs in the
  background and the account is not shown online. A session Telegram no
  longer accepts says "signed out … sign in again on the Integrations page"
  (also the `check`).
- `dialogs(conn)`: every chat, most recent first, with its type (`channel`,
  `group`, `private`, `bot`), unread and mention counts, `muted` (the chat's
  own mute, not Telegram's global per-type setting), `archived` and read mark;
  cached a minute (the unread tools always ask fresh).
- `messages(conn, chat, limit, unread_only)`: the latest messages, with
  `unread_only` only those after the read mark (`min_id`), oldest first.
  Media in words (`[Photo]`, `[Voice message]`, `[File: name (type)]`, a poll's
  question …), text capped at 1500 characters per message.
- **Nothing marks anything read**: that takes an explicit `ReadHistory`
  request, which this package never sends.

## Tools

All three are read-only and `untrusted_result` (the rest of the turn asks
before any `acts_outside` tool, docs/agent-actions.md); each result starts by
saying the messages are others' text. Chat only (`realtime=False`, as gmail).

| Tool | What |
|---|---|
| `list_telegram_unread(chat_type, include_muted, include_archived, limit)` | unread chats, muted and archived skipped unless asked; `chat_type`: `all` · `channels` · `groups` · `private` |
| `read_telegram_chat(chat, limit, unread_only)` | one chat by id, `@username`, exact or partial name (several matches: the candidates) |
| `summarize_telegram_unread(chat_type, include_muted, include_archived, max_chats, focus)` | reads the newest 40 unread messages of each unread chat (up to 30 chats, three at a time), at most 60,000 characters, fenced between `<<<MESSAGES` / `MESSAGES>>>`, and has the `telegram` job (`TELEGRAM_SUMMARY_MODEL` → `BACKGROUND_MODEL`, purpose `telegram_summary`) write 1–4 bullets per chat; then what it covered and what it could not read |

Tests: `python test_telegram_account.py` (a fake Telethon client per session).
