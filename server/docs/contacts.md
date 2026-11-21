# Google Contacts

The user's Google Contacts, available to Dana: a copy of the contacts is kept
per user and synced from Google's People API; Dana searches and reads that
copy, the user's people are filled in from it, and Dana adds or changes a
Google contact only after the user approves.

Addon `contacts` (`docs/access.md` → Addons, Google account). Tests: `bin/test
test_contacts_users`.

## Code

`addons/contacts/`:

| Module | Holds |
|---|---|
| `config.py` | The addon's name, the People API field mask (`PERSON_FIELDS`), the sync interval (`CONTACTS_SYNC_MINUTES`) |
| `addon.py` | The declaration: credential `refresh_token`, the scope, the connection hooks, the prompt parts |
| `client.py` | The People API over the user's connection: `list_changes`, `get`, `create`, `update`, `check` |
| `store.py` | Each user's copy: writing a sync result, one contact, `search`, `caller_name`, the sync token |
| `sync.py` | `sync_user`, `sync_job` (the `hooks.py` job's pass), what happens on connect, on another refresh token and on disconnect |
| `enrich.py` | `fill_people`: the user's people filled in from the copy |
| `tools.py` | `search_contacts`, `get_contact`, `update_contact`, `add_contact` |

## Connection

- A Google addon (`addons/google_account/oauth.py`): the credential is
  the `refresh_token` the Google sign-in stores, the scope is exactly
  `https://www.googleapis.com/auth/contacts`, the People API client is built
  per call from `oauth.credentials(conn)`. No settings. It is connected by the
  sign-in (or a token typed by hand).
- `check` (`POST /api/addons/contacts/test`): one request for the first
  contact's name; a failure is a short reason without a token.
- No HTTP endpoint of its own.

## The copy

Migration `m0023_google_contacts`. Everything is keyed by `user_id`; another
user's contact id answers like an unknown one.

| Table | Holds |
|---|---|
| `google_contacts` | one row per `(user_id, resource_name)` (`people/c…`, the contact id everywhere): `etag`, `name`, `other_names` (other name forms and nicknames), `phones` (`{number, value, type}`), `emails` (`{value, type}`), `birthday` (`YYYY-MM-DD`, or `MM-DD` without a year), `organisation`, `job_title`, `updated_at` |
| `google_contact_phones` | `(user_id, phone, resource_name)`: number → contact, for the caller-name lookup and the people fill. A number may be on several contacts |
| `google_contacts_sync` | per user: `sync_token`, `synced_at` |

- A phone's `number` is its digits normalised exactly like a person's numbers
  (`addons/people/values.py::norm_phone`: country code added to local
  numbers, `DEFAULT_COUNTRY_CODE`), from Google's `canonicalForm` when it
  gives one, else from the value; `value` and `type` are kept as Google has
  them, so an update sends the other numbers back unchanged. A value that is
  not a number (a short code) has `number` empty.
- A contact without a name is called by its organisation.
- The copy is written only from what Google returned: a sync, or the answer
  to a contact Dana added or changed. "Other contacts" are not read.

## Sync

`people.connections.list` for `people/me` with `requestSyncToken` and the
field mask `names,nicknames,phoneNumbers,emailAddresses,birthdays,organizations`
(the same on every request, as Google requires for a sync token), 1000 per
page.

- **Full**: without a stored sync token every contact is read; contacts
  Google did not return are removed from the copy.
- **Incremental**: with the stored token only what changed comes back; a
  contact with `metadata.deleted` is removed.
- **Expired token** (HTTP 410, or 400 naming the sync token): one full read
  in the same run.
- The result is written in one transaction together with the new sync token
  and the time. A failed sync leaves the copy and the token as they were.
- One sync at a time per user. A sync whose connection was disconnected or
  got another refresh token while it was reading writes nothing.

When it runs:

- **On connect** (`on_connect`): in the background; the request that
  connected the addon does not wait. Until it finishes, the tools answer that
  the contacts have not been read yet. A connection made from another
  process (`bin/dana users`) is read by the loop's next run.
- **Another refresh token** (`on_credentials_change`, e.g. signing in to
  Google again): the sync token is forgotten and a full read follows, so the
  copy becomes that account's.
- **The job** (`sync_contacts` in `hooks.py`, an `@addon.job`): 20 s after
  startup, then every `CONTACTS_SYNC_MINUTES` (60), every user with the
  addon, each as that user (`users.acting_as`), one after another; a user
  whose sync fails is logged (the reason only) and the next one still runs.
- **Disconnect** (`on_change`, once the addon is gone): the user's rows in
  the three tables are deleted, and their people forget which contact they
  were filled from (what was filled in stays).

## People

`enrich.fill_people(user)` runs after every sync and after Dana added or
changed a contact, only for a user who also has `people`. It never creates a
person. For each of the user's people whose number is on exactly one of
their contacts — or on the contact the person is already linked to — the
person takes from that contact (`addons/people/store.py::fill_from_contact`):

| What | When |
|---|---|
| the name, and the person is shown (`hidden` off) | only for an auto-created number-only person (`source=auto`, the name is a number) |
| the contact's other numbers | each only when none of the user's people has it |
| the birthday | when the person has none |
| the emails (`people.emails`) | when the person has none |
| the contact's id (`people.contact_resource`) | always |

- A name, birthday, email or note the user typed is never changed, and a
  person the user hid stays hidden.
- Numbers that lead to two different contacts, and a number two contacts
  share, link nothing.
- When the linked contact is deleted in Google the link is cleared.
- After a name or number was added, the user's unmatched calls and
  notifications are looked at again (`docs/people.md` → Linking).

**The caller's name** (`store.caller_name(user, phone)`, the one function
`people` asks): the name the user's contacts give a number — exact, else by
the last 9 digits; none without the addon, for an unknown number, or when
contacts with different names share it. `people` uses it in two places
(`docs/people.md`): a new caller whose call carries no name is created with
that name (shown, not a hidden number), or linked to the person who already
has that name; and the incoming-call card for a number none of the user's
people has shows that name.

