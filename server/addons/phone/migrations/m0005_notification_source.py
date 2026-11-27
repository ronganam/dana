"""Adds ``notifications.source``: where a row came from. ``phone`` (every row so
far: the Android app's notification listener) or ``whatsapp`` (a burst of
WhatsApp messages WAHA delivered, kept out of the Inbox and used only for
triage and suggestions: addons/phone/notifications/whatsapp.py)."""

import sqlite3

VERSION = 5
NAME = "notification_source"


def up(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE notifications ADD COLUMN source TEXT NOT NULL DEFAULT 'phone'")
