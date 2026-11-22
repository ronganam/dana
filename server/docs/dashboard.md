# Dashboards (tablets and TVs)

A tablet or a TV shows a **dashboard**: widgets that addons declare, laid out from a **preset** (Kitchen,
Bedroom, Hall, Living room) plus the edits made on the web (Devices → the device's dashboard button). The
tablet runs the Android `kiosk` flavor and the TV its `tv` flavor (`android/docs/kiosk.md`, `android/docs/tv.md`).

Code: `core/dashboard.py` (presets, layouts, who reads what, the payload), `core/dashboard_api.py` (the page and
the API), the table `dashboards` (`core/db/migrations/`), the page `static/dashboard.{html,css,js}`, the editor
`static/dashboard-editor.js` (on the Devices page, `static/devices.js`), the widget hook `@addon.widget`
(`core/addons/addon.py`, `docs/addon-reference.md`) and the renderers' SDK `Dana.widget` (`static/dana.js`).
Tests: `tests/test_dashboard.py`, `tests/test_web_dashboard_editor.mjs`; the payloads clients read are
captured in `tests/fixtures/dashboard_web.json` (`WRITE_WEB_FIXTURE=1 bin/test test_dashboard`).

## Widgets

A widget is an addon's: `@addon.widget(key, title=, script=, sizes=, settings=, description=, icon=, refresh=)`
on a function `f(user, ctx: WidgetContext) -> dict` (or async) giving its data for a user, and a script in the
addon's `web/` folder that draws it with `Dana.widget(key, {mount, update?, unmount?, style?, bare?})`
(`docs/writing-an-addon.md`, "Dashboard widgets"). `ctx` holds the placed widget's `settings` (every setting
it declares, defaults filled in), the `size` it is drawn at and `now`. Like every hook it runs only for a user
who has the addon: on the dashboard of one who does not it answers `not_connected` (and the editor does not
offer it). Sizes: `small` (one cell), `medium` (one column, two rows), `large` (two columns, three rows) of a
four-column grid (two on a narrow screen), filled in order, gaps back-filled.

