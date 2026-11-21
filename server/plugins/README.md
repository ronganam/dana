# Plugins

Integration kinds kept outside this repository (private ones, or ones you are trying out). The container image loads every kind in this folder (`DANA_PLUGIN_DIRS=/app/plugins`); `bin/deploy` copies the folders named by `DANA_IMAGE_PLUGINS` here before it builds. Locally, point `DANA_PLUGIN_DIRS` in `.env` at your plugin folders instead of putting them here.

How to write one: [`docs/plugins.md`](../docs/plugins.md).
