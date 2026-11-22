"""The ``phone`` addon (Phone)."""

from core.addons import Addon, PromptPart, LlmJob, Page, Nav, Need, Touches

CALLS_GUIDE = (
    "When asked to retrieve details from past calls or voice memos:\n"
    "  1. If the user asks about 'recent calls' in general, use list_recent_calls first.\n"
    "  2. If the user asks about calls with a specific person (e.g. 'what did Mom say?'), "
    "use search_calls with just the person's name (e.g. 'אמא') or list_recent_calls to find calls from them.\n"
    "  3. If the user asks about a specific topic (e.g. 'what did they say about the fish?'), "
    "search_calls can combine person + topic as separate words (e.g. 'אמא דגים').\n"
    "  4. Once you have a call ID, use get_call_transcript to read the full transcript and answer specific questions. "
)

addon = Addon("phone", web=(Page("inbox", panel="inbox.js", style="inbox.css", nav=Nav("Inbox", icon="inbox", order=30, primary=True)),
                   Page("calls", panel="calls.js", style="calls.css", nav=Nav("Calls", icon="phone", order=90))), order=70, default_on=True, modules=('calls.tools', 'notification_tools'), llm_jobs={"call_transcript": LlmJob("fast", "Call transcription"), "call_analysis": LlmJob("fast", "Call analysis"), "notification_triage": LlmJob("fast", "Notification sorting"), "notification_extract": LlmJob("fast", "Notification → task / event"), "notification_summary": LlmJob("fast", "Inbox summaries")}, title="Phone", icon="smartphone",
    needs=(Need("app", "Dana for Android on your phone, with notification access", "Sends your notifications and call recordings, gets Dana's push"),
           Need("hardware", "a dialer that saves call recordings to Recordings/Call (Samsung's)", "Call transcripts and the tasks and events suggested from them", optional=True),
           Need("env", "GEMINI_API_KEY", "Transcribes call recordings; the default TRANSCRIBE_MODEL is Gemini (another model, its own key)", optional=True),
           Need("env", "FCM_SERVICE_ACCOUNT_JSON", "Direct push to the phone (a Firebase project); without it events wait for the app's next check", optional=True),
           Need("env", "TYPESAFE_API_KEY", "Sorts notifications in the Inbox; without it they stay unsorted", optional=True)),
    touches=Touches(hosts=("fcm.googleapis.com", "oauth2.googleapis.com", "api.typesafe.ai"), acts_outside=True,
                    reads=("calendar", "homeassistant", "knowledge", "people", "tasks", "whatsapp")),
    description="The Android phone: notifications Inbox, calls and suggestions, push",
                    features=("Your phone's notifications sorted in an Inbox, with summaries",
                              "Call recordings transcribed, with suggested tasks and events",
                              "Push notifications from Dana on your phone"),
                    prompt_parts=(
                        PromptPart("tools", "call/voice memo history", rank=60),
                        PromptPart("read_examples", "searching calls", rank=20),
                        PromptPart("id_examples", "phone numbers", rank=20),
                        PromptPart("undoable", "dismissing or snoozing notifications", rank=60),
                        PromptPart("guides", CALLS_GUIDE, rank=20),
                    ))
