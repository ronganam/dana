"""Money: bank and card transactions (docs/money.md, docs/access.md "Addons").

``config`` (the addon's name, the worker's address, slots and limits),
``companies`` (the banks / cards and their login forms), ``categories`` (Dana's
categories and flows), ``worker`` (the bank-worker sidecar's client),
``sources`` (logins: add / update / remove), ``sync`` (one sync of one
source), ``otp`` (the SMS code broker), ``slots`` / ``schedule`` (when sources
sync), ``ingest`` (storing a sync's result), ``hooks`` (work after each
ingest), ``classify`` (categories, display names, flows and the merchant
memory), ``signals`` (payment notifications from the phone → provisional
transactions), ``rules`` (the user's categories and notes), ``store`` (reads
and payloads), ``periods`` (named periods), ``queries`` (the numbers),
``insights`` (recurring and unusual charges), ``tools`` (Dana's money tools),
``api``.
"""

from addons.money import addon  # noqa: F401 — declares the addon
from addons.money import tools  # noqa: F401 — registers the money tools
