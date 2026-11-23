# Zones

A **zone** is one of a user's places: a name, a centre (latitude, longitude) and a radius in metres. Location
reminders name a zone ("remind me when I arrive at the Gym"); "at home" is the user's Home zone. Zones are Dana's own,
per user: the phone app's geofences tell Dana when the user arrives or leaves (`docs/signals.md`). Home Assistant's
zones can be copied in once (below); Home Assistant does not report arrivals.

Code: `core/zones.py` (the zones), `core/zones_api.py` (HTTP), `core/places.py` (the place search, a country's
currency); web: `static/zones.js` + `zones.css` (the Settings → Zones card and the place picker the wizard shares).
Table: core migration `m0006_zones_wizard`. Tests: `bin/test test_zones`, `bin/test test_web_zones`; the import is in
`test_homeassistant_users`, location reminders on zones in `test_tasks_users`, the phone's zone events and
`zones_changed` in `test_signals`.

## A zone

| Field | Meaning |
|---|---|
| `id` | 12 hex chars |
| `user_id` | whose zone it is (never in an answer) |
| `name` | one line, at most 60 characters; unique per user in any case ("gym" and "Gym" are one name) |
| `latitude`, `longitude` | the centre, -90…90 and -180…180 |
| `radius_m` | 10…50 000 metres, default 100 |
| `is_home` | the user's Home; at most one per user (making another zone Home unmarks the old one) |
| `created_at` | ISO UTC |

Everything is per user: a user sees and changes only their own zones, and another user's zone id answers 404 like an
unknown one. The same name may exist for two users.

## API

| Method and path | Body → answer |
|---|---|
| `GET /api/zones` | → `[{name, entity_id, id, latitude, longitude, radius_m, is_home, created_at}]`, Home first, then by name |
| `POST /api/zones` | `{name, latitude, longitude, radius_m?, is_home?}` → 201 `{zone}` |
| `PATCH /api/zones/{id}` | any of the fields → `{zone}` |
| `DELETE /api/zones/{id}` | → `{ok}` |
| `PUT /api/zones/home` | `{latitude, longitude, radius_m?}` → `{zone}`: the Home zone moves there (or a zone called Home is made and marked Home) |
| `GET /api/places?q=` | → `{places: [{name, label, latitude, longitude, timezone, country_code, currency}]}` (below) |
| `POST /api/ha/zones/import` | (the `homeassistant` addon's route) → `{added: [zone], skipped: [name]}`; 502 `unreachable` when Home Assistant fails; 403 `not_connected` without the addon |

Every signed-in user may use them. A field that is not valid, or a name the user already has: 400 `{"error":
"invalid", "field", "detail"}`. `GET /api/zones` starts each zone with `name` and `entity_id` (`zone.home` for
Home, else `zone.<name in lower case, non-letters as _>`): that is what the reminder editors read (the web's
`addons/tasks/web/tasks-core.js`, the app's `Zones.kt`, which ignore the other fields).

## Location reminders

The tasks addon checks a location trigger against the user's zones (`addons/tasks/service.py::resolve_zone`): the
name in any case, "home" meaning their Home zone whatever it is called. An unknown name is refused with the list of
their zones (HTTP 400 `{"error": "'Moon' is not one of your zones. Your zones: Home, Gym."}`, the tool's error in
chat); with no zones at all the message says to add them in Settings → Zones. The agent lists them with the tasks
tool `get_zones`. The trigger fires on the user's `location` signal (`docs/signals.md`): the phone app's geofences on these zones
(`{"type": "zone", "action": "arrive" | "leave", "zone_id"}` to `POST /api/signals`: the zone's name is used, an id
that is not one of the caller's zones is refused).

## The phone's geofences

Every change of a user's zones (create, update, delete, a new Home, an import that added any) emits
`core.zones.changed` for them (`core/zones.py`); the phone addon sends their phone a `zones_changed` push event
(`{changed_at}`, replacing an earlier one; `docs/notifications.md`), and the app reads `GET /api/zones` again and
registers its geofences. A user with no phone registered for push gets no event: the app reads the zones when it
starts. The server never learns where the phone is, only "arrived at Home at 18:02".

## Import from Home Assistant

`POST /api/ha/zones/import` reads the caller's Home Assistant zones (`zone.*` states with a name and a position) and
copies each into their zones once: the name, the centre, the radius (`radius`, default 100), and `zone.home` as their
Home while they have none. A name they already have is skipped (their zone stays as it is), so importing again adds
only new ones. Nothing is kept in sync afterwards.

## Place search

`core/places.py::search` asks Open-Meteo's free geocoding service (no key; one HTTPS GET with the typed name and the
user's first language) for towns and places matching a name, each with its timezone, country and that country's
currency (`places.CURRENCIES`, the common ones). `GEOCODING_URL` points it elsewhere; empty turns it off. It never
raises: a failure, a timeout (6 s) or the service turned off answers `[]`, and the editors still have typed
coordinates and "Use my location" (the browser's position). Only what the user picks is stored.

## The web

Settings → **Zones** (`static/zones.js`): the list (Home marked, the radius and centre under each name), Add / Edit
in a dialog (name, a place search or "Use my location" that fills latitude and longitude, the radius, "This is my
Home"), Remove with a confirmation, and "Import from Home Assistant" for users with the addon on. The tasks page's
location picker offers the zones and, without any, a hint to add them here. The wizard's Home step uses the same place
picker (`DanaPlacePicker`, `docs/devices.md` → The wizard).
