"""Money: bank and card transactions (docs/money.md, docs/access.md "Addons").

``config`` (the addon's name, slots and limits), ``providers`` (the extension
point: the companies, reference rates and price checks provider addons
register), ``categories`` (Dana's categories and flows), ``sources`` (logins:
add / update / remove), ``sync`` (one sync of one source, through its
company's provider), ``otp`` (the SMS code broker), ``slots`` / ``schedule``
(when sources sync), ``ingest`` (storing a sync's result), ``portfolio`` /
``meals`` (investment accounts, meal cards), ``after_sync`` (work after each
ingest), ``classify`` (categories, display names, flows and the merchant
memory), ``signals`` (payment notifications from the phone → provisional
transactions), ``rules`` (the user's categories and notes), ``store`` (reads
and payloads), ``periods`` (named periods), ``queries`` (the numbers),
``insights`` (recurring and unusual charges), ``dashboard`` (the dashboards
and the forecast), ``savings`` (ways to save, spending to watch), ``tools``
(Dana's money tools), ``api``.
"""