## Tools

| Tool | Asks first? | What |
|---|---|---|
| `search_contacts(query, limit=10)` | no | the copy, by name, phone number or email |
| `get_contact(contact_id)` | no | one contact in full |
| `update_contact(contact_id, name, add_phone, remove_phone, add_email, remove_email, birthday, organisation)` | yes | changes an existing Google contact |
| `add_contact(name, phone, email, birthday, organisation)` | yes | adds a Google contact |

There is no tool that deletes a contact. Without the addon the tools are not
offered and are refused like a made-up tool name.

- **Search** scores each contact and returns the best first: the name itself,
  whole words of it, the start of a word, part of it — whatever the case and
  punctuation, over the name, the other names and the organisation; a name
  written in the other script (Hebrew ↔ Latin letters) matches by its
  consonants; a phone number in any format, or the end of one; an email
  address or part of one. A near-miss spelling is tried only when nothing
  else matched.
- **Update** sends the stored `etag` with only the fields that change
  (`updatePersonFields`); a field Google replaces as a whole (numbers,
  emails) is sent in full, the existing entries with their value and type as
  stored. The copy is then replaced by the contact Google returned (the new
  etag included), without waiting for a sync. An empty `birthday` /
  `organisation` removes it (the job title stays).
  - The contact changed in Google since the last sync: nothing is changed,
    the contact is read again into the copy and Dana is told to look and
    repeat.
  - The contact no longer exists in Google: it is removed from the copy.
  - A change that changes nothing, a value that cannot be used, or an
    unknown id never reaches Google.
- **Add** refuses a phone number one of the user's contacts already has
  (pointing at that contact), then stores the contact Google returned.
- **Approval** (`@addon.tool(..., describe=...)` in `addons/contacts/tools.py`, rendered by `core/approval_text.py`): "Add *name* to Google Contacts" with
  the phone, email, birthday and organisation; "Change *name* in Google
  Contacts" with each change — the name comes from the user's own copy, never
  from a Google call.
- **Prompt** (`addon.py`): Dana looks a person's number or email up with
  `search_contacts` instead of asking, and calls `add_contact` /
  `update_contact` without asking in text (the Approve button does).
