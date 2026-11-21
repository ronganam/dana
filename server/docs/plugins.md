# Plugins: integration kinds outside the repository

A **plugin** is an integration kind (`docs/access.md`, "Integration") that lives in its own folder instead of `integrations/`. It is written exactly like a built-in kind and loaded the same way. Use one for something private to your setup, or to try a kind before you contribute it. A kind that others could use belongs in `integrations/`: open a pull request.

## Loading

A deployment keeps its plugins in its site's `plugins/` folder (`site/plugins/` at the repository root, or `$DANA_SITE/plugins`; `../docs/site.md`). `DANA_PLUGIN_DIRS` (folders separated by `:`, `os.pathsep`) replaces that default when set; empty means none. Every sub-folder that has a `kind.py` is imported as `integrations.<name>` after the built-in kinds, in folder order and then by name (`integrations/__init__.py`, `PLUGIN_KINDS`). A plugin with a built-in kind's name, or two plugins with one name, stop the start. Folders that do not exist are skipped. The tests never load your plugins: `testenv.isolate()` sets `DANA_PLUGIN_DIRS=""`.

| Where | How |
|---|---|
| Running from a checkout | The site's `plugins/`, with nothing to set |
| Container image | Loads `/app/plugins` (the `plugins/` folder of the build context; only its `README.md` is tracked) |
| `bin/deploy` | Copies the site's plugin kinds (or those of the folders in `DANA_IMAGE_PLUGINS`) into `plugins/` of the clean build checkout, and adds `-p<hash>` of their files to the release name |

A user's connection of a plugin kind is a row in `integrations` like any other. If the plugin is later missing, the row is ignored: the kind is not offered and its tools are not listed.

## Writing one

Start from the complete example, [`examples/plugins/hello/`](../examples/plugins/hello/): a kind with a setting, a tool, a line of instructions and an HTTP route. `test_plugins.py` loads that exact folder, so it shows the current framework.

```bash
cp -r server/examples/plugins/hello site/plugins/my_kind   # then rename hello → my_kind inside
```

The parts, on a kind that reads a device on your network:

```
site/plugins/
  weather_station/
    __init__.py     # imports the kind and its tools
    kind.py         # the Integration
    tools.py        # @weather_station.tool() functions
```

```python
# kind.py
from integrations.framework import ADDRESS, Field, Integration

weather_station = Integration(
    "weather_station",
    credentials=(Field("url", "Station address", type=ADDRESS),),
    description="My backyard weather station",
)
```

```python
# tools.py
import httpx
from integrations.framework import Connection
from integrations.weather_station.kind import weather_station


@weather_station.tool()
async def station_reading(conn: Connection) -> str:
    """The station's current temperature and humidity."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{conn.credentials['url']}/now")
    return r.text
```

```python
# __init__.py
from integrations.weather_station.kind import weather_station
from integrations.weather_station import tools  # noqa: F401 (registers the tools)
```

- The schema comes from the type hints and the Google-style docstring; `conn` (the user's connection: `conn.user`, `conn.credentials`, `conn.settings`) is left out of it.
- Flags on `@kind.tool()`: `is_write`, `confirm` (asks the user first; only for sending to someone or destroying something), `acts_outside`, `untrusted_result`. Their meaning: the `tool()` docstring in `integrations/framework/kind.py` and `docs/agent-actions.md`.
- Hooks a kind can declare (`check`, `on_connect`, `prompt_parts`, `options`, …): the `Integration` docstring.
- Routes: an `api.py` with `setup(app)` in the plugin is registered after the built-in route modules (`core/server.py`, `plugin_route_modules`), behind the same auth middleware. Its handlers resolve the user from `request["user"]` and call `store.require(user, "<kind>")`, like the built-in kinds' `api.py`.
- The web Integrations page shows a kind it has no words for by its name and description (`static/integrations.js`, `KINDS`).

Connect it for a user on the Integrations page, with `bin/users.py integrations connect <user> <kind>`, or `PUT /api/integrations/<kind>`.

## Testing one

A test script that sets `DANA_PLUGIN_DIRS` before it imports the app (after `testenv.isolate()`) gets the plugin like production does; `test_plugins.py` builds throwaway plugins that way.
