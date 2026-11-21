"""A user's saved WhatsApp contacts (table ``whatsapp_contacts``, migration
``m0011_whatsapp_per_user``): nicknames → chat ids for the name resolver and
the contact search, the contact rows the call analysis lists as known people
(copied from the old ``memories`` table for the first admin), and preferred
contacts (category ``preferred``: the chat a first name means when it matches
several contacts; the first admin's two came from the code). Every read is the
user's own rows only."""
import logging
from typing import List, Optional

from core.db import get_db_connection
from core.users import User
from integrations.framework import store
from integrations.whatsapp.config import KIND

logger = logging.getLogger(__name__)

PREFERRED = "preferred"
# Saved nicknames: a chat id value, not a preferred contact.
_CHAT_ID_VALUE = ("(value LIKE '%@c.us' OR value LIKE '%@g.us' OR value LIKE '%@newsletter') "
                  f"AND COALESCE(category, '') != '{PREFERRED}'")


def resolve_rows(user: User, query_lower: str, query_normalized: str) -> List[dict]:
    """Rows whose key or value matches the query and whose value is a chat id
    (the name resolver's first step), in saved order."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM whatsapp_contacts WHERE user_id = ? "
            f"AND (key LIKE ? OR key LIKE ? OR value LIKE ?) AND {_CHAT_ID_VALUE} ORDER BY id",
            (user.id, f"%{query_lower}%", f"%{query_normalized}%", f"%{query_lower}%")).fetchall()
    return [dict(r) for r in rows]


def search_rows(user: User, query_lower: str, query_normalized: str) -> List[dict]:
    """Rows whose key or value matches the query (either spelling) and whose
    value is a chat id (the contact search), in saved order."""
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT key, value FROM whatsapp_contacts WHERE user_id = ? "
            f"AND (key LIKE ? OR key LIKE ? OR value LIKE ? OR value LIKE ?) AND {_CHAT_ID_VALUE} ORDER BY id",
            (user.id, f"%{query_lower}%", f"%{query_normalized}%", f"%{query_lower}%",
             f"%{query_normalized}%")).fetchall()
    return [dict(r) for r in rows]


def preferred(user: User, name: str) -> Optional[str]:
    """The chat id of the user's preferred contact named exactly ``name`` (lower case), if any."""
    with get_db_connection() as conn:
        row = conn.execute("SELECT value FROM whatsapp_contacts WHERE user_id = ? AND category = ? AND key = ?",
                           (user.id, PREFERRED, name)).fetchone()
    return row["value"] if row else None


def known_people(user: User, limit: int = 15) -> List[str]:
    """``key: value`` lines of the user's contact rows for an analysis prompt
    (empty without the ``whatsapp`` kind)."""
    if not store.is_connected(user, KIND):
        return []
    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT key, value FROM whatsapp_contacts WHERE user_id = ? AND "
                "(category IN ('contact', 'people', 'person', 'family') OR key LIKE '%contact%') "
                "ORDER BY id LIMIT ?", (user.id, limit)).fetchall()
    except Exception as e:
        logger.debug(f"known people lookup failed: {e}")
        return []
    return [f"{r['key']}: {str(r['value'])[:160]}" for r in rows]
