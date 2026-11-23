# Install Dana: a guide for your coding agent

You are a coding agent (Claude Code, Codex, Cursor or another) helping a person install **Dana**, a
self-hosted personal assistant for a household: one server (Docker, SQLite) that the person's phone,
watch, wall tablet, TV, browser and Telegram talk to. The person may not be technical. Your job is to
ask the few questions that decide the setup, then do it with them step by step, until Dana answers on
their server and their phone is paired.

Source of truth: the repository <https://github.com/ronganam/dana>. When something here and the code
disagree, the code wins; the documents named below say what the code does. Read a document when its
step comes up, not all at once.

## Rules

- **Never print, echo, log or paste back the person's keys, tokens or passwords.** Ask them to put a
  secret into a file themselves (`.env`) or into the setup page in their browser. If they paste one
  into the chat anyway, use it only where it belongs and do not repeat it.
- **Never expose the server to the internet without authentication in front of it.** Dana's port is
  published on `127.0.0.1` only, on purpose. Reaching it from other devices goes through a VPN
  (Tailscale) or a reverse proxy with HTTPS, never by opening the port on a router or binding it to
  `0.0.0.0` on a public host.
- **Confirm before anything destructive**: deleting volumes or folders, `docker compose down -v`,
  overwriting an existing `.env` or `compose.yaml`, restoring a backup, removing a container.
