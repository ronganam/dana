"""Google Calendar configuration of one user: the kind's name and its setting
names (docs/access.md, "Kinds"). The refresh token is the connection's
credential; nothing here reads env (the first admin's values are imported
once by ``integrations/bootstrap.py``)."""

KIND = "calendar"

# Setting names.
DEFAULT_CALENDAR = "calendar_id"      # the calendar new events go to
SELECTED = "selected_calendars"       # ids of the calendars shown and searched (none: the default calendar)
