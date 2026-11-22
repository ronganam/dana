# The household and the demo household

Who uses this Dana, managed in the browser (Settings → Household), and a made-up **demo household** an admin
can load to see Dana filled in and remove in one click.

Code: `core/household_api.py` (the household over HTTP), `core/demo.py` + `core/demo_api.py` (the demo),
`core/users.py` (`User.demo`, `users.demo_users()`), `core/auth.py` (viewing the demo), each addon's `demo.py`
(its sample data); web: `static/household.js` + `household.css` (the Household screen), `static/demo.js` (the
banner on Now). Tests: `bin/test test_household test_demo test_web_household`.

## The household

The Household screen (`household`, a core screen: `/#household`, a Settings tile and a place in the
navigation's core group for a user with `manage_users`) lists every member with their level, how many devices
they have and when one of their devices or tokens was last used. An admin can:

- **Add someone**: name, how Dana refers to them (they / she / he), languages (empty: the server's), level
  (member or admin). They get their default addons, as with `bin/dana users add`, and the Invite dialog opens.
- **Invite**: their phone's pairing (the QR code the Dana app scans and the code, drawn by
  `DanaDevicesCore.qrSvg`, the Devices page's own) next to a browser sign-in code they type on `/login`; both
  are ordinary pairings for that member (`POST /api/devices/pairings` with `user_id`, `docs/devices.md`), valid
  five minutes, followed until they are used, cancelled when the dialog closes.
- **Change the level** (the last admin stays admin) and **remove** a member after a confirmation: everything
  that is theirs goes with them (the database's cascades, as `bin/dana users remove --delete-data`), their
  devices are signed out at once. An admin cannot remove themselves here.

| Method and path | Who | Body → answer |
|---|---|---|
| `GET /api/household` | signed in | → `{members: [<member>], can_manage, you}`; without `manage_users` only themselves |
| `POST /api/household/members` | `manage_users` | `{name, pronoun?, languages?, level?}` → 201 `{member}` |
| `PATCH /api/household/members/{id}` | `manage_users` | `{name?, pronoun?, languages?, level?}` → `{member}`; 409 `last_admin` |
| `DELETE /api/household/members/{id}` | `manage_users` | → `{ok, removed}`; 409 `self` (yourself), `last_admin` |

`<member>` = `{id, name, level, pronoun, languages: [..], devices, last_seen_at, you}`. A bad field: 400
`{"error": "invalid", "field", "detail"}`. A demo user is not a member (404). A member changes their own
languages and currency in Settings → Profile (`PUT /api/profile`).

## The demo household

"Show me with sample data" (the setup wizard) calls `POST /api/demo`. That creates two **demo users**
(made-up members: Sam Carter and Jamie Carter, `core/demo.py` `PEOPLE`), gives them their default addons and
every enabled addon that has sample data and needs no credentials, then runs every addon's `@addon.demo`
hook for each (in addon order). Today: people (made-up people with fictional 555-01xx numbers), phone (calls
already transcribed and analysed with a suggestion waiting, notifications already sorted), tasks (open tasks,
two with reminders), shopping (items with their aisle), money (a current account and a card with three months
of transactions in their categories), about (facts). Loading again changes nothing.

**Why separate users.** Every per-user table in Dana has `user_id ... REFERENCES users(id) ON DELETE
CASCADE` (or hangs off such a row), so deleting the demo users removes exactly their rows and nothing of a
real user, whatever tables addons add later, with no tag column to keep in sync. Their usage rows (kept for a
removed real user, as cost history) are deleted too. The demo users' ids are kept in
`system_marks['users:demo']`; `users.list_users()` leaves them out, so no background job, composer
(briefing, weekly review), sharing target, `share_item`, household list or user count sees them: they cost no
model call and send nothing to anyone. They are members, with no token, device or Telegram id, and cannot be
made admin.

**Viewing it.** An admin views the app as the first demo user: `POST /api/demo/view` sets the `dana_demo_view`
cookie (the demo user's id); while it names a demo user and the signed-in user has `manage_users`, the auth
middleware makes every request the demo user's (`request["user"]`, with the admin in `request["viewer"]`),
except `/api/demo*`, which stays the admin's so the banner can always go back or remove it. A member with the
cookie, or the cookie naming a real user, changes nothing.

**The banner** (`static/demo.js`, on Now for an admin): "Sample data is loaded" with *View it* and *Remove*;
while viewing, "Viewing the sample household as Sam Carter" with *Back to my account* and *Remove* (with a
confirmation).

| Method and path | Who | Answer |
|---|---|---|
| `GET /api/demo` | `manage_users` | `{loaded, users: [{id, name}], rows: {}, viewing: <id> \| null}` |
| `POST /api/demo` | `manage_users` | 201 `{loaded: true, users, rows: {<addon>: n}, viewing}`; 200 with `rows: {}` when it was loaded |
| `DELETE /api/demo` | `manage_users` | `{loaded: false, removed: <users>, users: [], viewing: null}`; clears the view cookie |
| `POST /api/demo/view` | `manage_users` | `{user_id?}` → `{viewing}`; 409 `not_loaded`, 404 not a demo user |
| `DELETE /api/demo/view` | `manage_users` | `{viewing: null}` |

### An addon's sample data

```python
# addons/<name>/demo.py (imported by the loader after tools.py and hooks.py)
from core.addons import DemoContext
from addons.<name>.addon import addon

@addon.demo
def demo_things(user, ctx: DemoContext) -> int:
    """Write this addon's rows for ``user`` (ctx.index 0: the one an admin views first); return how many."""
```

A plain function (it runs in a worker thread). Rules: write only rows that go with the user (`user_id`
with `ON DELETE CASCADE`, or rows of such rows), nothing in a shared table (the shopping aisle cache, a
server-wide mark), no file on disk; never call the model or an outside service, and leave nothing for a
background job to pick up (write calls and notifications as already processed); made-up names, fictional
numbers, no real money data. `ctx.now`, `ctx.ago(days=, hours=, minutes=)` and `ctx.household` (the demo
users) help. `bin/test test_demo` loads every hook and checks that removing the demo brings every table back
to exactly what it held before.
