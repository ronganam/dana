"""The phone addon's sample data for the demo household (core/demo.py): calls
already transcribed and analysed (with a suggestion waiting) and notifications
already sorted, so nothing is sent to the model or a phone."""

import hashlib
import json
import uuid
from typing import Any, Tuple

from core.addons import DemoContext
from core.db import get_db_connection
from core.users import User
from addons.phone.addon import addon

# (minutes ago, caller, number, direction, seconds, title, summary, transcript, importance, suggestion or None)
CALLS: Tuple[Tuple[Any, ...], ...] = (
    (95, "Dr. Morgan Hale", "+15555550102", "incoming", 74, "Dentist: moving the check-up",
     "The clinic asked to move Thursday's check-up to next Monday at 9:30.",
     "Hi, this is Dr. Hale's office. We need to move your Thursday check-up. Could you come next Monday at "
     "9:30 instead? — Monday works. — Great, see you then.", "high",
     {"title": "Dentist check-up on Monday at 9:30", "group": "Personal", "priority": "high"}),
    (60 * 26, "Riley Brooks", "+15555550101", "outgoing", 312, "Climbing on Tuesday",
     "Planned Tuesday's climbing session; Riley will bring the spare harness.",
     "Hey Riley, still on for Tuesday? — Sure, 7 pm at the gym. I'll bring the spare harness. — Perfect.",
     "normal", None),
    (60 * 50, "Grandma Rose", "+15555550104", "incoming", 540, "Sunday call with Grandma",
     "Grandma finished the big crossword and asked for photos from the trip.",
     "Hello dear! I finished the Sunday crossword. Will you send me the photos from your trip? — Of course, "
     "tonight.", "normal",
     {"title": "Send Grandma the trip photos", "group": "Home", "priority": "medium"}),
)

# (minutes ago, package, app, title, text, sender, category, priority score, bucket)
NOTIFICATIONS: Tuple[Tuple[Any, ...], ...] = (
    (8, "org.example.messages", "Messages", "Riley Brooks", "Can you pick up the climbing chalk on the way?",
     "Riley Brooks", "personal_message", 3, "needs_you"),
    (25, "org.example.bank", "Example Bank", "Card payment", "Your card was charged $42.80 at Green Market.",
     "", "money", 2, "normal"),
    (40, "org.example.parcel", "Parcel Tracker", "Out for delivery", "Your package arrives today between 2 and 4 pm.",
     "", "delivery", 3, "needs_you"),
    (70, "org.example.calendar", "Calendar", "Design review", "Tomorrow 10:00 with Taylor Quinn.",
     "", "calendar_reminder", 2, "normal"),
    (120, "org.example.shop", "Shop", "48 hours only", "Up to 60% off garden furniture.", "", "promotion", 0,
     "dismiss"),
    (180, "org.example.news", "Daily News", "Morning headlines", "The city opens two new bike lanes.", "", "news", 1,
     "dismiss"),
)
PRIORITY = {0: "low", 1: "low", 2: "normal", 3: "high", 4: "urgent"}


@addon.demo
def demo_phone(user: User, ctx: DemoContext) -> int:
    """Three calls (one suggestion each where there is something to do) and six
    sorted notifications for the first demo user; one notification for the others."""
    rows = 0
    with get_db_connection() as conn:
        if ctx.index == 0:
            for ago, caller, number, direction, seconds, title, summary, transcript, importance, s in CALLS:
                call_id = str(uuid.uuid4())
                when = ctx.ago(minutes=ago).isoformat(timespec="seconds")
                analysis = {"participants": [caller], "key_facts": [summary],
                            "action_items": [{"text": s["title"], "owner": "me", "kind": "task"}] if s else []}
                incoming = direction == "incoming"
                # No recording is stored: the audio counts as deleted (nothing to play or purge).
                conn.execute("INSERT INTO phone_calls (id, user_id, filename, caller, receiver, call_timestamp, "
                             "transcript, summary, title, duration_seconds, labels, analysis, importance, status, "
                             "direction, phone_number, audio_deleted_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'ready', ?, ?, ?)",
                             (call_id, user.id, f"demo-{call_id}.m4a", caller if incoming else "Me",
                              "Me" if incoming else caller, when, transcript, summary, title, seconds,
                              json.dumps(analysis), importance, direction, number, when))
                rows += 1
                if s:
                    payload = {"title": s["title"], "description": f"From the call with {caller}.",
                               "due_date": None, "priority": s["priority"], "group": s["group"], "triggers": {},
                               "confidence": 0.9, "evidence": ""}
                    conn.execute("INSERT INTO call_suggestions (id, user_id, call_id, source_type, kind, payload, "
                                 "status) VALUES (?, ?, ?, 'call', 'task', ?, 'pending')",
                                 (str(uuid.uuid4()), user.id, call_id, json.dumps(payload)))
                    rows += 1
        wanted = NOTIFICATIONS if ctx.index == 0 else NOTIFICATIONS[3:4]
        for ago, package, app, title, text, sender, category, score, bucket in wanted:
            posted = ctx.ago(minutes=ago).isoformat(timespec="seconds")
            digest = hashlib.sha256(f"demo|{package}|{title}|{text}".encode()).hexdigest()
            triage = {"model": "demo", "low_engagement": False, "rule": None}
            conn.execute("INSERT INTO notifications (id, user_id, package, app_name, title, text, sender, posted_at, "
                         "content_hash, status, triage_status, category, priority, priority_score, bucket, triage, "
                         "seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'done', ?, ?, ?, ?, ?, ?)",
                         (str(uuid.uuid4()), user.id, package, app, title, text, sender or None, posted, digest,
                          category, PRIORITY[score], float(score), bucket, json.dumps(triage),
                          None if bucket == "needs_you" else posted))
            rows += 1
        conn.commit()
    return rows
