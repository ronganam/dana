"""Google Contacts tools: search and read the user's own copy of their
contacts; add or change a contact in Google only after the user approved
(``confirm``), then store what Google returned (integrations/contacts/store.py)."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from integrations.contacts import client, enrich, store
from integrations.contacts.kind import contacts
from integrations.framework import Connection
from integrations.people.values import norm_phone, valid_birthday

logger = logging.getLogger(__name__)


def _phone_text(phone: Dict[str, Any]) -> str:
    shown = f"+{phone['number']}" if phone.get("number") else str(phone.get("value") or "")
    return shown + (f" ({phone['type']})" if phone.get("type") else "")


def _email_text(email: Dict[str, Any]) -> str:
    return str(email.get("value") or "") + (f" ({email['type']})" if email.get("type") else "")


def _line(contact: store.Contact) -> str:
    bits = [contact["name"] or "(no name)"]
    if contact["phones"]:
        bits.append(", ".join(_phone_text(p) for p in contact["phones"]))
    if contact["emails"]:
        bits.append(", ".join(str(e["value"]) for e in contact["emails"]))
    if contact["organisation"] and contact["organisation"] != contact["name"]:
        bits.append(contact["organisation"])
    return f"- [{contact['id']}] " + " · ".join(bits)


def _full(contact: store.Contact) -> str:
    out = [f"{contact['name'] or '(no name)'} [{contact['id']}]"]
    if contact["other_names"]:
        out.append("Also known as: " + ", ".join(contact["other_names"]))
    if contact["phones"]:
        out.append("Phones: " + ", ".join(_phone_text(p) for p in contact["phones"]))
    if contact["emails"]:
        out.append("Emails: " + ", ".join(_email_text(e) for e in contact["emails"]))
    if contact["birthday"]:
        out.append(f"Birthday: {contact['birthday']}")
    work = " — ".join(x for x in (contact["organisation"], contact["job_title"]) if x)
    if work:
        out.append(f"Organisation: {work}")
    return "\n".join(out)


def _not_read_yet(conn: Connection) -> Optional[str]:
    if store.synced_at(conn.user) is None:
        return "The contacts have not been read from Google yet; try again in a minute."
    return None


@contacts.tool()
def search_contacts(conn: Connection, query: str, limit: int = 10) -> str:
    """Search the user's Google Contacts by name (Hebrew or English, partial is fine), phone number or email address. Use it to find someone's phone number or email instead of asking the user.

    Args:
        query: A name or part of it, a phone number or an email address.
        limit: The maximum number of contacts to return (default 10).
    """
    found = store.search(conn.user, query, limit)
    if not found:
        return _not_read_yet(conn) or f"No contacts matching '{query}'."
    return "\n".join(_line(c) for c in found)


@contacts.tool()
def get_contact(conn: Connection, contact_id: str) -> str:
    """Everything one Google contact has: names, phone numbers, email addresses, birthday and organisation.

    Args:
        contact_id: The contact's id as search_contacts returned it (e.g. 'people/c123').
    """
    contact = store.get(conn.user, contact_id)
    if contact is None:
        return _not_read_yet(conn) or f"No contact with id '{contact_id}'. Use search_contacts to find it."
    return _full(contact)


def _name_field(name: str) -> List[Dict[str, Any]]:
    return [{"unstructuredName": name}]


def _birthday_field(birthday: str) -> List[Dict[str, Any]]:
    """``YYYY-MM-DD`` / ``MM-DD`` as the People API's birthdays ('' removes it; ValueError when invalid)."""
    value = valid_birthday(birthday)
    if not value:
        return []
    parts = [int(x) for x in value.split("-")]
    date = {"year": parts[0], "month": parts[1], "day": parts[2]} if len(parts) == 3 else \
        {"month": parts[0], "day": parts[1]}
    return [{"date": date}]


def _organisation_field(name: str, title: str) -> List[Dict[str, Any]]:
    entry = {k: v for k, v in (("name", name), ("title", title)) if v}
    return [entry] if entry else []


def _typed(value: str, kind: str) -> Dict[str, Any]:
    return {"value": value, "type": kind} if kind else {"value": value}


def _changes(contact: store.Contact, *, name: Optional[str], add_phone: Optional[str], remove_phone: Optional[str],
             add_email: Optional[str], remove_email: Optional[str], birthday: Optional[str],
             organisation: Optional[str]) -> Dict[str, Any]:
    """The People API fields (each in full) that turn ``contact`` into what was asked.

    Raises:
        ValueError: a value that cannot be used (the message says which).
    """
    fields: Dict[str, Any] = {}
    if name is not None and name.strip() and name.strip() != contact["name"]:
        fields["names"] = _name_field(name.strip())
    if add_phone or remove_phone:
        phones = list(contact["phones"])
        if remove_phone:
            gone = norm_phone(remove_phone)
            kept = [p for p in phones if not ((gone and p.get("number") == gone) or p.get("value") == remove_phone)]
            if len(kept) == len(phones):
                raise ValueError(f"the contact has no number {remove_phone}")
            phones = kept
        if add_phone:
            if not any(ch.isdigit() for ch in add_phone):
                raise ValueError(f"{add_phone} is not a phone number")
            new = norm_phone(add_phone)
            if not any((new and p.get("number") == new) or p.get("value") == add_phone for p in phones):
                phones.append({"value": add_phone.strip(), "type": ""})
        if phones != contact["phones"]:
            fields["phoneNumbers"] = [_typed(p["value"], p.get("type") or "") for p in phones]
    if add_email or remove_email:
        emails = list(contact["emails"])
        if remove_email:
            kept = [e for e in emails if str(e["value"]).lower() != remove_email.strip().lower()]
            if len(kept) == len(emails):
                raise ValueError(f"the contact has no email {remove_email}")
            emails = kept
        if add_email:
            if "@" not in add_email:
                raise ValueError(f"{add_email} is not an email address")
            if not any(str(e["value"]).lower() == add_email.strip().lower() for e in emails):
                emails.append({"value": add_email.strip(), "type": ""})
        if emails != contact["emails"]:
            fields["emailAddresses"] = [_typed(e["value"], e.get("type") or "") for e in emails]
    if birthday is not None and valid_birthday(birthday) != contact["birthday"]:
        fields["birthdays"] = _birthday_field(birthday)
    if organisation is not None and organisation.strip() != contact["organisation"]:
        fields["organizations"] = _organisation_field(organisation.strip(), contact["job_title"])
    return fields


