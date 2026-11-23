# Knowledge: projects and entries

Projects (topics such as a wedding, a trip, a flat hunt), the sections that
organise them (decisions with their options side by side, notes) and the
entries saved in them (notes, records, links, options), with files attached.
Code: `addons/knowledge/`; the `/api/projects*`, `/api/sections*` and
`/api/entries*` handlers and `GET /media/{name}` live in
`addons/knowledge/api.py` and call into the package. Web panel, the addon's
page (`web/`, registered with `Dana.panel`): `web/projects.js` +
`web/projects-core.js` (pure, tested by `bin/test test_web_projects` against
`tests/fixtures/projects_web.json`) + `web/projects.css`. Tests: `bin/test test_knowledge_users` (per user,
sharing, media), `bin/test test_knowledge_sections` (sections, decisions,
columns, files, budget, the tools, search, `save_whatsapp_file`, the web fixture),
`bin/test test_db` (the addon's migrations), `bin/test test_addons`.

| Module | What |
|---|---|
| `values.py` | vocabulary (`KINDS`, `STATUSES`, `PRIORITIES`, `FIELD_TYPES`, `SECTION_KINDS`, `DECISIONS`, column roles), normalisation (`clean_value`: a money / number / rating value is a number when it reads as one — "13,400 ₪" → 13400 — and stays text otherwise, e.g. a range; `clean_columns`), row → payload (`serialize_project` / `serialize_entry` / `serialize_section` / `serialize_file` leave out the owner columns and stored file names) |
| `projects.py` | projects for a user (with their stats: sections, decisions made, the committed sum): `visible_sql`, `access`, `list_projects`, `get_project`, `create_project`, `update_project` / `delete_project` (owner only), `writable_project`, `project_tasks`, `active_projects`, `search_titles`; registers the sharing item type `project` |
| `entries.py` | entries for a user: `visible_sql`, `entry_row`, `writable_entry`, `get_entry`, `list_entries`, `entry_previews`, `create_entry` / `update_entry` (also moves an entry to another project) / `delete_entry`, `fts_match_query` / `search_entries` (FTS, below), `matching_ids` / `search_rows` (the search box), `retry_media`, `media_file` |
| `sections.py` | sections of a project: `list_sections`, `get_section`, `find_section` (id or title), `create_section`, `update_section` (renaming a column renames that field in the section's entries), `delete_section` (its entries become loose, or go with it) |
| `files.py` | files on entries (`paths.KNOWLEDGE_FILES_DIR`, table `entry_files`, up to 60 MB each): `add_file` (a file Dana can read, `llm.readable`: audio, a PDF, a photo, text, is read in the background as the owner with `llm.read_file`, purpose `knowledge_file`, into `transcript`), `fill_texts` (reads the files of an entry that have no text yet, waiting up to 90 s), `files_of`, `file_path`, `update_file`, `delete_file`, `forget_entries` (rows and disk files when entries go) |
| `media.py` | link processing: yt-dlp download, transcript (`llm.transcribe`), summary + tags (`llm.complete_json`, purpose `media_summary`), stored on the entry; `file_for` (the `/media/` files) |
| `tools.py` | the 14 agent tools (`create_project`, `list_projects`, `list_project_entries`, `get_knowledge_item`, `save_knowledge_item`, `search_knowledge`, `update_project`, `delete_project`, `update_knowledge_item` (`project` moves the entry), `delete_knowledge_item`, `update_entry_file` (rename / note; `remove` asks first: the file leaves the disk, no undo), `create_section`, `update_section` (`columns` merge by key, else name; `"remove": true` drops one), `delete_section` (`confirm`, an Approve card naming it)) and their undos; `record_file_added` registers the undo of a file a tool attached |

## Sections, decisions and files

- **A project** has an optional `target_date` (the panel counts down to it)
  and `budget`, in the owner's currency (`users.currency`, else `CURRENCY`;
  every project payload carries it as `currency`, also through a share, and
  the panel and `list_project_entries` show money in it). `GET /api/projects` adds per project `section_count`,
  `decisions` (options sections), `decided` (those with a chosen option) and
  `committed` (the sum of the chosen options' price column).
- **A section** (`project_sections`) is `notes` (free items) or `options` (a
  decision): it has `columns` — `[{key, name, type, role?, best?, options?}]`,
  types from `FIELD_TYPES` (text, number, money, phone, checkbox, date, url,
  select, rating); `role: "price"` marks the column that counts toward the
  budget; `best` (`low` / `high`) which value the compare view highlights
  (money defaults to low, rating to high) — and a Markdown `summary` (the
  TL;DR, a comparison table, the recommendation). A column's `key` stays when
  it is renamed, and the entries' field follows the new name.
- **An entry** may sit in a section (`section_id`, a section of its own
  project, else 400 `field: section_id`). `update_knowledge_item` with
  `project` (id or title of a project the user can write) moves it: it then
  belongs to that project's owner, its section is cleared unless `section`
  names one of the new project's, and the undo moves it back (while the old
  project is still writable); an option's `decision` is `""`,
  `shortlist`, `chosen` or `rejected`. Its values for the columns are
  ordinary `fields` named like the columns; fields no column names still show
  (as extras). Entries without a section are "Other items".
- **Files** (`entry_files`): uploaded (`POST /api/entries/{id}/files`,
  multipart `file` + `note`; 413 over the limit) or saved from WhatsApp
  (`save_whatsapp_file`, docs/whatsapp.md). `GET
  /api/entries/{id}/files/{file_id}` serves the file inline (`?download=1` as
  an attachment) to whoever sees the entry; `PUT` changes its `name` / `note`
  / `transcript`, `DELETE` removes it (both need write access). An entry's
  payload lists its `files` with their `url`. A file's `transcript` is the
  text read from it (a recording's transcript, a PDF written out, a photo
  described); `get_knowledge_item` shows it (up to 6,000 characters each) and
  reads a file that has none yet first (an older file, a read that failed).
- **Payloads.** `GET /api/projects/{id}/items` is `{project, sections,
  entries, tasks}`; `POST /api/projects/{id}/sections`, `PUT` / `DELETE
  /api/sections/{id}` (`?with_entries=1` deletes its entries too). Writes
  follow the project's sharing level (below). Old clients that ignore
  sections still see every entry.
- **Dana** keeps a decision as an options section: one entry per option
  (title = the vendor's name, values in the columns, the details in the
  body, files attached), the comparison in the section's summary, and sets
  `decision` only when the user says so (`KNOWLEDGE_GUIDE` in `addon.py`).
  `list_project_entries` prints a project by section with each option's
  values and decision. `update_knowledge_item` changes only what it is given:
  `fields` merge by name (a null value removes one), `body_edits` replace
  exact passages of the body or append; its result names what changed and,
  when a price or decision changed under a written summary, asks to refresh
  it. A quote or contract beats a chat about it: the guide has Dana read the
  newest one before writing its figures.

## The web panel

Three levels (`data-level` on `#panel-projects`): the project list, a project
(its hero with the countdown and decisions made, the budget card, the
decision and notes section cards, other items, tasks) and a section (the
summary card, the options as a compare table — the best value per column
marked, rejected options faded — or as cards, an add box). An entry opens in
a sheet: the decision buttons, call / WhatsApp / link chips, its values
edited in place, details, files (players for audio / video / images,
transcripts, upload by picker or drop) and moving it to another section. On a
phone one level shows at a time and the sheet is full screen. A project whose
titles are Hebrew is laid out right to left and its labels, counts and
decisions are in Hebrew. The project and section editors (columns, their
types, the budget column) are sheets too.

## Per user

- **Projects** belong to the user who created them (`projects.user_id`).
  Titles are not unique. A user sees their own projects plus the ones shared
  with them, and nothing without the `knowledge` addon (HTTP: 403
  `not_connected`; tools not offered).
- **Entries** belong to the owner of their project (`entries.user_id`; an
  entry without a project to its creator) and record who created them
  (`entries.created_by`). They follow their project: whoever sees the project
  sees its entries.
- **Search.** The FTS index `entries_fts` holds every user's entries; every
  query of it (`search_knowledge`, `entries.search_entries`, the search box in
  `core/search.py`) joins `entries` and keeps only the rows the user can see.
  Besides the entry's own text it has a `files` column: each file's name,
  note and `transcript` (the text read from it), one per line (migration
  `m0002_files_in_search`; triggers on `entries` and `entry_files` keep it
  current). `search_entries` matches every word as a word prefix (`"word"*`);
  a Hebrew word also without its leading prefix letter (ה ו ב ל מ ש כ:
  "באביב" finds "אביב") and with one ("צלם" finds "הצלם"); when no entry
  has every word, any word. Each hit carries an FTS `snippet` around it,
  `matched` (the column), `matched_file` (when it is in a file's text),
  `section_title` and the entry's files (one query for all hits);
  `search_knowledge` prints the entry id, project, section, decision and
  that snippet.
- **Media files** (`paths.MEDIA_DIR`, URL `/media/<file>` in
  `entries.media_path`) are served by `GET /media/{name}` only to a user who
  can see an entry with that file (else 404; a file no entry names is never
  served). Range requests work (video seeking).
- **Who set the link** (`entries.url_set_by`, migration
  `m0018_entry_url_set_by`; existing rows: the creator): the user who saved the
  entry with its URL or last changed the URL (`update_knowledge_item`, `PUT
  /api/entries/{id}`; sending the same URL back with other changes does not
  count). Never in a payload. The undo of a URL change puts back who had set
  the old URL. A user without `manage_server` setting a URL gets it checked at
  once (`core/outbound.check_url_form`: http(s), a host, no literal
  non-public address, no `localhost`) and refused with 400 `{"error":
  "invalid", "field": "url", "message"}` / a tool error.
- **Media processing** (a link saved with `url`, `POST /api/entries/{id}/retry`)
  runs in the background as the entry's owner (`users.acting_as`), so its model
  calls are that user's usage. Unless both the user who set the link
  (`url_set_by`; unknown, e.g. removed, counts as restricted) and the user who
  saved / retried it have `manage_server`, the download is restricted — also
  when an admin presses Retry on a link a member set (`core/outbound.py`,
  docs/access.md → Security rules): `http` / `https` on a host whose every
  address is public (no loopback / private / link-local / cluster address),
  and yt-dlp with site extractors only (`--use-extractors default,-generic`),
  since it resolves the name again and follows redirects itself. A refused
  link is logged and the entry keeps the link without media, as after a
  failed download. A URL that is no longer the entry's when processing starts
  is skipped. The URL is always passed after `--`.
- **Tasks with a project** (`tasks.project_id`, legacy; and task links of
  `ref_type` `project`) stay governed by their own list: a project shows only
  the tasks the user can see through `tasks` (`project_tasks`, `task_count`,
  both asked of the tasks addon's service: `project_tasks`,
  `project_task_counts`, `project_in_use`); a project never grants access to a
  task, nor a task to a project. The column has no foreign key (the projects
  table is this addon's, the tasks table the tasks addon's): deleting a project
  emits `knowledge.project.deleted` (`project_id`) for its owner, and the tasks
  addon then deletes the owner's tasks that carry it and clears it from
  everyone else's (`service.forget_project`, run in the background).
- **Composers.** The call analysis context lists the current user's active
  projects (`projects.active_projects`); approval cards name a project / entry
  only when the current user can see it.

## Sharing

Item type `project` (docs/access.md, "Sharing"): `share_item(item_type:
'project', item: <title or id>, …)` finds only the caller's own projects (a
title shared with them and also used by one of their own resolves to their
own); `/api/sharing/project/{id}`.

| | owner | `edit` | `read` |
|---|---|---|---|
| see the project, its entries, search hits, media | yes | yes | yes |
| add / change / delete entries, retry a link | yes | yes | no: "This project is shared with you read-only, so nothing in it can be changed." (HTTP 403 `read_only`) |
| rename / change / archive / delete / share the project | yes | no: "Only the owner of the project can change / delete it." (HTTP 403 `owner_only`) | no |

An entry added through an `edit` share belongs to the project's owner and
records who added it. Unsharing hides the project, its entries, its search
hits and its media again (the entries someone added stay in the project).
An undo of an entry change is refused once its user can no longer change the
entry. Deleting a project ends its shares. Payloads (`GET /api/projects`,
`GET /api/projects/{id}/items`, `PUT /api/entries/{id}`) add `shared_by` (the
owner's name) / `access` to shared projects and their entries only; tool
output adds "[shared by …, read-only]" / "Shared by …".
