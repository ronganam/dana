# Hello

The example addon: what `bin/dana addons new hello --web` writes, grown by a table, a job, an event handler and a Now card. Copy it into your site (`site/addons/hello`), rename it and the addon, restart Dana, and turn it on in Settings → Addons. `server/tests/test_addon_loading.py` runs its test, so it keeps working as Dana changes; `server/tests/test_addon_scaffold.py` keeps it the scaffold's shape.

- **Setting** `greeting`: how it greets people (default `Hello`).
- **Tool** `hello_greet` (`tools.py`): greets someone in the user's words.
- **Page** `hello` (`web/hello.js`), reading **route** `GET /api/hello` (`api.py`): the greeting and who is left to greet.
- **Table** `hello_greetings` (`migrations/m0001_baseline.py`, rows in `store.py`): people to greet, per user.
- **Hooks** (`hooks.py`): a task titled "Greet <name>" (the `tasks.task.created` event) puts <name> on the Now screen as a card with a Greeted action; a daily job forgets greetings done more than 30 days ago.

Emits no events. Test: `bin/test test_addon_loading` (from `server/`), or `PYTHONPATH=.:tests python ../examples/addons/hello/tests/test_hello.py`.
