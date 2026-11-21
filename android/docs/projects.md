# Projects (phone)

Projects on the phone: a wedding, a trip, a flat hunt — decisions whose options are compared side by side, notes, links and the files people send. The server contract (projects, sections and their columns, entries, files, sharing, search) is the server repo's `docs/knowledge.md`; its web panel (`static/projects.js`, `projects-core.js`, `projects.css`) is the reference for look, rules and wording, and `test_fixtures/projects_web.json` holds real payloads.

## Pieces

| Where | What |
|---|---|
| `data/projects/ProjectsModels.kt` | `Project` (with its list stats, `sharedBy`, `access`: null = own, `edit` / `read` through a share), `Section` (`options` / `notes`, `columns`, `summary`), `Column` (`key`, `type`, `role` `price`, `best`, select `options`), `Entry` (`decision`, `fields`, `files`), `EntryField`, `EntryFile`, `ProjectTask`, `ProjectItems` |
| `ProjectsJson.kt` | org.json ↔ models; `fieldsJson` / `columnsJson` are what writes send (column keys kept) |
| `ProjectsCore.kt` | The port of `projects-core.js`: `asNumber` (the server's rule: ranges and text are no number), money / short money, values by type (`shown`), best value per column (`bestIds`: money low, rating high unless the column says; rejected left out; only with two numbers that differ), option order (chosen, shortlist, option, rejected), section lines in English / Hebrew, countdown, budget (committed = the chosen options' price column), `setField` (typed by column), extra fields, phones (tel / wa.me 972…), file kind / size, the editor's column payload, Hebrew detection (`langOf`) |
| `ProjectsApi.kt` | The calls (`/api/projects`, `/api/projects/{id}/items`, sections, entries, files), errors → `ProjectsApiError` with the server's words (`read_only`, `owner_only`, 413 too large); `not_connected` drops the `knowledge` kind (`Me.drop`) |
| `data/network/ApiService.kt` | `uploadEntryFile` (multipart `file` + `note`, streamed, the name percent-encoded) and `downloadTo` (with the Bearer token) |
| `ui/viewmodels/ProjectsController.kt` | What is open (project, section, entry, the section's Compare / Cards pick, the summary being edited, the editor, the confirm), the loads, every write (optimistic decision), uploads, opening files, `focus` from search, `forget` |
| `ui/screens/projects/ProjectList.kt` | The list: tile, countdown · decided, budget line, decided ring, shared by; archived behind Show archived; in the side pane the open project's sections hang under it |
| `ProjectPage.kt` | Hero (date · countdown · decided, shared by), Edit project / + Decision / + Notes, budget card, section cards (decided mark, chosen / options, price range), Other items, tasks; two columns from 720 dp |
| `SectionPage.kt` | Summary (Markdown, edited in place), Compare table or Cards, the add box |
| `EntryPane.kt` | Decision, Call / WhatsApp / link chips, values edited in place by type, extra fields, From the link, details (Markdown), files, move, delete |
| `ProjectEditors.kt` | The project editor (icon, name, description, date, budget, colour, Archive / Restore, Delete) and the section editor (icon, name, kind, columns) |
| `ProjectMarkdown.kt` | The small Markdown of summaries and details: headings, lists, checks, code, quotes, pipe tables (shared column widths, sideways scroll), rules; each line runs in its own script's direction |
| `ProjectsUi.kt` | Shared bits: `ProjectScope` (Hebrew projects lay out right to left and speak Hebrew), kicker, back link, tile, ring, bar, decision pill, `ValueText`, the overlays (editor + confirm) |
| `ProjectsScreen.kt` | `ProjectsStage` (what the stage shows) |
| `ui/shell/destinations/ProjectsDestination.kt` | The place in the shell |

## The place

- More → Projects (route `"projects"`); a search result of kind `note` (an entry or a project) focuses it (`ProjectsController.focus`, then `navigateTo("projects")`). Fresh on arrival and when the app comes back to the front. No on-screen context (`placeRef`): the server has no project screen kind.
- **Cover (STACKED)**: one level at a time — projects → project → section → entry; the back link on top and Back pop one level.
- **Unfolded (SPLIT / FULL, a wide place)**: the side pane is the list with the open project's sections under it (the way around a project); the stage shows the project page or the section page. The first project opens by itself there (marked `auto`, so the cover still shows the list after folding). The open entry sits beside the page as a 370–400 dp pane when the stage is ≥ 760 dp wide, else over it (× / Back closes it). Fold / unfold keeps what is open.
- **Compare**: options are rows, columns are the section's columns (plus Files and Notes); the names column stays put while the rest scrolls sideways; columns fill the width when they fit. The best value of a column is in the accent with LOWEST / BEST, a rejected row is faded, a chosen row tinted with an accent bar, the open row marked. The default view is Cards on the cover or with fewer than two options, else Compare; the user's pick sticks per section while it is open. Links are underlined (the accent is kept for best values).
- **Entry**: decision buttons (optimistic); values edited in place by column type — yes / no toggles, dates with the date picker, ratings with stars, choices as buttons, the rest a field with the matching keyboard (numbers become numbers when whole). Details are Markdown, edited in place. Move to another section (or none) from the section menu.
- **Files**: listed with kind, size and note; images inline (Coil, Bearer token); a tap downloads to `cacheDir/project_files/<id>/` and opens it with an Intent through the FileProvider (`res/xml/file_paths.xml`); transcripts fold open. Upload with "+ Add files" (several at once) and an optional note; over 60 MB the server answers 413 and the line says so.
- **Deletes** (entry, file, section, project) ask first: the server's deletes return no Undo.
- **Sharing**: a read share shows no edit controls; an edit share can change sections, entries and files but not the project itself (Edit project is hidden: only the owner may change, archive or delete it).
- **Hebrew**: a project whose title or a section title is Hebrew lays out right to left with Hebrew labels; numbers, dates, phones and links stay left to right.
- **Errors** are said in the snackbar from the server's `error` / `message`.

## Tests

`app/src/test/java/io/github/ronganam/dana/projects/`: `ProjectsJsonTest` (the fixture's list / owner / read-share payloads, the writers), `ProjectsCoreTest` (numbers, money, best values, order, section lines, countdown, budget, `setField`, phones, values by type, the column payload, helpers), `ProjectMarkdownTest` (tables, lists, checks, code). The fixture is `app/src/test/resources/projects_web.json`, the server's `test_fixtures/projects_web.json` with the sharer's name replaced.
