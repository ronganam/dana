"""The ``phone`` integration kind (Phone)."""

from integrations.framework import Integration, PromptPart

CALLS_GUIDE = (
    "When asked to retrieve details from past calls or voice memos:\n"
    "  1. If the user asks about 'recent calls' in general, use list_recent_calls first.\n"
    "  2. If the user asks about calls with a specific person (e.g. 'what did Mom say?'), "
    "use search_calls with just the person's name (e.g. 'אמא') or list_recent_calls to find calls from them.\n"
    "  3. If the user asks about a specific topic (e.g. 'what did they say about the fish?'), "
    "search_calls can combine person + topic as separate words (e.g. 'אמא דגים').\n"
    "  4. Once you have a call ID, use get_call_transcript to read the full transcript and answer specific questions. "
)

phone = Integration("phone", description="The Android phone: notifications Inbox, calls and suggestions, push",
                    prompt_parts=(
                        PromptPart("tools", "call/voice memo history", rank=60),
                        PromptPart("read_examples", "searching calls", rank=20),
                        PromptPart("id_examples", "phone numbers", rank=20),
                        PromptPart("undoable", "dismissing or snoozing notifications", rank=60),
                        PromptPart("guides", CALLS_GUIDE, rank=20),
                    ))
