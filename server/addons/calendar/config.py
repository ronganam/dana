"""Google Calendar configuration of one user: the addon's name and its setting
names (docs/access.md, "Addons"). The refresh token is the connection's
credential; nothing here reads env (the Google sign-in sets them)."""

ADDON = "calendar"

# Setting names.
DEFAULT_CALENDAR = "calendar_id"      # the calendar new events go to
SELECTED = "selected_calendars"       # ids of the calendars shown and searched (none: the default calendar)
