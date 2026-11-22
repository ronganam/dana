"""The addon kit behind ``bin/dana addons new / check / test / dev / reference / publish``
(docs/writing-an-addon.md, "The addon kit").

- :mod:`.schema` — the manifest's JSON schema, made from the manifest dataclasses, and a small validator.
- :mod:`.reference` — docs/addon-reference.md (each new addon's REFERENCE.md), generated from the code.
- :mod:`.probe` — loads one addon in a clean process and reports what it declares (run by check).
- :mod:`.check` — what ``check`` finds: the manifest, imports, migrations, tool docstrings, declared vs used.
- :mod:`.runner` — ``test``: an addon's tests against Dana's harness.
- :mod:`.dev` — ``dev``: the addon in the local test environment, reloaded on save.
- :mod:`.publish` — ``publish``: its GitHub repository, a release, a pull request to the catalog.
"""
