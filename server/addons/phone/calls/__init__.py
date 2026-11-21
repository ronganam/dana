"""Phone calls and suggestions of the ``phone`` addon (docs/calls.md), per user.

``values`` (tuning, small helpers), ``labels`` (the label catalog), ``store``
(the user's calls: reads, writes, the API shape), ``upload`` (a recording
from the phone), ``transcription``, ``context`` (what the analysis prompt
knows about the owner), ``analysis`` (the structured model call),
``suggest`` (action items → suggestions, the feedback block), ``actions``
(accept, dismiss, edit, reopen a suggestion), ``processing`` (one call from
upload to ready, as its owner), ``retention`` (audio kept 90 days),
``chat`` (context chats about a call / suggestion / notification),
``tools`` (the agent's call and suggestion tools), ``backfill`` (CLI).

Every call and suggestion belongs to a user (``user_id``, migration
``m0014_calls_per_user``); every read and write here is for one user, and
another user's id answers like one that does not exist.
"""
