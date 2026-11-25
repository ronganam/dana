# Devices, pairing, setup mode and the wizard

Every app instance that talks to Dana (a phone, a watch, a wall tablet, a TV, a browser, another client) is a
**device** of one user with its own revocable token. A device is made by **pairing**: a signed-in user shows a
short code (and a QR code), the new device sends it to the server and gets its token. A fresh server with no
users starts in **setup mode** and waits for its first admin; then every user goes through the **first-run wizard**.

Code: `core/devices.py` (devices, pairing), `core/devices_api.py` (HTTP), `core/setup.py` +
`core/setup_api.py` (setup mode), `core/ratelimit.py` (wrong-code limits), `core/public_url.py` (the address
in QR codes and the setup link), `core/auth.py` (the request's device, `/login` with a code); web:
`static/devices.js` + `devices.css` (the Devices page), `static/setup.html` + `setup.js` (the setup page),
`static/login.html`; the wizard: `core/wizard.py` + `core/wizard_api.py`, its steps in `static/setup.js`. Tables:
core migrations `m0005_devices`, `m0006_zones_wizard` (`user_wizard`). Tests: `bin/test test_devices`,
`bin/test test_web_devices`, `bin/test test_wizard`, `bin/test test_web_setup`.

## Device

| Field | Meaning |
|---|---|
| `id` | 12 hex chars |
| `user_id` | whose device it is (never changes) |
| `name` | one line, at most 60 characters; empty → the kind's name ("Phone") |
| `kind` | `phone`, `watch`, `tablet`, `tv`, `browser`, `other` (checked in code: `devices.KINDS`) |
| `room` | free text, at most 40 characters, may be empty |
| `role` | empty for a personal device; `kitchen`, `bedroom`, `hall`, `living_room` for a household tablet or TV (`devices.ROLES`; only a tablet or a TV has one): its dashboard starts from that room's preset (`docs/dashboard.md`) |
| `token_id` | its own row in `user_tokens` (core/users.py): an ordinary user token, hashed; NULL for a phone moved from the phone addon's old push registrations, until it is paired (`paired: false` in the API) |
| `created_at`, `last_seen_at` | ISO UTC; `last_seen_at` is written by requests made with its token, at most every 5 minutes (`devices.TOUCH_INTERVAL_SECONDS`) |

Revoking a device deletes its token in the same transaction: the next request made with it gets 401 (the
in-memory token lookup is refreshed at once). Revoking the token itself (`bin/dana users tokens revoke`)
removes the device too (`ON DELETE CASCADE`). Kinds and roles are checked in code, so adding one needs no
migration.

**How a request learns its device.** The auth middleware resolves the Bearer token or the session cookie to
`users.Identity(user, token_id)`; `devices.by_token_id(token_id)` (an in-memory copy, refreshed after every
change and at most every 10 s) gives the device, which is set as `request["device"]` (read it with
`core.auth.request_device(request)`; None for a token of no device, `API_TOKEN`, or a request without
credentials in local development) and touched. A browser signed in with a pairing code has its cookie made
from its device token, so cookie requests carry the device too. The stored token's id (never the token) is
`request["token_id"]` (`core.auth.request_token_id`; None for `API_TOKEN`, local development or a demo view).

## Tokens without a device

A token issued by hand (`bin/dana users tokens issue`) belongs to no device: a script's, or an app set up
before devices had their own tokens (a deployment upgraded from then keeps such phones, TVs and tablets
signed in; its moved phone rows have `token_id` NULL). Requests made with it work, but Dana can't
tell which device they come from: the phone's push registration (`POST /api/phone/register`) is refused,
per-device features (a tablet's or TV's dashboard) can't find it, and the phone's events feed answers
`"device": false` and marks the fetch, so the `push` self-check says "This phone signs in with an old token"
(`docs/notifications.md`) and the app asks to pair again (`../android/docs/phone.md`, "Pairing").

`GET /api/devices` lists them as `unpaired_tokens` (`devices.unpaired_tokens`) and the Devices page shows
them as **Signed in without pairing**, with a hint: pair the device, then revoke its token. There is no
reliable marker of what a token is for (a browser that signed in at `/login` with a token, not a code, uses
that token too), so every token of no device is listed with its label. `DELETE /api/devices/tokens/{id}`
revokes one (its user, or `manage_users`; a device's token, someone else's or an unknown id: 404).

## Pairing

1. A signed-in user starts a pairing for themselves: `POST /api/devices/pairings {kind, name?, room?, role?}`.
   With `manage_users` they may start one for another user (`user_id`: the Household screen's invites, a phone
   and a browser code at once: `docs/household.md`); anyone else gets 403 `forbidden`.
2. The answer has a **code**: 8 characters from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ` (no 0/O, 1/I), valid 5
   minutes, used once, stored as its SHA-256 only; and **`qr`**, the string the QR code holds:
   `dana://pair?server=<public base URL>&code=<code>`. The base URL is `PUBLIC_URL` when set, else the
   request's `X-Forwarded-Proto` / `X-Forwarded-Host` (first value), else the request's own scheme and host.
3. The new device sends `POST /api/pair {code, kind, name}` (public). The code is matched by its hash
   (any case, spaces and dashes ignored), checked unexpired and unused, and spent in the same transaction
   that creates the device and its token. The device gets the pairing's kind, room and role, and the
   pairing's name, else the one it sent.
4. The page that showed the code polls `GET /api/devices/pairings/{id}` until it is `paired` (with the
   device) or `expired`; closing it early cancels the code (`DELETE`).

A user may have 10 pairings waiting at once (429 `too_many_pairings` beyond). Spent and expired pairings are
deleted an hour after they expire, the next time their user starts one.

**Wrong codes** are limited (`ratelimit.PAIRING_FAILURES`): 10 per client and 200 in all per 10 minutes; past
either, every attempt (a right code too) gets 429 `rate_limited` with `Retry-After`. The client is the last
`X-Forwarded-For` entry (what the proxy in front saw), else the peer address. The overall limit bounds
guessing from many addresses (or forged `X-Forwarded-For` values on a server reached without a proxy); it
pauses pairing for everyone while it lasts. A code for another kind of device (409 `wrong_kind`) is not
counted and stays usable. Neither codes nor tokens are logged; a failure logs the client key only.

**From server code**: `devices.start_pairing(user, kind, by=<who starts it>, name=, room=, role=)` → a
`Pairing` with the raw `code`, `id` and `expires_at` (raises `PermissionError` for someone else without
`manage_users`, `ValueError(field, why)` for a bad field); build the QR string with
`devices_api.pair_uri(public_url.base_url(request), code)`. `devices.create(user, kind, name, room, role)`
makes a device and its token directly (the setup page's browser, tests). `devices.redeem(code, kind=, name=)`
is what `/api/pair` runs.

**The phone and the watch** (android `docs/phone.md` → Pairing, `docs/wear.md` → Pairing): the phone app
pairs with the QR code's `dana://pair` link (a camera app opens it in Dana) or the typed address and code,
as kind `phone`; Settings → Unpair sends `DELETE /api/devices/{id}` with its own token. The watch gets a code
from the phone, which starts a pairing of kind `watch` with its own token, and redeems it itself, so it is a
device of the same user. A paired phone's push registration (`POST /api/phone/register`) is tied to its device
by its token.

**On a tablet or a TV**: the kiosk and TV apps open on a pairing screen until they are paired: the code (the
TV has an on-screen keypad for the D-pad) and the server address, which fills itself from discovery (below).
They send `POST /api/pair {code, kind: "tablet" | "tv", name: <manufacturer and model>}` and take the room
and role from the pairing; their settings show the device (read back from `GET /api/devices`, the `current`
one) and **Unpair** (`DELETE /api/devices/{id}` with the device's own token). A tablet whose dashboard lands
on `/login` (its device was removed) forgets the pairing and asks again (`android/docs/kiosk.md`, `tv.md`).
What a tablet or TV shows, and who may change it, is its dashboard (`docs/dashboard.md`; the web's Devices
page edits it).

**In a browser**: `/login` accepts a pairing code of kind `browser` as well as a token: the browser becomes
a device of the code's user (named from its User-Agent, "Firefox on Linux") and gets the session cookie of
its device token. A code of another kind is refused (`error=kind`) and stays usable; wrong codes count
towards the same limit.

## Finding the server on the home network

The apps (phone Settings, the kiosk and TV pairing screens) browse mDNS / DNS-SD for **`_dana._tcp`** with
Android's `NsdManager` (`android/app/src/main/.../data/discovery/`) and offer what they find as the server
address: one server fills the field, several are listed to pick from. A service stands for
`http://<host>:<port><path>` (`https` when the port is 443); its TXT record carries `path` (default `/`) and
`version` (shown next to the name). Release builds reach a plain-`http` address only when the build allows
cleartext (`android/docs/release.md` → HTTPS).

The server does not advertise itself (no mDNS library in its dependencies). Advertise it from the host or a
sidecar on the host network, e.g. Avahi with a service file:

```xml
<service-group>
  <name>Dana</name>
  <service><type>_dana._tcp</type><port>8080</port>
    <txt-record>path=/</txt-record><txt-record>version=10.0.0</txt-record></service>
</service-group>
```

The address in QR codes and the setup link stays `core/public_url.py`'s (above); discovery only helps a device
that types its code.

## API

| Method and path | Who | Body → answer |
|---|---|---|
| `POST /api/devices/pairings` | signed in; `user_id` of someone else: `manage_users` | `{kind, name?, room?, role?, user_id?}` → 201 `{id, code, expires_at, qr, kind, name, room, role, user: {id, name}}` |
| `GET /api/devices/pairings/{id}` | the one who started it | → `{id, status: pending \| paired \| expired, expires_at, device?}` |
| `DELETE /api/devices/pairings/{id}` | the one who started it | → `{ok}` (404 when none waits) |
| `POST /api/pair` | public, rate-limited | `{code, kind, name?}` → `{token, device, user: {id, name}, server: {version}}`; 403 `invalid_code`, 409 `wrong_kind` `{expected}`, 400 `invalid` (kind), 429 |
| `GET /api/devices` | signed in | → `{devices: [device + current], unpaired_tokens: [{id, user_id, label, created_at, last_used_at, current}]}` (theirs; `unpaired_tokens`: tokens of no device, never the token or its hash); `?user=<id>` / `?user=all` with `manage_users` (each with `user_name`) |
| `PATCH /api/devices/{id}` | its user, or `manage_users` | `{name?, room?, role?}` → `{device}` |
| `DELETE /api/devices/{id}` | its user, or `manage_users` | → `{ok}`; its token stops working at once |
| `DELETE /api/devices/tokens/{id}` | its user, or `manage_users` | → `{ok}`; a token of no device stops working at once (404 for a device's token: remove the device) |

A device in an answer: `{id, user_id, name, kind, room, role, paired, created_at, last_seen_at}` (never a
token or token id). Someone else's device answers 404 like an unknown one. A field that is not valid: 400
`{"error": "invalid", "field", "detail"}`.

Example:

```
POST /api/devices/pairings {"kind": "tablet", "role": "kitchen", "room": "Kitchen", "user_id": "a1b2c3d4e5f6"}
→ 201 {"id": "9f8e7d6c5b4a", "code": "K7QM4XPZ", "expires_at": "2026-11-22T12:05:00+00:00",
       "qr": "dana://pair?server=https%3A%2F%2Fdana.example.com&code=K7QM4XPZ", "kind": "tablet",
       "name": "", "room": "Kitchen", "role": "kitchen", "user": {"id": "a1b2c3d4e5f6", "name": "Gal"}}
POST /api/pair {"code": "k7qm-4xpz", "kind": "tablet", "name": "Lenovo Tab"}
→ 200 {"token": "<shown once>", "device": {"id": "…", "user_id": "a1b2c3d4e5f6", "name": "Lenovo Tab",
       "kind": "tablet", "room": "Kitchen", "role": "kitchen", "paired": true, …},
       "user": {"id": "a1b2c3d4e5f6", "name": "Gal"}, "server": {"version": "10.0.0"}}
```

## The Devices page

A core screen (`devices`, in the navigation's core group and a Settings tile; `/#devices`): the user's devices
with the kind's icon, name, room / role, when last seen ("This device" for the one in use), rename (and room
and role for a tablet or TV) and remove with a confirmation (removing the device in use signs it out). "Pair
a device" asks the kind (and for a tablet or TV, who it is for and the room), then shows the QR code (the
vendored qrcode-generator, `static/vendor/qrcode.js`, drawn by `DanaDevicesCore.qrSvg`, which the Household
screen's invites reuse; dark on white in both themes) and the code in two groups
of four with a five-minute countdown, polls every 2 s and says when the device has paired (or that the code
expired, with "New code"). A browser gets no QR: it types the code on `/login`. Below the devices,
**Signed in without pairing** lists the user's tokens of no device (label, made, last used; "This browser"
for the one in use) with Revoke and the hint to pair the device first (Tokens without a device, above).

## Setup mode

When the database has no users, `API_TOKEN` is unset and `DANA_INSECURE_DEV` is not `1`
(`setup.required()`), `main.py` skips creating the first user from env, and `setup.begin(base)` draws a
one-time setup code (random, kept in memory only as its hash; a restart draws a new one). The start logs one
line: `Dana is not set up yet. Open <PUBLIC_URL or http://localhost:<PORT>>/setup?code=<code> to create the
first admin.` While setup is required, the server answers only `/setup`, `/api/setup`, `/api/setup/admin`,
`/api/health`, `/static/*`, `/sw.js` and `/manifest.json`; everything else is 503 `{"error":
"setup_required"}` (a page for a browser opening one, saying where the link is). The server listens on every
interface: the code is what counts.

- `GET /api/setup` (public) → `{setup_required, version}`.
- `POST /api/setup/admin {code, name}` (public; wrong codes limited per client like pairing codes,
  `ratelimit.SETUP_FAILURES`) checks the code in constant time, creates the first admin (the `OWNER_*`
  profile values apply when set), gives them their default addons once, and makes the browser their first
  device (kind `browser`): 201 `{user: {id, name, access}, device, token}` with the session cookie of that
  token set, so the browser is signed in. The code is spent and setup mode ends (a user exists): a second
  call gets 409 `already_set_up`; a wrong code 403 `invalid_code`; no name 400 `invalid` (`field: name`).

With `API_TOKEN` set (a headless install) the first start creates the admin from `OWNER_*` as before and there
is no setup mode; `DANA_INSECURE_DEV=1` (local development) does the same.

**The setup page** (`static/setup.html`, `static/setup.js`): `/setup` is open to anyone during setup mode and
an ordinary signed-in page afterwards. `setup.js` takes the code out of the address bar, asks `GET /api/setup`
and runs `STEPS` in order: each step is `{id, kicker, title, needed(state), render(el, state, done)}`; a step
whose `needed(state)` is false is skipped, `render` draws it and calls `done(changes)` (merged into `state`),
and after the last step the wizard is finished and the app opens (`/`). The first step is `admin` (name →
create, setup mode only); the wizard's steps follow.

## The wizard

After setup mode creates the first admin, and on every new user's first sign-in, the web app sends the user to
`/setup` while their wizard is not finished (`static/app.js` asks `GET /api/wizard`). Users who existed before the
wizard have it finished (migration `m0006_zones_wizard`). Each step has Skip; what a user finished or skipped is
stored per user (`user_wizard.steps`), so a reload or a later sign-in picks up at the next step. Settings → Setup
wizard (`/setup?again=1`) shows every step again.

| Step | Who | What it does |
|---|---|---|
| `you` | everyone | name, pronoun, languages → `PUT /api/profile` |
| `home` | everyone | a place search or "Use my location" (`static/zones.js`'s place picker, `docs/zones.md`) → `PUT /api/wizard/home`: their Home zone; the place's timezone becomes the server's when the user has `manage_server` (stored in `server_settings`, `core/clock.py`; `TIMEZONE` in env still wins), the country's currency becomes theirs when they chose none |
| `model` | `manage_server` | the LLM provider and its key, tested on the spot (`static/llm.js`, `PUT /api/llm`; `docs/configuration.md` → Models) |
| `phone` | everyone | a pairing code and QR for a phone (`POST /api/devices/pairings`, drawn with `DanaDevicesCore` from the Devices page); done when the phone pairs |
| `addons` | everyone | the addons the server suggests (`GET /api/addons` → `welcome.suggested`) that are off and free to turn on now (no required account, hardware, key or credential), each turned on with `PUT /api/addons/{name}` |
| `sample` | when the server has `POST /api/demo` | "Show me with sample data": calls it |

A step that is not for the user (`model` without `manage_server`, `sample` without the demo route, found in the app's
router at request time) is left out (`available: false`).

| Method and path | Body → answer |
|---|---|
| `GET /api/wizard` | → `<state>` = `{steps: [{id, status: pending \| done \| skipped, available}], finished, finished_at, next, demo, timezone}` (`next`: the first available pending step, null when none) |
| `POST /api/wizard/steps/{step}` | `{status: done \| skipped}` → `<state>`; 404 unknown step, 400 `unavailable` |
| `POST /api/wizard/finish` | → `<state>` (steps not reached stay pending) |
| `PUT /api/wizard/home` | `{latitude, longitude, radius_m?, timezone?, currency?}` → `{zone, timezone: {value, applied}, currency, state}`; marks `home` done |

Every signed-in user, for themselves only.
