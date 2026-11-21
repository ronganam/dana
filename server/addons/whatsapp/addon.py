"""The ``whatsapp`` addon (WhatsApp through WAHA)."""

from core.addons import ADDRESS, NAME, TELEGRAM_CHAT, Field, Addon, PromptPart, LlmJob
from addons.whatsapp import accounts, client
from addons.whatsapp.config import ADDON, VOICE_CHAT

# No owner-specific defaults here: the address, key and session are set on the
# Addons page or with bin/dana. The account (the session's own
# WhatsApp id, routing inbound events) is learned from WAHA (accounts.py).
addon = Addon(
    ADDON, order=90, modules=('latest',), llm_jobs={"whatsapp_match": LlmJob("fast", "WhatsApp name matching")},
    title="WhatsApp", icon="message-circle",
    credentials=(
        Field("url", "WAHA API base URL", type=ADDRESS, label="WAHA address",
              help="The address your WAHA server answers at, the one its dashboard opens on.",
              url="https://github.com/devlikeapro/waha", placeholder="http://waha:3000"),
        Field("api_key", "WAHA API key", required=False, label="WAHA API key",
              help="The API key your WAHA server was started with; leave it empty if it has none."),
        Field("session", "WAHA session name", type=NAME, label="Session name",
              help="The session's name in WAHA's dashboard, the one you linked your phone to.",
              placeholder="default"),
    ),
    settings=(
        Field(VOICE_CHAT, "Telegram chat inbound voice-note transcripts go to (None: none).", type=TELEGRAM_CHAT,
              label="Send voice-note transcripts to",
              help="Optional: your Telegram user id, to get the voice notes you receive as text from Dana's bot.",
              placeholder="123456789"),
    ),
    description="Read and send WhatsApp messages, voice notes as text",
    features=("Read your chats and find messages and files",
              "Send messages and forward files (asks you first)",
              "Turns the voice notes you receive into text"),
    setup=("Run a WAHA server (a WhatsApp Web bridge) that Dana's server can reach; the link on the address "
           "below shows how.",
           "In WAHA's dashboard, start a session and scan its QR code with WhatsApp on your phone (Settings → "
           "Linked devices).",
           "Come back here with WAHA's address, its API key and the session's name."),
    examples=("What did Noa write me on WhatsApp today?", "Tell Avi on WhatsApp that I'm running late"),
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
