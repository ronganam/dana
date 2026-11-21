"""The ``whatsapp`` integration kind (WhatsApp through WAHA)."""

from integrations.framework import ADDRESS, NAME, TELEGRAM_CHAT, Field, Integration, PromptPart
from integrations.whatsapp import accounts, client
from integrations.whatsapp.config import KIND, VOICE_CHAT

# No owner-specific defaults here: the first admin's address, key and session
# are imported by integrations/bootstrap.py. The account (the session's own
# WhatsApp id, routing inbound events) is learned from WAHA (accounts.py).
whatsapp = Integration(
    KIND,
    credentials=(
        Field("url", "WAHA API base URL", type=ADDRESS),
        Field("api_key", "WAHA API key", required=False),
        Field("session", "WAHA session name", type=NAME),
    ),
    settings=(
        Field(VOICE_CHAT, "Telegram chat inbound voice-note transcripts go to (None: none).", type=TELEGRAM_CHAT),
    ),
    description="WhatsApp through WAHA",
    on_connect=accounts.on_connect,
    on_credentials_change=accounts.on_credentials_change,
    validate=accounts.validate,
    blocked=accounts.blocked,
    check=client.check,
    prompt_parts=(
        PromptPart("tools", "WhatsApp messaging", rank=50),
        PromptPart("id_examples", "contact chat IDs", rank=30),
        PromptPart("send_via", " (WhatsApp)"),
        PromptPart("choice_examples", "which contact to message", rank=10),
    ),
)
