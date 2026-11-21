"""People filled in from the user's Google contacts (docs/contacts.md, "People").

After every sync, and after Dana added or changed a contact: each of the
user's people whose number is on exactly one contact (or on the contact it is
already linked to) takes what that contact has and the person lacks. What is
taken, and what is never changed, is decided by
``addons.people.store.fill_from_contact``. No person is ever created
from a contact, and only a user with ``people`` has anything filled.
"""

import logging
from typing import List

from core.db import get_db_connection
from core.users import User
from addons.contacts import store
from addons.people import activity as people_activity, store as people_store

logger = logging.getLogger(__name__)


def fill_people(user: User) -> int:
    """Fill ``user``'s people from their contacts copy. Returns how many
    people are linked to a contact now (0 without ``people`` or ``contacts``)."""
    if not people_store.has_people(user) or not store.has_contacts(user):
        return 0
    linked = 0
    recheck = False
    with get_db_connection() as conn:
        contacts = {c["id"]: c for c in store.all_contacts(user, conn)}
        by_number = store.by_number(user, conn)
        numbers = people_store.phones_by_person(conn, user)
        for person in people_store.rows(conn, user):
            matches: List[str] = []
            for number in numbers.get(person["id"], []):
                matches += [rid for rid in by_number.get(number, []) if rid not in matches]
            current = person["contact_resource"]
            chosen = current if current in matches else matches[0] if len(matches) == 1 else ""
            if not chosen:
                if current and current not in contacts:
                    people_store.unlink_contact(conn, user, person["id"])  # the contact is gone from Google
                linked += 1 if current in contacts else 0
                continue
            contact = contacts[chosen]
            recheck = people_store.fill_from_contact(
                conn, user, person, resource=chosen, name=contact["name"], phones=store.numbers_of(contact),
                birthday=contact["birthday"], emails=[e["value"] for e in contact["emails"]]) or recheck
            linked += 1
        if recheck:
            people_activity.recheck_unmatched(conn, user)  # a new name or number may match calls / messages
        conn.commit()
    return linked


def forget(user: User) -> None:
    """The user's contacts copy is gone: their people keep what was filled in, not the link."""
    people_store.unlink_contacts(user)