@contacts.tool(is_write=True, confirm=True)
async def update_contact(conn: Connection, contact_id: str, name: Optional[str] = None,
                         add_phone: Optional[str] = None, remove_phone: Optional[str] = None,
                         add_email: Optional[str] = None, remove_email: Optional[str] = None,
                         birthday: Optional[str] = None, organisation: Optional[str] = None) -> str:
    """Change an existing contact in the user's Google Contacts (the user approves it first). Only the fields given change.

    Args:
        contact_id: The contact's id as search_contacts returned it (e.g. 'people/c123').
        name: A new full name.
        add_phone: A phone number to add.
        remove_phone: One of the contact's phone numbers to remove.
        add_email: An email address to add.
        remove_email: One of the contact's email addresses to remove.
        birthday: YYYY-MM-DD, or MM-DD when the year is unknown; an empty string removes it.
        organisation: The company or organisation they belong to; an empty string removes it.
    """
    user = conn.user
    contact = store.get(user, contact_id)
    if contact is None:
        return f"No contact with id '{contact_id}'. Use search_contacts to find it."
    try:
        fields = _changes(contact, name=name, add_phone=add_phone, remove_phone=remove_phone, add_email=add_email,
                          remove_email=remove_email, birthday=birthday, organisation=organisation)
    except ValueError as e:
        return f"Error: {e}"
    if not fields:
        return f"Nothing to change: {contact['name'] or contact['id']} already has that."
    try:
        person = await asyncio.to_thread(client.update, conn, contact["id"], contact["etag"], fields)
    except client.ContactChanged:
        # Someone changed it in Google since the last sync: read it again, change nothing.
        try:
            store.save(user, await asyncio.to_thread(client.get, conn, contact["id"]))
        except client.ContactGone:
            store.remove(user, contact["id"])
        except Exception as e:
            logger.warning(f"Reading a changed contact again failed: {client.failure_reason(e)}")
        return ("Error: this contact was changed in Google Contacts since it was last read, so nothing was "
                "changed. It has been read again: look at it with get_contact and repeat the change if it is "
                "still needed.")
    except client.ContactGone:
        store.remove(user, contact["id"])
        return "Error: this contact no longer exists in Google Contacts."
    except Exception as e:
        logger.error(f"Updating a Google contact failed: {client.failure_reason(e)}")
        return f"Error: {client.failure_reason(e)}"
    saved = store.save(user, person)
    enrich.fill_people(user)
    return f"Updated in Google Contacts:\n{_full(saved)}"


@contacts.tool(is_write=True, confirm=True)
async def add_contact(conn: Connection, name: str, phone: Optional[str] = None, email: Optional[str] = None,
                      birthday: Optional[str] = None, organisation: Optional[str] = None) -> str:
    """Add a new contact to the user's Google Contacts (the user approves it first). Search first: change an existing contact with update_contact instead of adding a second one.

    Args:
        name: The contact's full name.
        phone: Their phone number.
        email: Their email address.
        birthday: YYYY-MM-DD, or MM-DD when the year is unknown.
        organisation: The company or organisation they belong to.
    """
    user = conn.user
    name = (name or "").strip()
    if not name:
        return "Error: a contact needs a name."
    fields: Dict[str, Any] = {"names": _name_field(name)}
    if phone:
        if not any(ch.isdigit() for ch in phone):
            return f"Error: {phone} is not a phone number."
        number = norm_phone(phone)
        holder = next((c for c in store.all_contacts(user) if number and number in store.numbers_of(c)), None)
        if holder is not None:
            return (f"Error: {holder['name'] or 'a contact'} [{holder['id']}] already has that number; "
                    "change that contact with update_contact.")
        fields["phoneNumbers"] = [{"value": phone.strip()}]
    if email:
        if "@" not in email:
            return f"Error: {email} is not an email address."
        fields["emailAddresses"] = [{"value": email.strip()}]
    try:
        if birthday:
            fields["birthdays"] = _birthday_field(birthday)
    except ValueError as e:
        return f"Error: {e}"
    if organisation and organisation.strip():
        fields["organizations"] = _organisation_field(organisation.strip(), "")
    try:
        person = await asyncio.to_thread(client.create, conn, fields)
    except Exception as e:
        logger.error(f"Adding a Google contact failed: {client.failure_reason(e)}")
        return f"Error: {client.failure_reason(e)}"
    saved = store.save(user, person)
    enrich.fill_people(user)
    return f"Added to Google Contacts:\n{_full(saved)}"
