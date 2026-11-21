"""Google Contacts configuration: the kind's name, what is read of a contact
and how often every user's copy is synced (docs/contacts.md). The refresh
token is the connection's credential; the kind has no settings."""

import os

KIND = "contacts"

# The People API field mask of every read (the same on every sync request:
# Google requires it to match the request that issued the sync token).
PERSON_FIELDS = "names,nicknames,phoneNumbers,emailAddresses,birthdays,organizations"

# Minutes between two syncs of every user's contacts (server-wide; docs/configuration.md).
SYNC_MINUTES = max(1, int(os.getenv("CONTACTS_SYNC_MINUTES", "60")))
