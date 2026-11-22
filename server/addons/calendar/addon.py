"""The ``calendar`` addon (Google Calendar)."""

from core.addons import Field, Addon, PromptPart, Page, Nav
from addons.calendar import client
from addons.calendar.config import DEFAULT_CALENDAR, ADDON, SELECTED
from addons.google_account import oauth

SCOPES = ("https://www.googleapis.com/auth/calendar",)

# The OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) is server-wide env;
# the refresh token comes from the Google sign-in (addons/google_account).
addon = Addon(
    ADDON, web=(Page("calendar", panel="calendar.js", nav=Nav("Calendar", icon="calendar", order=70, primary=True)),), order=100,
    title="Google Calendar", icon="calendar",
    credentials=(Field("refresh_token", "Google OAuth refresh token", label=oauth.TOKEN_LABEL, help=oauth.TOKEN_HELP),),
    settings=(Field(DEFAULT_CALENDAR, "Calendar new events go to", "primary", label="Calendar for new events",
                    help="Where the events Dana adds go; \"primary\" is your main calendar.", placeholder="primary"),
              Field(SELECTED, "Calendars shown and searched (ids); none chosen: the default calendar",
                    label="Calendars Dana reads",
                    help="Pick from the suggestions, separated by commas; empty: only your main calendar.")),
    description="Your events in chat, on the Now screen and in the briefing",
    features=("Your events in chat, on the Now screen, in the morning briefing and the weekly review",
              "Add and move events, with Undo (asks first when it invites people)"),
    setup=oauth.SETUP,
    examples=("What's on my calendar tomorrow?", "Move my dentist appointment to Thursday at 10"),
    test=client.check,
    connect_url=oauth.CONNECT_PATH,
    prompt_parts=(
        PromptPart("tools", "calendar management", rank=10),
        PromptPart("id_examples", "calendar event IDs", rank=40),
        PromptPart("undoable", "adding or moving calendar events", rank=20),
        PromptPart("routing_heading", "CALENDAR", rank=20),
        PromptPart("routing", "Use add_event for calendar entries like meetings, appointments, or scheduled blocks "
                   "of time that should appear on the user's calendar view. ", rank=20),
    ),
)
oauth.register(ADDON, SCOPES)
