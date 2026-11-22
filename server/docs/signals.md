# Signals

A **signal** is one fact about a user that Dana reacts to without being asked: `wake` (they are up), `screen` (their
phone's screen was on), `car` (they got in or out of the car), `location` (they arrived at or left one of their
zones), `sleep` (a night's sleep ended), `weather` (today's weather where they live). Wake briefings, wake / car /
place reminders and the weather in the briefing and on Now come from them.

Each signal has **sources**, ranked per user. Dana's phone app (`phone`) comes first, Home Assistant
(`homeassistant`) next when the user has it connected; for weather, Open-Meteo (`open_meteo`, everyone's, no key)
before Home Assistant's weather entity. The consumers (the tasks addon, the wake flow, the briefing, Now) take the
signal and never ask where it came from. A household with no Home Assistant gets all of it from the phone.

Code: `core/signals.py` (sources, ranking, delivery), `core/signals_api.py` (`GET /api/signals`),
`core/wake.py` + `core/wake_window.py` (the wake flow and window, `docs/people.md`), `addons/phone/signals.py` (the
phone's events and screen sessions; `POST /api/signals` in `addons/phone/api.py`), `addons/essentials/weather.py`
(Open-Meteo), `addons/homeassistant/hooks.py` (Home Assistant's sources). Tables: `signal_sources`, `wake_windows`
(core migration `m0008_signals`), `phone_screen_sessions`, `phone_signal_keys` (phone migration `m0004_signals`).
Tests: `bin/test test_signals`; the payload is captured in `tests/fixtures/signals_web.json` (`WRITE_WEB_FIXTURE=1
bin/test test_signals`), which the Android contract test reads.

## Sources and ranking

A source is an addon that declares `@addon.provide("signal.<name>", rank=, source=, label=)`: a plain function
`f(user) -> str` saying what is missing before it can deliver the signal for that user ("" when nothing). A user has
only the sources of the addons active for them.

| Signal | Sources (rank) | Set up when |
|---|---|---|
| `wake`, `screen`, `car`, `location`, `sleep` | `phone` "Dana's phone app" (10) | the user has a paired phone (`docs/devices.md`); `sleep` has no other source |
| `wake`, `screen` | `homeassistant` (50) | their `phone_interactive_sensor` setting is set |
| `car` | `homeassistant` (50) | `car_sensor` and `car_device` are set |
| `location` | `homeassistant` (50) | `person` is set |
| `weather` | `open_meteo` "Open-Meteo" (10, the essentials addon) | they have a Home zone (`docs/zones.md`) |
| `weather` | `homeassistant` (50) | `weather_entity` is set |

Every delivery is recorded as the user's `signal_sources` row for that signal and source: when it happened and a short
word ("unlock", "arrive Home", "enter (bluetooth)", "rainy"). Only the last one is kept: never a track. A source is
**recent** when it delivered within `SIGNAL_ACTIVE_HOURS` (72 h). The **active** source of a signal is the
highest-ranked recent one, else the highest-ranked one that is set up.

**Event signals** (`wake`, `car`, `location`, `sleep`) go through `signals.deliver(user, signal, source, ...)`: an
event from a source is ignored while a higher-ranked source is recent for that user (it is still recorded and shown on
the overview). So Home Assistant and the phone never both fire a reminder: while the phone delivers places, Home
Assistant's zone changes are only noted; when the phone has been quiet for 72 h (the app was removed, location access
was taken away), Home Assistant's are used again. The window is per signal: the phone can be the car source while
Home Assistant still delivers places. The trigger engine's own refire / flap marks (`docs/tasks-and-triggers.md`)
drop the odd duplicate at the hand-over.

An accepted `car` / `location` event is emitted as `core.signal.car` `{action: enter|leave, source, late}` /
`core.signal.location` `{action: arrive|leave, target: <zone name>, source, late}`; the tasks addon fires the
reminders. `late`: the event happened more than `SIGNAL_LATE_MINUTES` (30) before it arrived (the phone was offline);
the transition is recorded for the refire logic, but no reminder goes out. `wake` and `sleep` are the wake flow's
(`docs/people.md`, "Wake detection").

**Data signals** are read through ranked providers (`@addon.provide(key, rank=)`, `registry.answer(key, user, ...)`
asks each in rank order and takes the first that answers, anything but None): `screen_sessions` (the phone's stored
sessions, None while the phone has sent none for 72 h; then Home Assistant's screen sensor history) and `weather`
(Open-Meteo, then Home Assistant). The answering source is recorded as that signal's.

## POST /api/signals (the phone addon's route)

The phone app's events, for the caller (a device token or a user token; the user must have the `phone` addon, else
403 `not_connected`):

```json
{"events": [
  {"type": "unlock", "at": 1795400000000},
  {"type": "screen", "start": 1795400000000, "end": 1795400090000},
  {"type": "car",    "action": "enter", "at": 1795400000000, "via": "bluetooth"},
  {"type": "zone",   "action": "arrive", "zone_id": "<zones.id>", "at": 1795400000000},
  {"type": "sleep",  "start": 1795370000000, "end": 1795398000000, "via": "health_connect"}
]}
```

→ `{"accepted": 4, "rejected": [{"index": 3, "error": "unknown_zone"}]}`

- Times are epoch milliseconds. `car.action` `enter` | `leave`, `car.via` `bluetooth` | `android_auto` | `activity`;
  `zone.action` `arrive` | `leave`; `sleep.via` `health_connect`. At most 200 events per call, oldest first; a body
  that is not `{"events": [...]}` (or more than 200) → 400 `{"error": "invalid", "detail"}`.
- Per event: `bad_event` (a missing or invalid field, an end before its start, a span over 24 h, a time more than
  10 min ahead), `unknown_zone` (not one of the caller's zones: another user's zone id answers the same), `stale`
  (older than 48 h, by its `at` or `end`; dropped).
- The app sends a whole batch again when a call fails: each applied event is remembered by a hash of its type, action,
  zone and times (`phone_signal_keys`, 48 h), so a repeat is accepted without effect.
- Effects: `unlock` → the wake flow (and the wake window's learning); `screen` → a screen session
  (`phone_screen_sessions`, kept 3 days; the same start again keeps the longer end); `car` → the `car` signal;
  `zone` → the `location` signal with that zone's name; `sleep` → a sleep ending in today's wake window is the wake,
  at that exact time.

## GET /api/signals (core)

The caller's own overview, for the phone's Signals page and the web:

```json
{"signals": [
  {"signal": "wake", "sources": [
     {"source": "phone", "label": "Dana's phone app", "active": true,
      "last_at": "2026-11-22T07:12:03+02:00", "detail": "unlock"},
     {"source": "homeassistant", "label": "Home Assistant", "active": false,
      "last_at": null, "detail": ""}]},
  {"signal": "screen", "sources": [...]}, {"signal": "car", "sources": [...]},
  {"signal": "location", "sources": [...]}, {"signal": "sleep", "sources": [...]},
  {"signal": "weather", "sources": [{"source": "open_meteo", "label": "Open-Meteo", ...}, ...]}],
 "wake_window": {"start": "05:00", "end": "12:00", "learned": false}}
```

Every signal, always in that order; `sources` only those this user can have (Home Assistant only with the addon
connected), highest rank first; `active` the source Dana takes the signal from now; `last_at` the last delivery in the
server's zone (null: never); `detail` the last delivery's word, or what the source still needs ("Pair Dana's phone app
(Settings → Devices)", "Set your Home zone (Settings → Zones)") when it never delivered. Clients ignore unknown keys.

The wake window (`core/wake_window.py`) is also in `GET /api/profile`, and set with `PUT /api/profile {"wake_window":
{"start": "HH:MM", "end": "HH:MM"}}` (start before end, same day; else 400 `{"error": "invalid", "field":
"wake_window"}`) or `null` (back to the learned one): `docs/people.md`, "Wake detection".

## Weather from Open-Meteo

The essentials addon (everyone has it) provides `weather` at rank 10: `GET https://api.open-meteo.com/v1/forecast`
with the Home zone's latitude and longitude (rounded to 2 places), `current=temperature_2m,weather_code,is_day`,
`daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max`, `timezone=auto`, `forecast_days=1`. Free,
no key; the addon's Touches names the host. The answer has Home Assistant's shape: `{condition, temperature, unit,
high, low, rain_chance}`, the WMO code mapped to Home Assistant's condition words (`sunny` / `clear night`,
`partlycloudy`, `cloudy`, `fog`, `rainy`, `pouring`, `snowy`, `snowy rainy`, `lightning rainy`, `hail`; anything else
`exceptional`). Cached per place for 30 min (a failure for 5 min). Without a Home zone, or when Open-Meteo fails, Home
Assistant's weather entity answers (when connected). `OPEN_METEO_URL` points at another Open-Meteo server (it can run
on your own). The briefing's `weather` section is the essentials addon's, from whichever provider answers.

## Zones on the phone

The phone registers a geofence per zone (`GET /api/zones`). When a user's zones change, `core/zones.py` emits
`core.zones.changed` and the phone addon pushes `zones_changed` to their phones registered for push (`docs/zones.md`).

## Battery and privacy

- The server only learns transitions: "entered Home at 18:02", "got in the car at 08:10", the screen's on / off
  times, when a night's sleep ended. Never coordinates, never a track; the phone decides arrive / leave on the device.
- What is kept: the last delivery per source and signal, screen sessions for 3 days, event hashes for 48 h, a week of
  first-unlock times while the wake window is learned.
- The phone batches events and sends them when it is online; nothing needs it to poll.

## The self-check

`signals` (core, `core/checks.py`) for a user with reminders or a phone channel: ok when the wake, car and place
signals each have a source that is set up or delivered recently; otherwise it says plainly what Dana can't tell ("Dana
can't tell when you wake up, drive or arrive somewhere") and how to fix it (pair the phone app, or connect Home
Assistant). The facts list the active source of each.
