"""The ``calendar`` addon (Google Calendar)."""

from core.addons import Field, Addon, PromptPart
from addons.calendar import client
from addons.calendar.config import DEFAULT_CALENDAR, ADDON, SELECTED
from addons.google_account import oauth

SCOPES = ("https://www.googleapis.com/auth/calendar",)

# The OAuth client (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET) is server-wide env;
# the refresh token comes from the Google sign-in (addons/google_account).
addon = Addon(
    ADDON, order=100,
    credentials=(Field("refresh_token", "Google OAuth refresh token"),),
    settings=(Field(DEFAULT_CALENDAR, "Calendar new events go to", "primary"),
              Field(SELECTED, "Calendars shown and searched (ids); none chosen: the default calendar")),
    description="Google Calendar",
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