| Widget | Addon | Data | Settings |
|---|---|---|---|
| `clock` Clock and weather | essentials | `weather` from the highest-ranked `weather` provider that answers (`core.briefing.weather`: Open-Meteo at the Home zone, then Home Assistant), or null; the time is the page's own clock | |
| `now` Now | essentials | the top of the user's Now screen: `lede`, `total`, `items` (3 / 5 / 8 by size) | |
| `shopping` Shopping list | shopping | the active list: `list`, `to_buy`, `items` (6 / 12 / 30); live: the script follows `/api/shopping/events` | |
| `calendar` Calendar | calendar | `days`: today and tomorrow with their events (the user's Google Calendar) | |
| `reminders` Reminders | tasks | due today and overdue, high priority, today's timed reminders, and before noon today's briefing (`headline`, three `points`) | |
| `cameras` Camera | cameras | `camera`, `webrtc` (`/api/frigate/webrtc/<camera>`), at the large size the latest 3 events | `camera` (a Frigate camera name) |
| `ha_climate` Climate | homeassistant | an AC / thermostat: modes, fan modes, current / target temperature, humidity; controls call `POST /api/ha/service` | `entity` (empty: the first `climate.*`) |
| `ha_lights` Lights | homeassistant | lights on / off and brightness, a tap toggles | `entities` (empty: every light, up to 12) |
| `ha_entities` Home Assistant | homeassistant | the chosen entities' states; switches toggle | `entities` |
| `ha_batteries` Batteries | homeassistant | battery levels | `sensors` (empty: the phone battery sensor of the Home Assistant settings) |
| `money` Money | money | this month so far: `spent`, `typical`, `on_track`, the forecast's `balance` / `low` | |

Entity and camera settings are text a person types on the editor (entity ids separated by commas); a
deployment's own ids are never in the presets.

## Presets

| | Kitchen | Bedroom | Hall | Living room |
|---|---|---|---|---|
| Clock and weather | medium | medium | medium | medium |
| Now | medium | | medium | medium |
| Shopping list | large | | | |
| Calendar | medium | | medium | medium |
| Reminders | medium | medium | medium | |
| Camera | | large | medium | medium |
| Home Assistant | Lights (small) | Climate (medium), Batteries (small) | | Lights (medium) |
| Money | | | | medium |

The order is the table's (Bedroom: clock, camera, reminders, climate, batteries). A device starts from the preset of
its household role (`devices.role`: kitchen, bedroom, hall, living_room), else a tablet from Kitchen and a TV from
Living room. Saving a dashboard stores the chosen preset and, once edited, the whole widget list
(`dashboards.widgets`, JSON: `[{id, widget, size, settings}]`; null = the preset's own). Each placed widget has an
id (`<widget>-<n>`) kept across edits. A saved widget whose addon its owner no longer has stays listed (it shows
`not_connected`); only widgets of an enabled addon can be saved. At most 24 widgets; setting values up to 300
characters. Removing the device removes its dashboard.

## Who reads and edits a dashboard

- **The device itself** (a tablet or TV token, or the session cookie it got from it at `POST /login`) reads only
  its own dashboard; `device` may be left out. It cannot change it (403 `forbidden`).
- **A user who may manage the device** (`devices.may_manage`: its owner, or an admin with `manage_users`) reads
  and edits it from any other credential.
- Anyone else, and any id that is not a tablet or TV, gets 404 `not_found`, as for a missing id.
- The **data** is the device owner's (the user who paired it; a household tablet shows that user's tasks, list,
  Home Assistant…), read when the device itself or its owner asks. An admin managing someone else's device gets
  the layout with every widget's data withheld (`"error": "private"`).

## API

- `GET /dashboard[?device=<id>]` — the page (no-cache; its own CSS / JS and `dana.js` carry `?v=<web release>`, so a
  kiosk WebView picks up each release). Without `device` it shows the requesting tablet / TV's dashboard, else a
  list of the user's tablets and TVs. An id the caller may not read: 404.
- `GET /api/dashboard[?device=<id>]` — the layout and every widget's data:

  ```json
  {"device": {"id": "…", "name": "Bedroom tablet", "kind": "tablet", "role": "bedroom", "room": ""},
   "preset": "bedroom", "edited": false, "release": "…", "time": "2026-11-22T08:00:00+00:00",
   "widgets": [{"id": "clock-1", "widget": "clock", "size": "medium", "settings": {}, "addon": "essentials",
                "title": "Clock and weather", "script": "/addons/essentials/web/widgets.js?v=…", "refresh": 900,
                "data": {"weather": {"condition": "sunny", "temperature": 27, "unit": "°C"}}},
               {"id": "cameras-1", "widget": "cameras", "size": "large", "settings": {"camera": ""},
                "addon": "cameras", "…": "…", "data": null, "error": "not_connected"}]}
  ```

  `error` (with `data` null): `not_connected` (its addon is not the owner's), `unavailable` (no enabled addon
  declares it), `private` (see above), `failed` (it raised or took over 8 s). Without `device` from anything but a
  tablet / TV: 400 `device_required`.
- `GET /api/dashboard/widget?device=<id>&id=<widget id>` — `{id, data[, error]}` of one widget (its `refresh`
  timer); 403 `not_connected` for an addon the owner lacks, 404 for an id not on the dashboard.
- `GET /api/dashboard/catalog?device=<id>` — `{presets: [{key, title, widgets}], widgets: [{key, addon, title,
  description, icon, sizes, refresh, settings: [{name, label, help, placeholder, default}]}], sizes,
  default_preset}`: what the editor offers (the owner's widgets).
- `PUT /api/dashboard?device=<id>` `{preset, widgets: [{id?, widget, size?, settings?}] | null}` — choose a preset
  (`widgets` null) or save the edited list; answers the layout (no data). 400 `invalid` names what is wrong.

## The page

`static/dashboard.js` loads each widget's script once, draws the widgets in order (a title line unless the
renderer is `bare`), reads each widget again every `refresh` seconds and the whole layout every 5 minutes and
when the page becomes visible (an edit made on the web reaches the wall). A widget renderer's `api` adds to a
page's: `size`, `settings`, `refresh()`, `night()`, `kiosk` (`window.DanaKiosk` or null) and `setValue(el, text)`
(the only motion: a changed value crossfades).

- **Zoom**: `?zoom=` (20-300); the kiosk loads `/dashboard?zoom=98`.
- **Night mode**: inside the kiosk app (`window.DanaKiosk`) the tablet's light sensor (`DanaKiosk.lux()`, polled
  every 10 s, dark below `?nightlux=5` lx, bright again at 3×, 2 readings in a row) decides, and night dims the real
  backlight (`DanaKiosk.setBacklight(?backlight=6)`; by day `setBacklight(-1)`, the system's adaptive brightness).
  Elsewhere, or without a reading, the 22-7 schedule decides and a black overlay dims (`?nightdim=0.55`).
  `?night=23-6` forces a schedule, `?night=off` turns it off. At night the clock drops its seconds, the layout
  drifts a few pixels (burn-in) and a tap restores full brightness for 2 minutes.
- **Ask Dana** (`#ask-dana`, kiosk only) calls `DanaKiosk.openAssistant()`.
- **Look**: `docs/web-design.md`, "Dashboards".

## Cameras

The `cameras` widget plays a camera of the owner's Frigate through go2rtc WebRTC (`/api/frigate/webrtc/{camera}`)
**over TCP** (the page keeps only the answer's TCP ICE candidates, go2rtc's passive LAN address on port 8555;
`?camtcp=0` turns this off): a tablet's Wi-Fi loses packets that go2rtc does not resend, and a 1440p keyframe is
~420 packets, so over UDP nothing decoded. A watchdog reconnects a frozen stream with backoff. One finger drags,
two pinch, the wheel zooms at the cursor, a double tap resets; the pan always keeps the picture covering the frame.
At the large size a rail beside it shows the latest 3 events (refreshed with the widget); a tap plays the clip
full-screen.

**Per user** (`addons/cameras/`: `config` the setting, `client` the Frigate logic: events, previews / clips,
go2rtc WebRTC; handlers in `api.py`). Every `/api/frigate/*` endpoint (WebRTC signaling and the MJPEG stream
included) and `/api/camera/{entity}/proxy` work for the caller's own `cameras` addon: its `url` setting is their
Frigate; without it they answer 403 `{"error": "not_connected", "addon": "cameras"}` before anything else. The two
that go through Home Assistant (`/api/camera/{entity}/proxy`, the MJPEG `/api/frigate/{camera}`) need the caller's
`homeassistant` too (checked first). Events / clip caches are per connection (the clip LRU keeps 4 clips in all).
Upstream paths are fixed templates: camera names match `[A-Za-z0-9_-]{1,64}`, event ids `[A-Za-z0-9._-]{1,64}` and
not only dots, motion windows are integers ≤ 60 s, and the WebRTC stream name is sent as an encoded `src` query
parameter.

**Events**: non-audio Frigate object events, plus optional **plain motion** cards (off unless
`FRIGATE_MOTION_THRESHOLD` > 0, a server-wide env value). Motion is camera-wide: the server reads
`/api/review/activity/motion` (30 s buckets, % of frame moving, last 24 h), joins buckets above the threshold into
segments and drops any within 10 s of an object event; a motion card's GIF and clip cover the segment's first
minute (`/api/frigate/motion/{camera}/{start}/{end}/preview.gif|clip.mp4`). Each item of
`GET /api/frigate/events/{camera}` carries `type` (`object` / `motion`), `preview_url` and `clip_url`; object media
is `/api/frigate/event/{id}/preview.gif|clip.mp4`.

## The kiosk and the TV

- **Kiosk** (`io.github.ronganam.dana.kiosk`): pairs as a `tablet` (its role picks the preset), exchanges its
  device token for the session cookie at `POST /login`, and loads `/dashboard?zoom=98`: with that cookie the page
  shows the tablet's own dashboard, no `device` needed. It listens for "Hey Dana" and opens the native assistant
  sheet over the page (`/api/assistant/voice_stream`).
- **TV** ("Dana TV"): can load the same page (`/dashboard`, its own cookie) or read `GET /api/dashboard` with its
  Bearer device token. Its own home screen keeps using `GET /api/ha/states` + `POST /api/ha/service` for Home
  Assistant tiles and `/api/camera/{entity}/proxy` for snapshots; camera picture-in-picture plays go2rtc RTSP
  directly (`rtsp://<frigate host>:8554/<camera>`).
- **Home Assistant endpoints** (`/api/ha/state/{entity}`, `/api/ha/states`, `POST /api/ha/service`,
  `/api/client-config`, `/api/camera/{entity}/proxy`, the MJPEG `/api/frigate/{camera}`) each go to the caller's own
  Home Assistant; without the addon: 403 `{"error": "not_connected", "addon": "homeassistant"}`.
  `/api/client-config` returns the caller's `notify_service`, `interactive_sensor` and `person` for the phone app.
