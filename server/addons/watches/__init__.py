"""The watches addon: "when X happens, do Y" compiled into watch specs and fired by a cheap engine (docs/watches.md).

- :mod:`.spec` — the spec: validation, time rules, cheap matching, dry runs;
- :mod:`.store` — watches and their fires in the database;
- :mod:`.engine` — the live engine (the events tap, the time pass);
- :mod:`.pipeline` — what the engine did, in memory, with live subscribers;
- :mod:`.compiler` — plain words to a spec;
- :mod:`.actions` — what a fire does;
- :mod:`.service` — create / change / describe, shared by the tools and the routes;
- :mod:`.tools`, :mod:`.api`, :mod:`.hooks` — the tools, the routes, the jobs and the Now card.
"""
