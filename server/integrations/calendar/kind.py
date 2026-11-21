"""The ``calendar`` integration kind (Google Calendar)."""

from integrations.framework import Field, Integration, PromptPart
from integrations.calendar import client
from integrations.calendar.config import DEFAULT_CALENDAR, KIND, SELECTED
from integrations.google_account import oauth

SCOPES = ("https://www.googleapis.com/auth/calendar",)

# The OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) is server-wide env;
# the refresh token comes from the Google sign-in (integrations/google_account).
calendar = Integration(
    KIND,
    credentials=(Field("refresh_token", "Google OAuth refresh token"),),
    settings=(Field(DEFAULT_CALENDAR, "Calendar new events go to", "primary"),
              Field(SELECTED, "Calendars shown and searched (ids); none chosen: the default calendar")),
    description="Google Calendar",
    check=client.check,
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
oauth.register(KIND, SCOPES)
