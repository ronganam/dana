# Security

Dana holds people's mail, messages, calls, home controls and bank logins, so security reports are welcome and taken seriously.

**Please don't open a public issue for a vulnerability.** Report it privately through GitHub: the repository's **Security** tab → **Report a vulnerability**. Include what you found, how to reproduce it, and what it lets an attacker do. You'll get an answer within a week.

What is in scope: the server (`server/`), the bank worker, the Android apps and the Raycast extension — e.g. one user reaching another user's data or tools, getting past authentication, making Dana act without the approval it should ask for, or leaking credentials. The security model is described in [`server/docs/access.md`](server/docs/access.md) ("Security rules").

Running Dana yourself: keep `API_TOKEN` and `CREDENTIALS_KEY` secret, put the server behind HTTPS, and don't expose it to the internet without a token.

## Threat model

What Dana keeps, where it goes, and what you are trusting when you turn something on. It describes what the code does today; the details live in the linked docs.

### What the server stores, and where

- **Everything lives in the data directory**, the folder holding the SQLite database (`/app/db` in the container): the database itself (users, tokens, chats, tasks, notifications, call transcripts, mail Dana saved for you, transactions…), call recordings, saved media and uploads, the Android app updates it hosts, and the snapshots taken before each migration. Back it up, and protect it like the data it is: anyone who can read that folder can read your household's life.
- **Credentials are encrypted** with `CREDENTIALS_KEY` (Fernet) before they are stored: every addon's credentials (Home Assistant tokens, WAHA keys, Google and Telegram sign-ins) and the money sources' bank and card logins, passwords included. They are decrypted only in the server's memory, when an addon or a bank sync needs them; the API never returns a secret field's value, only whether it is set.
- **The key**: when `CREDENTIALS_KEY` is not set, the first start generates one into `credentials.key` next to the database (mode 0600). That is convenient, but the key then sits beside what it protects, so a copy of the data directory (a backup, a stolen disk) carries both. To keep them apart, set `CREDENTIALS_KEY` from a secret store (your orchestrator's secrets, a password manager, Infisical, Vault) and delete the file; the environment value takes precedence. Without the key the stored credentials cannot be read, so keep a copy of it somewhere other than the database's backup ([`server/docs/configuration.md`](server/docs/configuration.md)).
- **Sign-in tokens**: `API_TOKEN` (the admin's) and each device's token are stored as hashes. A leaked token acts as its user until you revoke it.

### What goes to the LLM provider, and what never does

Dana is an agent over a hosted language model (the provider is set by env, through LiteLLM). Whatever the model needs to read reaches that provider, under its terms:

- **Chat and voice**: your messages, the system prompt (your profile, the facts Dana remembers about you, your home's devices, the parts of the addons you have on) and every tool result in the turn: the mail, messages, notifications, events, transactions or files a tool read for the answer.
- **Background jobs of the features you turn on**: notification sorting and summaries, call-recording analysis (the transcript), people briefs, About-you learning from your chats and calls, transaction categories, link and file summaries, Telegram summaries, the morning briefing and the weekly review. Each job's model is set by env and its use is listed on the Usage screen ([`server/docs/usage.md`](server/docs/usage.md)).
- **Audio** (call recordings, voice notes, push-to-talk) goes to the transcription model's provider (Gemini by default). Web searches go to public search engines. Notification text goes to TypeSafe when `TYPESAFE_API_KEY` is set.
- **Never sent anywhere**: your credentials and passwords (not in prompts, not in tool results), and anything of an addon you have not turned on. **Push payloads** to the phone (Firebase) carry only a sequence number and the event's type; the phone then fetches the event from your server with its own token. Dana keeps **no location history**: it reacts to the zone and car changes your Home Assistant reports and keeps only when each reminder last fired.

### Addons run with the server's full access

Official addons, your site's addons and addons installed from the catalog all run inside the server process, with its full access: every user's data, the credentials key, the network. Each addon declares what it **needs** and what it **touches** (the hosts it contacts, whether it acts for you outside Dana, whether it keeps credentials, which other addons' data it reads); the Addons page shows that before you turn one on, installing shows it before Install, and an update that declares more asks again. That is a **disclosure, not a sandbox**: nothing stops an addon's code from doing more than it declares. Install only addons whose code you trust; an installed addon is pinned to the git commit you saw ([`docs/addons.md`](docs/addons.md)).

### Text written by other people

Mail, messages, notifications, transaction notes, web pages and the phone screen you share are written by others, and any of them can contain instructions aimed at Dana. The rules, as the code applies them ([`server/docs/agent-actions.md`](server/docs/agent-actions.md)):

- Tools that send to another person or destroy something (`confirm` tools: sending mail or WhatsApp messages, running code, deleting) always wait for your Approve, whatever the turn carries.
- Tools that act outside Dana without an undo (`acts_outside`: Home Assistant services, the TV, reading the cluster, saving a fact Dana will remember) run at once normally, but **ask first for the rest of a turn in which a tool returned text written by others** (`untrusted_result`: the mail, WhatsApp, Telegram, notification, call, money and web-search reads), in a turn that carries the phone screen you shared, and in a chat opened from a transaction. "The rest of the turn" is everything after your last message, including the continuation after an approval.
- Addons whose content is untrusted and that have no Approve card in voice (Gmail, your Telegram account) are not offered in realtime voice calls at all.
- The prompts tell the model to treat such text as material, never as instructions; the rules above are what holds when it does not.

### Network exposure

- The server refuses to start without `API_TOKEN` (only `DANA_INSECURE_DEV=1`, for local development, lifts that, and then listens on 127.0.0.1 only). Every request needs a token or the sign-in cookie, except the login page, the health check, the app-update downloads, static files and the WhatsApp webhook, which accepts only events WAHA signed with `WAHA_WEBHOOK_SECRET`.
- Docker Compose publishes the port on **127.0.0.1** only. To reach Dana from other devices, put a **reverse proxy with HTTPS** in front of it (or a VPN such as Tailscale or WireGuard), never the plain port on the internet ([`server/docs/operations.md`](server/docs/operations.md)).
- Give **each device its own token** (`bin/dana users tokens issue <user> --label phone`) and revoke a lost device's token (`bin/dana users tokens revoke`) instead of sharing `API_TOKEN`. A member can point an addon only at addresses an admin's own connection uses or `MEMBER_ALLOWED_ORIGINS` lists, and links a member saves are fetched only from public hosts, so one user cannot make the server call into your network ([`server/docs/access.md`](server/docs/access.md), "Security rules").
