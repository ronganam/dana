"""The watches addon: "when X happens, do Y" compiled into watch specs and fired by a cheap engine (docs/watches.md).

- :mod:`.spec` — the spec: validation, time rules, cheap matching, dry runs;
- :mod:`.store` — watches, their fires and their trace in the database;
- :mod:`.trace` — each event a watch listens to and what became of it (stored, capped);
- :mod:`.interest` — the values active watches can fire on, told to ``core.events``;
- :mod:`.identity` — who the user is and how to reach them, for the compiler and agent runs;
- :mod:`.engine` — the live engine (the events tap, the time pass);
- :mod:`.pipeline` — what the engine did, in memory, with live subscribers;
- :mod:`.compiler` — plain words to a spec;
- :mod:`.actions` — what a fire does;
- :mod:`.service` — create / change / describe, shared by the tools and the routes;
- :mod:`.tools`, :mod:`.api`, :mod:`.hooks` — the tools, the routes, the jobs and the Now card.
"""
