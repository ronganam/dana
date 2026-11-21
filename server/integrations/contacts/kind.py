"""The ``contacts`` integration kind (Google Contacts)."""

from integrations.contacts import client, sync
from integrations.contacts.config import KIND
from integrations.framework import Field, Integration, PromptPart
from integrations.google_account import oauth

SCOPES = ("https://www.googleapis.com/auth/contacts",)

# ``{user}``: what the agent's instructions call the user (core/agent.py).
CONTACTS_GUIDE = (
    "When you need someone's phone number or email address (to message, call or invite them, or because {user} "
    "asks for it), look them up with search_contacts instead of asking {user}. add_contact and update_contact "
    "change {user}'s Google Contacts and are confirmed through the Approve button like a message: just call them. "
)

# The OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) is server-wide env;
# the refresh token comes from the Google sign-in (integrations/google_account).
contacts = Integration(
    KIND,
    credentials=(Field("refresh_token", "Google OAuth refresh token"),),
    description="Google Contacts",
    check=client.check,
    connect_url=oauth.CONNECT_PATH,
    on_connect=sync.on_connect,
    on_credentials_change=sync.on_credentials_change,
    on_change=sync.on_change,
    prompt_parts=(
        PromptPart("tools", "contact lookup (Google Contacts)", rank=55),
        PromptPart("read_examples", "looking up a contact", rank=25),
        PromptPart("id_examples", "contact IDs", rank=45),
        PromptPart("guides", CONTACTS_GUIDE, rank=35),
    ),
)
oauth.register(KIND, SCOPES)
