"""One user's Google Contacts over the People API, with that user's own
connection (the refresh token from its credentials; the OAuth client is the
server's, ``integrations/google_account/oauth.py``).

Every function takes the user's :class:`~integrations.framework.Connection`
and blocks (run it in a thread). A service object is built per call: they are
not thread-safe, and building one is cheap.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.discovery import build

from integrations.contacts.config import PERSON_FIELDS
from integrations.framework import Connection
from integrations.google_account import oauth

logger = logging.getLogger(__name__)

ME = "people/me"
PAGE_SIZE = 1000
SERVICE_NAME = "Google Contacts"


class SyncTokenExpired(Exception):
    """Google no longer accepts the sync token: the copy is read in full again."""


class ContactChanged(Exception):
    """The contact was changed in Google since its etag was stored."""


class ContactGone(Exception):
    """Google has no such contact (deleted there)."""


def service(conn: Connection) -> Any:
    """A People API client for the connection.

    Raises:
        ValueError: the server has no OAuth client or the connection no refresh token.
    """
    return build("people", "v1", credentials=oauth.credentials(conn))


def _status(error: Exception) -> Optional[int]:
    return getattr(getattr(error, "resp", None), "status", None)


def _text(error: Exception) -> str:
    """What Google said (the error body and message), lower case."""
    content = getattr(error, "content", b"")
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    return f"{content} {error}".lower()


def _token_expired(error: Exception) -> bool:
    text = _text(error)
    return _status(error) == 410 or (_status(error) == 400 and ("expired_sync_token" in text or "sync token" in text))


def failure_reason(error: Exception) -> str:
    """A short reason (never a token or a request URL) for a failed request."""
    return oauth.failure_reason(error, SERVICE_NAME)


def list_changes(conn: Connection, sync_token: str = "") -> Tuple[List[Dict[str, Any]], str]:
    """The user's contacts changed since ``sync_token`` (every contact without
    one), as Google's person dicts (a deleted one has ``metadata.deleted``),
    and the sync token for the next call.

    Raises:
        SyncTokenExpired: ``sync_token`` is no longer accepted.
    """
    connections = service(conn).people().connections()
    people: List[Dict[str, Any]] = []
    page: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"resourceName": ME, "personFields": PERSON_FIELDS, "pageSize": PAGE_SIZE,
                                  "requestSyncToken": True}
        if sync_token:
            params["syncToken"] = sync_token
        if page:
            params["pageToken"] = page
        try:
            response = connections.list(**params).execute()
        except Exception as e:
            if sync_token and _token_expired(e):
                raise SyncTokenExpired() from e
            raise
        people.extend(response.get("connections") or [])
        page = response.get("nextPageToken")
        if not page:
            return people, str(response.get("nextSyncToken") or "")


def get(conn: Connection, resource_name: str) -> Dict[str, Any]:
    """One contact as Google has it now.

    Raises:
        ContactGone: Google has no such contact.
    """
    try:
        return service(conn).people().get(resourceName=resource_name, personFields=PERSON_FIELDS).execute()
    except Exception as e:
        if _status(e) == 404:
            raise ContactGone(resource_name) from e
        raise


def create(conn: Connection, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Add a contact with ``fields`` (People API person fields); returns it as Google stored it."""
    return service(conn).people().createContact(body=fields, personFields=PERSON_FIELDS).execute()


def update(conn: Connection, resource_name: str, etag: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Replace ``fields`` (People API person fields, each in full) of a contact
    whose stored etag is ``etag``; returns it as Google stored it.

    Raises:
        ContactChanged: the contact changed in Google since ``etag``.
        ContactGone: Google has no such contact.
    """
    body = {"resourceName": resource_name, "etag": etag, **fields}
    try:
        return service(conn).people().updateContact(
            resourceName=resource_name, updatePersonFields=",".join(sorted(fields)), personFields=PERSON_FIELDS,
            body=body).execute()
    except Exception as e:
        if _status(e) == 404:
            raise ContactGone(resource_name) from e
        if _status(e) in (400, 412) and ("etag" in _text(e) or "failed_precondition" in _text(e)
                                         or "failedprecondition" in _text(e)):
            raise ContactChanged(resource_name) from e
        raise


def _check_blocking(conn: Connection) -> Optional[str]:
    try:
        service(conn).people().connections().list(resourceName=ME, personFields="names", pageSize=1).execute()
    except Exception as e:
        return failure_reason(e)
    return None


async def check(conn: Connection) -> Optional[str]:
    """``Integration(check=)``: one cheap request (the first contact's name,
    refreshing the access token): None when it works, else a short reason
    (never a token)."""
    return await asyncio.to_thread(_check_blocking, conn)
