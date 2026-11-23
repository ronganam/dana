# Security

Dana holds people's mail, messages, calls, home controls and bank logins, so security reports are welcome and taken seriously.

**Please don't open a public issue for a vulnerability.** Report it privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Include what you found, how to reproduce it, and what it lets an attacker do. You'll get an answer within a week.

What is in scope: the server (`server/`), the Android apps and the Raycast extension — e.g. one user reaching another user's data or tools, getting past authentication, making Dana act without the approval it should ask for, or leaking credentials. The security model is described in [`server/docs/access.md`](server/docs/access.md) ("Security rules"). The bank worker lives in the money_il add-on's own repository (`github.com/ronganam/dana-addon-money-il`); report its issues there the same way.

Running Dana yourself: an LLM key is the only value to set, then open the setup link the server logs at its first start (it creates the first admin in your browser). Keep `CREDENTIALS_KEY`, device tokens and `API_TOKEN` (when you run headless) secret, put the server behind HTTPS, and finish setup before anyone else can reach it.

## Threat model

What Dana keeps, where it goes, and what you are trusting when you turn something on. It describes what the code does today; the details live in the linked docs.

### What the server stores, and where

- **Everything lives in the data directory**, the folder holding the SQLite database (`/app/db` in the container): the database itself (users, tokens, chats, tasks, notifications, call transcripts, mail Dana saved for you, transactions…), call recordings, saved media and uploads, the Android app updates it hosts, and the snapshots taken before each migration. Back it up, and protect it like the data it is: anyone who can read that folder can read your household's life.
- **Credentials are encrypted** with `CREDENTIALS_KEY` (Fernet) before they are stored: every addon's credentials (Home Assistant tokens, WAHA keys, Google and Telegram sign-ins) and the money sources' bank and card logins, passwords included. They are decrypted only in the server's memory, when an addon or a bank sync needs them; the API never returns a secret field's value, only whether it is set.
- **The key**: when `CREDENTIALS_KEY` is not set, the first start generates one into `credentials.key` next to the database (mode 0600). That is convenient, but the key then sits beside what it protects, so a copy of the data directory (a backup, a stolen disk) carries both. To keep them apart, set `CREDENTIALS_KEY` from a secret store (your orchestrator's secrets, a password manager, Infisical, Vault) and delete the file; the environment value takes precedence. Without the key the stored credentials cannot be read, so keep a copy of it somewhere other than the database's backup ([`server/docs/configuration.md`](server/docs/configuration.md)).
- **Sign-in tokens**: each device's token (made by pairing) and, on a headless install, `API_TOKEN` are stored as hashes. A leaked token acts as its user until you revoke the device.

### What goes to the LLM provider, and what never does

Dana is an agent over a hosted language model (the provider is set by env, through LiteLLM). Whatever the model needs to read reaches that provider, under its terms:

- **Chat and voice**: your messages, the system prompt (your profile, the facts Dana remembers about you, your home's devices, the parts of the addons you have on) and every tool result in the turn: the mail, messages, notifications, events, transactions or files a tool read for the answer.
- **Background jobs of the features you turn on**: notification sorting and summaries, call-recording analysis (the transcript), people briefs, About-you learning from your chats and calls, transaction categories, link and file summaries, Telegram summaries, the morning briefing and the weekly review. Each job's model is set by env and its use is listed on the Usage screen ([`server/docs/usage.md`](server/docs/usage.md)).
- **Audio** (call recordings, voice notes, push-to-talk) goes to the transcription model's provider (Gemini by default). Web searches go to public search engines. Notification text goes to TypeSafe when `TYPESAFE_API_KEY` is set.
- **Never sent anywhere**: your credentials and passwords (not in prompts, not in tool results), and anything of an addon you have not turned on. **Push payloads** to the phone (Firebase) carry only a sequence number and the event's type; the phone then fetches the event from your server with its own token. Dana keeps **no location history**: it reacts to the zone and car changes your Home Assistant reports and keeps only when each reminder last fired.

### Addons run with the server's full access

Official addons, your site's addons and addons installed from the catalog all run inside the server process, with its full access: every user's data, the credentials key, the network. Each addon declares what it **needs** and what it **touches** (the hosts it contacts, whether it acts for you outside Dana, whether it keeps credentials, which other addons' data it reads); the Addons page shows that before you turn one on, installing shows it before Install, and an update that declares more asks again. That is a **disclosure, not a sandbox**: nothing stops an addon's code from doing more than it declares. Install only addons whose code you trust; an installed addon is pinned to the git commit you saw ([`docs/addons.md`](docs/addons.md)).

### Text written by other people

Mail, messages, notifications, call transcripts, transaction notes, web pages, media titles and the phone screen you share are written by others, and any of them can contain instructions aimed at Dana. The rules, as the code applies them ([`server/docs/agent-actions.md`](server/docs/agent-actions.md), "Text written by others"):

- Tools that send to another person or destroy something (`confirm` tools: sending mail or WhatsApp messages, running code, deleting) always wait for your Approve, whatever the turn carries.
- Tools that act outside Dana without an undo (`acts_outside`: Home Assistant services, the TV, reading the cluster, saving a fact Dana will remember) run at once normally, but **ask first in a turn that carries text written by others**: from the first step when the turn starts with it (the phone screen you shared, a chat opened from a transaction, a call, a notification or a person, a message you forwarded to the Telegram bot), and for the rest of the turn once a tool returned it (`untrusted_result`: the mail, WhatsApp, Telegram, notification, call, suggestion, people, knowledge, money, calendar and web-search reads; a Home Assistant listing that shows what a media player is playing). "The rest of the turn" is everything after your last message, including the continuation after an approval. Learned phrases are never used or learned in such a turn.
- That text reaches the model inside a frame (`core.text.untrusted`): fences with a random nonce, a sentence saying it is data written by others, and the fence's name neutralised inside the text so it cannot close the frame. The background jobs that read others' text without tools (call analysis, notification suggestions and summaries, people briefs, link summaries, the About learner) frame it too; they only write suggestions and summaries you see.
- Addons whose content is untrusted and that have no Approve card in voice (Gmail, your Telegram account) are not offered in realtime voice calls at all.
- **What this does not protect**: it is a speed bump for the model, not a sandbox. Reversible writes (tasks, notes, shopping, mail drafts, archive and trash) still run at once in such a turn, so a crafted message can make Dana do something you then undo; text written by other members of your household (shared tasks, lists, projects) is not treated as untrusted; an approval card shows what a call will do, not which text suggested it; and the model may still repeat or act on what it read in ways that need no tool.

### Network exposure

- **Setup mode**: a new server with no users and no `API_TOKEN` answers only its setup page and API, the health check and static files until the first admin is created with the one-time setup link it logs (random, kept only as a hash, a new one at each restart, wrong codes rate-limited). Anyone who can reach the server with the link's code before you becomes its admin, so open it right away, from the machine itself or over your VPN. `API_TOKEN` (headless installs) skips setup mode; `DANA_INSECURE_DEV=1`, for local development only, turns authentication off and listens on 127.0.0.1 only ([`server/docs/devices.md`](server/docs/devices.md)).
- After setup every request needs a token or the sign-in cookie, except the login page, the health check, pairing (`/api/pair`: a short-lived one-time code, rate-limited), the app-update downloads, static files and the WhatsApp webhook, which accepts only events WAHA signed with `WAHA_WEBHOOK_SECRET`.
- Docker Compose publishes the port on **127.0.0.1** only. To reach Dana from other devices, put a **reverse proxy with HTTPS** in front of it (or a VPN such as Tailscale or WireGuard), never the plain port on the internet ([`server/docs/operations.md`](server/docs/operations.md)).
- **Each device has its own token**, made by pairing it from the Devices page (a QR code or an 8-character code valid for five minutes); remove a lost device there and its token stops working at once. Don't share `API_TOKEN` between devices. A member can point an addon only at addresses an admin's own connection uses or `MEMBER_ALLOWED_ORIGINS` lists, and links a member saves are fetched only from public hosts, so one user cannot make the server call into your network ([`server/docs/access.md`](server/docs/access.md), "Security rules").
