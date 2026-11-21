"""The ``whatsapp`` addon (WhatsApp through WAHA)."""

from core.addons import ADDRESS, NAME, TELEGRAM_CHAT, Field, Addon, PromptPart, LlmJob
from addons.whatsapp import accounts, client
from addons.whatsapp.config import ADDON, VOICE_CHAT

# No owner-specific defaults here: the address, key and session are set on the
# Addons page or with bin/dana. The account (the session's own
# WhatsApp id, routing inbound events) is learned from WAHA (accounts.py).
addon = Addon(
    ADDON, order=90, modules=('latest',), llm_jobs={"whatsapp_match": LlmJob("fast", "WhatsApp name matching")},
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
    test=client.check,
    prompt_parts=(
        PromptPart("tools", "WhatsApp messaging", rank=50),
        PromptPart("id_examples", "contact chat IDs", rank=30),
        PromptPart("send_via", " (WhatsApp)"),
        PromptPart("choice_examples", "which contact to message", rank=10),
    ),
)
