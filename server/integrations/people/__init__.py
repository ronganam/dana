"""People integration: who each user talks to, built from their calls and
messages (docs/people.md, docs/access.md "Kinds").

``values`` (tuning, normalisation, formatting), ``activity`` (every read and
write of calls / notifications; whose they are is decided in ``calls_sql`` /
``notifications_sql``), ``store`` (each user's people and numbers, lookups),
``linking`` (calls / notifications → people), ``merge``, ``person_tasks``
(tasks per person), ``cadence`` (contact cadence, drifting, the People list),
``briefs`` (model briefs per user, the loop), ``detail`` (a person in full,
the call card, the context chat), ``api`` (``/api/people*`` routes), ``tools``.
"""

from integrations.people.kind import people
from integrations.people import tools  # noqa: F401 — registers the people tools

__all__ = ["people"]
