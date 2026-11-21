# The image's plugins

The container image loads every kind in this folder (`DANA_PLUGIN_DIRS=/app/plugins`). Don't put your plugins here: keep them in your site's `plugins/` (`../site/plugins/`, docs/site.md at the repository root). Running from a checkout loads them from there, and `bin/deploy` copies them into this folder of its clean build checkout.

How to write one: [`docs/plugins.md`](../docs/plugins.md); a working example: [`examples/plugins/hello/`](../examples/plugins/hello/).
