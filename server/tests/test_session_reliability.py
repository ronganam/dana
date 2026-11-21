"""Sessions: create / list / delete, history formatting and the stored history."""

import asyncio
import os
import uuid
import sys

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("session_test")

from core import db, users
from addons.tasks.groups import ensure_default_groups_for_everyone

db.migrate()
users.bootstrap()
ensure_default_groups_for_everyone()
UID = users.first_admin().id
from core.session import (
    create_new_session_db,
    get_session_state,
    list_all_sessions,
    delete_session_from_db,
    format_session_history
)

async def test_session_management():
    print("Testing server-side session management...")
    
    # 1. Create a session
    sess_id = f"test_{uuid.uuid4()}"
    print(f"Creating session: {sess_id}")
    state = create_new_session_db(UID, sess_id, "Test Chat Title")
    assert state is not None, "Failed to create session in DB"
    assert state["session_id"] == sess_id, "Session ID mismatch"
    assert state["status"] == "idle", "Default status should be idle"
    assert state["title"] == "Test Chat Title", "Title mismatch"
    print("✓ Session created successfully")

    # 2. Fetch session detail
    print("Fetching session detail...")
    detail = get_session_state(UID, sess_id)
    assert detail is not None, "Failed to fetch session detail"
    assert detail["session_id"] == sess_id
    assert detail["status"] == "idle"
    print("✓ Session detail fetched successfully")

    # 3. List sessions
    print("Listing all sessions...")
    sessions = list_all_sessions(UID)
    found = any(s["session_id"] == sess_id for s in sessions)
    assert found, "Created session not found in list"
    print("✓ Session found in sessions list")

    # 4. Test formatting history
    print("Testing history formatting...")
    history = [
        {"role": "user", "content": "hello assistant"},
        {"role": "assistant", "content": "hello user", "tool_calls": [
            {"id": "tc_test_123", "type": "function", "function": {"name": "list_tasks", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "tc_test_123", "name": "list_tasks", "content": "[]"}
    ]
    formatted = format_session_history(history)
    assert len(formatted) == 3, f"Expected 3 formatted messages, got {len(formatted)}"
    assert formatted[0]["sender"] == "You"
    assert formatted[0]["message"] == "hello assistant"
    assert formatted[1]["sender"] == "Assistant"
    assert formatted[2]["sender"] == "Tool"
    assert formatted[2]["toolCall"]["toolName"] == "list_tasks"
    assert formatted[2]["toolCall"]["response"] == "[]"
    print("✓ Message history formatting behaves correctly")

    # 5. Delete session
    print("Deleting session...")
    delete_session_from_db(UID, sess_id)
    after_delete = get_session_state(UID, sess_id)
    assert after_delete is None, "Session was not deleted successfully"
    print("✓ Session deleted successfully")

    print("\nAll session reliability DB tests PASSED!")

if __name__ == "__main__":
    asyncio.run(test_session_management())
