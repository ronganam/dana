# Writing an addon

The design of addons (what an addon declares, the framework, the build plan) is [`../docs/addons.md`](../../docs/addons.md); the framework's reference (fields, hooks, the store, the registry) is `docs/access.md`, "Addon". This page is the short practical part: where an addon lives, how it is loaded, and how to start one.

## Where it lives

| Addon | Folder | Loaded as |
|---|---|---|
| Official (ships with Dana) | `server/addons/<name>/` | `addons.<name>`, in `ADDON_ORDER` (`addons/__init__.py`) |
| Site addon (one deployment's own) | the site's `addons/<name>/` (`site/addons/` at the repository root, or `$DANA_SITE/addons`; `../docs/site.md`) | `addons.<name>` too, after the official ones (`addons.SITE_ADDONS`) |

`DANA_ADDON_DIRS` (folders separated by `:`) replaces the site's `addons/` when set; empty means none. Every sub-folder with an `addon.py` is imported, in folder order and then by name. A site addon with an official addon's name, or two with one name, stop the start; folders that do not exist are skipped. A site addon is written exactly like an official one. One that others could use belongs in `server/addons/`: open a pull request.

| Where it runs | Site addons it loads |
|---|---|
| A checkout | the site's `addons/`, with nothing to set |
| The container image | `/app/addons-site` (`DANA_ADDON_DIRS`; the Dockerfile's `site_addons` build context, empty unless `bin/deploy` fills it) |
| `bin/deploy` | stages the site's addons (or those of the folders in `DANA_IMAGE_ADDONS`) as that build context, and adds `-a<hash>` of their files to the release name |

A user's connection of an addon is a row in `connections` like any other. If a site addon is later missing, its rows are ignored: it is not offered and its tools are not listed.

## Starting one

Start from the complete example, [`examples/addons/hello/`](../../examples/addons/hello/) at the repository root: an addon with a setting, a tool, a line of instructions and an HTTP route. `tests/test_addon_loading.py` loads that exact folder, so it shows the current framework.

```bash
cp -r examples/addons/hello site/addons/my_addon   # then rename hello → my_addon inside
```

```
my_addon/
  __init__.py     # from addons.my_addon import addon, tools  (registers both)
  addon.py        # addon = Addon("my_addon", ...)
  tools.py        # @addon.tool() functions; the first parameter is conn: Connection
  api.py          # optional: setup(app) adds its routes
  tests/          # optional: test_*.py, run by bin/test
```

- Inside the addon its declared object is `addon`. Another module refers to it as `from addons.<name>.addon import addon as <name>_addon`.
- The tool schema comes from the type hints and the Google-style docstring; `conn` (the user's connection: `conn.user`, `conn.credentials`, `conn.settings`) is left out of it. Flags on `@addon.tool()`: `is_write`, `confirm`, `acts_outside`, `untrusted_result` (the `tool()` docstring in `core/addons/addon.py`, `docs/agent-actions.md`). Hooks (`check`, `on_connect`, `prompt_parts`, `options`, …): the `Addon` docstring.
- Routes in `api.py` are registered after the official route modules (`core/server.py`, `site_route_modules`), behind the same auth middleware; handlers take the user from `request["user"]` and call `store.require(user, "<name>")`.
- The web Addons page shows an addon it has no words for by its name and description (`static/addons.js`, `ADDONS`).

Connect it for a user on the Addons page, with `bin/users.py addons connect <user> <name>`, or `PUT /api/addons/<name>`.

## Testing one

`bin/test` runs the `tests/test_*.py` of every site addon it finds (the same folders as the server). `testenv.isolate()` sets `DANA_ADDON_DIRS=""`, so a test that needs its addon sets `DANA_ADDON_DIRS` to the addon's parent folder after `isolate()` and before it imports the app; `tests/test_addon_loading.py` builds throwaway site addons that way.