- **Stop and ask when a step fails.** Show the error (without secrets), say what you think it means,
  and propose the next step. Do not improvise around a failure (other images, other ports, editing
  Dana's code).
- **One question at a time**, each with a recommended answer the person can accept with "yes".
- Only promise what Dana does. If the person asks for something this guide does not cover, look it
  up in the repository's docs before answering, and say so when it is not there.

## 1. Ask first

Ask these in order, one at a time. Skip a question whose answer is already clear. Keep the answers;
they decide the steps below.

1. **Where will Dana run?** This computer / a home server or NAS / a VPS (a rented server) /
   Kubernetes. *Recommended: a machine that is always on, at home (a mini PC, a NAS, a home server).
   This computer is fine for trying it.* The machine needs Docker, about 1 GB of memory for Dana, and
   amd64 or arm64 (the image is published for both).
2. **Is Docker there?** Check yourself when you can run commands on that machine (`docker version`,
   `docker compose version`). If not, help them install Docker Engine (or Docker Desktop) from
   Docker's own instructions for their system; ask before installing anything.
3. **How will you reach it from your phone and other devices?**
   - *LAN only* (at home, nothing else): fine for the browser on the same machine; the Android apps
     released on GitHub talk only to **HTTPS** servers, so the phone needs one of the next two.
   - *Tailscale* (*recommended*: private, HTTPS, works away from home, no ports opened): the server
     and the phone join the person's tailnet, and Tailscale serves Dana over HTTPS on the tailnet.
   - *A domain* they own, through a reverse proxy with a TLS certificate (Caddy, Traefik, nginx) on
     the same host. Only with authentication in front, or on a network only they reach; never a bare
     open port.
4. **Which model provider and key do you have?** Anthropic (Claude, the default) / Google Gemini /
   OpenAI / a local model through Ollama (experimental for tool use). *Recommended: Anthropic.* One key
   is enough; the setup page asks for it and tests it on the spot. Mention that voice messages and call
   recordings need a model that hears audio (a Gemini or OpenAI key) or a local Whisper server; Claude
   alone does not transcribe (`server/docs/configuration.md`, "Models" and "Transcription").
5. **Who will use it?** Just me / my household. *Members are added later in the browser
   (Settings → Household), each with their own data, add-ons and devices.*
6. **Which apps?** The Android phone app (*recommended*), the Wear OS watch, a wall tablet, the TV,
   Telegram. The browser always works.

Then say back in a few lines what you are going to do (where, how it is reached, which provider,
which apps) and wait for a yes.

## 2. Run the server

Read `server/docs/operations.md`, "Running it". On the machine that will run Dana, in a folder of its
own (e.g. `~/dana`):

```bash
mkdir -p ~/dana && cd ~/dana
curl -fsSLO https://raw.githubusercontent.com/ronganam/dana/master/compose.yaml
docker compose up -d
docker compose logs dana
```

`compose.yaml` runs the published image `ghcr.io/ronganam/dana:latest` (each release is also tagged
`ghcr.io/ronganam/dana:<version>`; pin one when they prefer upgrades by hand). Everything Dana writes
lives in the `data` volume. With no users and no `API_TOKEN` the server starts in **setup mode** and the
log has one line:

```
Dana is not set up yet. Open http://localhost:8080/setup?code=… to create the first admin.
```

The code is one-time and only in memory (a restart draws a new one: read the log again).

- **Same machine**: the person opens that link in their browser.
- **Another machine** (home server, VPS): the port listens on that machine's `127.0.0.1`. Either
  forward it over SSH from their computer, `ssh -L 8080:127.0.0.1:8080 <user>@<server>`, and open the
  link as printed, or do step 3 first and open the same `/setup?code=…` path on the HTTPS address.
- **Headless** (no browser at all, rare): put `API_TOKEN` in `.env` before the first start (they
  generate it: `openssl rand -hex 32`); the admin is then created from env and signs in with it
  (`server/docs/configuration.md`, "Security").

The setup page creates the person as the admin and walks them through a short wizard: their name,
their home (sets the server's timezone), **the model and its key** (they paste it there; it is tested,
stored encrypted, and you never see it), their phone (a QR code: step 5), add-ons, and an optional
made-up household to look around with. Let them do the wizard themselves; help when they ask.

### Kubernetes instead

One replica only (SQLite). Read `server/docs/operations.md`, "Running it" → Kubernetes, and
"Backups" → Kubernetes. The image runs as UID 1001 and keeps everything it writes in the directory of
`DATABASE_PATH` (default `/app/db/db.sqlite3`): mount one persistent volume at `/app/db`; probes TCP on
8080; secrets as env vars from a Secret. The repository has no chart: write a Deployment, Service and
PVC with them, show it before applying, and expose it only inside their network or tailnet.

## 3. Reach it from other devices

Set `PUBLIC_URL` to the address the devices will use (e.g. `https://dana.example.com`): pairing QR
codes and the setup link carry it (`server/docs/configuration.md`, "External services"). Settings go in
`.env` next to `compose.yaml`, one `NAME=value` per line, read at start:

```bash
docker compose up -d     # after changing .env: recreates the container with the new values
```

- **Tailscale**: install Tailscale on the server and the phone and sign both into the same tailnet
  (Tailscale's own instructions). Serving the local port over HTTPS on the tailnet is Tailscale's
  `serve` feature (check its current docs for the exact command and that HTTPS certificates are on for
  the tailnet). Then `PUBLIC_URL=https://<machine>.<tailnet>.ts.net`.
- **A domain**: a reverse proxy on the same host forwarding to `127.0.0.1:8080` with a certificate.
  Keep it reachable only from networks they trust, or put authentication in front. Dana reads
  `X-Forwarded-Proto` / `X-Forwarded-Host` from the proxy.
- **LAN discovery** (optional): the apps can find the server on the home network when something
  advertises `_dana._tcp` (`server/docs/devices.md`, "Finding the server on the home network": an
  Avahi example). It only fills the address field; pairing still needs the code.

## 4. Check it

```bash
curl -fsS http://127.0.0.1:8080/api/health          # {"ok": true, "version": "...", "time": ...}
docker compose ps                                   # "healthy" after a minute
```

And from another device, `https://<their address>/api/health`. The web app's **Needs attention** page
lists what the server checks and finds wrong (a missing key, a failed backup).

## 5. The apps

Each release on <https://github.com/ronganam/dana/releases> carries signed APKs and `SHA256SUMS`
(`android/docs/release.md`, "Releases"):

| File | What |
|---|---|
| `dana-phone-<version>.apk` | the phone app |
| `dana-wear-<version>.apk` | the Wear OS watch app (needs the phone app) |
| `dana-watchface-<version>.apk` | the Dana watch face |
| `dana-tablet-<version>.apk` | the wall-tablet (kiosk) app |
| `dana-tv-<version>.apk` | the TV home screen |

Ask before downloading. Check each file against `SHA256SUMS` (`sha256sum -c SHA256SUMS
--ignore-missing`). The phone installs its APK from the browser (Android asks to allow installs from
that source); the tablet, the TV and the watch face install with `adb install <file>` from a computer
with the device in developer mode. After that, the server offers phone and watch updates by itself.

**Pairing** (`server/docs/devices.md`, "Pairing"): no app has a server built in.

- **Phone**: in the web app, Devices → Pair a device → Phone shows a QR code and an 8-character code
  (valid 5 minutes, used once). The phone's camera opens the QR code in Dana, or the person types the
  server address and the code. The release app needs the HTTPS address from step 3.
- **Watch**: pairs itself through the paired phone when the watch app starts. Without the phone:
  Devices → Pair a device → Watch, and the code on the watch.
- **Tablet, TV**: they open on a pairing screen; Devices → Pair a device → Tablet / TV gives the code.
- **Household members**: Settings → Household → add a person, then Invite: their phone's QR code and
  a code for their browser.
- **Push to the phone**: the free ntfy app on the phone (UnifiedPush); without it the app still works
  and catches up when it syncs (`android/docs/notifications.md`).

## 6. Telegram (optional)

To chat with Dana in Telegram: the person makes a bot with Telegram's @BotFather and puts its token in
`.env` as `TELEGRAM_BOT_TOKEN` themselves; then `docker compose up -d`. The bot refuses everyone until a
Telegram id is linked to a user: a stranger's message is answered with their id, and the admin links it:

```bash
docker compose exec dana dana users telegram add <user> <telegram id>
```

(`server/docs/access.md`, "Telegram".)

## 7. Backups

Read `server/docs/backups.md`. In the web app, Settings → Backups: pick a schedule (daily is a good
default) and how many to keep; archives go to the `backups` volume (`/backups`). Recommend keeping them
on another disk or a NAS: bind-mount a host folder writable by UID 1001 at `/backups` in
`compose.yaml` (e.g. `- /mnt/nas/dana-backups:/backups`), with their yes. An archive holds every user's
data and the key that decrypts their stored credentials: keep it as safe as the server. Restoring
(`dana backups restore`, the server stopped) replaces the database: confirm first.

## 8. Upgrades

```bash
docker compose pull && docker compose up -d
```

A newer Dana migrates the database at start, after writing a copy of it to `snapshots/` in the data
volume (`server/docs/operations.md`, "Database schema").

## 9. What next

- **Add-ons**: Settings → Addons shows what each one adds, what it needs and what it may touch, with
  numbered setup steps (Home Assistant, Google, WhatsApp, money…). More install from the catalog on the
  same page (admins). Turning one on is per person.
- **Build an add-on** for something only they need: give the agent
  <https://ronganam.github.io/dana/build-an-addon.md>.
- Configuration reference: `server/docs/configuration.md`. Everything else: the repository's
  `AGENTS.md` and `server/AGENTS.md`.

End by telling the person, in a few lines: the address Dana answers on, where its data and backups
live, how to upgrade, and what is left to do (apps not yet paired, add-ons to set up).
