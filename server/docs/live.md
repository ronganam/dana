# Live activities

A **live activity** is something ongoing that an add-on describes in plain terms (a watched train, a coding-agent session, a watering timer) and Dana keeps current on the user's clients: the phone shows it as an Android 16 Live Update (a promoted ongoing notification with a status-bar chip, Samsung's Now Bar), the web shows it in the Live strip on top of Now, and the Omarchy bar shows the soonest one with its countdown. Add-ons never touch Android fields: they say what to show, Dana validates, stores, batches and delivers it.

Code: `core/live.py` (the API, validation, storage, pushes, the loop), `core/live_api.py` (the routes), core migration `m0011_live` (`live_activities`). Tests: `tests/test_live.py`; the wire contract is `tests/fixtures/live_web.json` (generated from `to_json`, read by the Android contract tests, `tests/test_web_live.mjs` and the Omarchy plugin's tests).

## For add-on authors

```python
from core import live

await live.start(user, addon, "watch-12", live.Activity(
    title="Departs 17:46", text="Platform 3", icon="train-front", tone="warn",
    countdown=live.Countdown(departs, "Departs"),
    actions=(live.Action("next", "Next train", kind="next"), live.Action("stop", "Stop", kind="stop")),
    choices=(live.Choice("404", live.Activity(title="Departs 17:46")), live.Choice("406", live.Activity(title="Departs 17:52"))),
    open="/israel_rail", alert="delay:4", ends_at=arrives, refresh_at=departs))
await live.update(user, addon, "watch-12", activity)            # None when it is not running
await live.end(user, addon, "watch-12", live.Final("Arrived", tone="good"))
live.get(user, addon, "watch-12")                                # the running one (a live.Current), or None
live.active(user)                                                # every running one, newest-updated first
```

`addon` is the add-on's `Addon` object; `key` is the add-on's own name for it (1–100 letters, digits, `_ . : @ -`). `start` replaces a running activity with the same key. The two hooks (`server/docs/addon-reference.md` has their full words and the showcase's example):

```python
@addon.live_refresh(every=60)          # seconds, at least 5
async def refresh(user, current: live.Current) -> live.HookResult: ...

@addon.live_action
async def act(user, current: live.Current, action: str, choice: str) -> live.HookResult: ...
```

A hook returns an `Activity` (update it), `live.END` (end it) or None (leave it). `live_refresh` is called per running activity of the add-on when it is due: at the activity's `refresh_at` when it set one later than the last call, else `every` seconds after the last call (or the start). `live_action` gets a `server` action's id (with the choice it was pressed on), `"select"` after Dana stored a new selection (with its key), or `"stop"` after Dana ended the activity (clean your own rows; the answer is ignored). Both run as the activity's user, only while the add-on is active for them; a failing or slow (60 s) one is logged and never ends the activity.

### The activity

| Field | What |
|---|---|
| `title` | required, up to 120 characters |
| `text`, `context` | up to 240 / 120; `context` is the line under (the route, the source) |
| `icon` | one of `live.ICONS` (Lucide names the phone ships as drawables) or "" |
| `tone` | `neutral`, `good`, `warn`, `bad`, `muted` (the colour) |
| `countdown` / `since` | a timer counting down to `to` (with a `label` up to 24), or counting up; not both |
| `chip` | up to 7 characters replacing the status-bar chip's text ("3/7") |
| `progress` | `Progress(segments=(Seg(length, tone), …), points=(Point(at, tone), …), value, tracker, indeterminate, styled_by_progress)`; the total is the sum of the segment lengths (100 without segments); `value` and each point lie within it; `tracker` is an icon riding the bar |
| `actions` | up to 3 `Action(id, label, kind)`: `server` (goes to `live_action`; not called `select` or `stop`), `next` / `prev` (the next / previous choice, handled by the client), `stop` (at most one) |
| `choices` | up to 8 `Choice(key, Activity)`: alternatives (a later train); a choice's display fields override the activity's while it is selected |
| `selected` | the choice to start on ("" = the first) |
| `open` | where a tap goes: an `https://` address, or a Dana path starting with `/` naming a screen (`/<page id>` of an add-on, or a core screen such as `/now`) |
| `alert` | a key; an update alerts (sound, heads-up) only the first time each non-empty value is seen |
| `ends_at` | it expires by itself then (Dana ends it; the phone times it out) |
| `refresh_at` | when `live_refresh` should run next (not sent to clients) |

Times are aware datetimes (`core.clock.now()` based). Anything else, or more than 16 KB as JSON, raises `live.LiveError` with what is wrong.

### What Dana does for you

- **No-op updates are dropped**: an update whose wire form is unchanged bumps nothing and pushes nothing (fields clients never see, such as `refresh_at`, are still stored).
- **Batched pushes**: at most one push per user every 10 s (`LIVE_PUSH_INTERVAL`). A start, an end, a new non-empty `alert` and a selection change go at once; other changes wait and a trailing push carries the latest.
- **Expiry**: an activity whose `ends_at` passed is ended (quietly: no `live_action`).
- **Selection**: the user's choice is stored on the activity and survives updates whose choices still have that key; otherwise it falls back to the activity's `selected`, else the first choice.
- **Stop**: the user's Stop (or a `stop` action, or dismissing the notification) ends it, then `live_action` gets `"stop"`.
- **Finals**: an activity ended with a `Final` stays listed (ended) for 10 minutes so the phone can show the final; one ended without is gone at once.

## For client authors

### API (every signed-in user, their own activities only)

| Route | Answer |
|---|---|
| `GET /api/live` | `{"activities": [<wire>, …]}`, newest-updated first |
| `POST /api/live/{id}/act {"action": "<id>", "choice": "<key>"?}` | `{"activity": <wire> \| null}` (null: it ended); 400 `unknown_action`, 400 `unknown_choice` |
| `POST /api/live/{id}/select {"choice": "<key>"}` | `{"activity": <wire> \| null}`; 400 `unknown_choice` |
| `DELETE /api/live/{id}` | `{"ok": true}`: stops it (an ended one kept for its final is dismissed) |

`{id}` is `<addon>:<key>`. Another user's id answers 404 `not_found` like a missing one; a body that is not a JSON object answers 400 `invalid`. Redraw from the reply at once; the push follows for the user's other devices.

### The push

The same object as `GET /api/live` arrives as the phone event `live` (`addons/phone/push.py`, TTL 6 h, each one replacing the earlier ones). Core sends it with `core.notify.send(user, Message(kind="live", data=…, replace=True))`, so only the phone add-on's `push` channel delivers it, and only to users who have the phone add-on.

### Wire JSON

Each activity: `id`, `addon`, `source` (the add-on's title), `rev` (one more for every change a client sees), `updated_at`, `started_at`, then the activity's fields (`title`, `text`, `context`, `icon`, `tone`, `countdown {to, label}`, `since`, `chip`, `progress {segments [{length, tone}], points [{at, tone}], value, tracker, indeterminate, styled_by_progress}`, `actions [{id, label, kind}]`, `choices [{key, title, tone, …}]`, `selected`, `open`, `alert`, `ends_at`), and for an ended one kept for its final `ended: true` and `final {title, text, tone, open}`. Rules:

- empty or unset fields are left out, never null (`tone` is always there, in choices too; a progress always has `value`, `indeterminate`, `styled_by_progress`);
- times are ISO 8601 with the offset of the server's zone, to the second;
- `selected` is always a key of `choices` when there are any (the first when the add-on gave none);
- a choice carries only display fields (`title`, `text`, `context`, `icon`, `tone`, `countdown`, `since`, `chip`, `progress`, `open`); show the top-level fields overridden by the selected choice's (a field the choice leaves out keeps the top-level one);
- `alert`: alert only when it is non-empty and differs from the last alert shown for that id.

`tests/fixtures/live_web.json` is a train-like activity with every field and an ended one with a final.

### Clients

- **Phone** (`android/docs/notifications.md`): an Android 16 Live Update per activity, from the `live` push.
- **Web** (`static/app-live.js`, its pure part `static/app-live-core.js`, `tests/test_web_live.mjs`; `docs/web-design.md`, "Now"): the **Live** strip on top of Now, one compact card per activity: the Lucide icon on its tone's tint, the title, text and context, the chip, the timer ticking every second in the browser ("Departs in 7:42", "1 h 05", "Elapsed 28:14"; `aria-live="off"` with the absolute time for screen readers), the bar (segments in their tones, faint until reached when styled by progress, the points, the tracker icon at the value) and the actions (Earlier / Next switch the shown choice at once, then `select`; a server action is the pill, `act`; Stop, `DELETE`). A tap on the card follows `open` (a `/path` is that screen in the shell, https a new tab). An ended one with a final shows muted with "Done" and a × until it leaves the list. `GET /api/live` when Now opens, every 20 s while Now is on screen and the tab visible, and when the window comes back (the push is for the phone only); hidden when there is none.
- **Omarchy** (`extensions/omarchy/README.md`): the bar's glyph becomes the soonest running activity's icon with its countdown (else its count-up, else its chip); the Now panel lists them with Earlier / Next / the server actions / Stop. It polls `GET /api/live` with Now, and every 20 s while one is running.
