"""Every tool whose result carries text written by other people is marked ``untrusted_result``.

Such a result (an email, a chat message, a web page, a notification, a calendar
invitation, a call's other side) may hold instructions aimed at Dana; the flag
makes every ``acts_outside`` tool ask first for the rest of that turn
(core/agent.py, docs/agent-actions.md). The list below names each tool that
returns third-party text; a new tool of that kind is added here with its flag.
Also: no write tool that sends to other people runs without asking.
Run: bin/test test_untrusted_tools
"""

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("untrusted_tools_test")

import addons  # noqa: E402,F401  (loads every official addon)
from core.addons.addon import all_tools, find_tool  # noqa: E402

# Tool -> whose words its result carries.
THIRD_PARTY_TEXT = {
    "web_search": "web pages",
    "find_last_chats": "WhatsApp messages",
    "read_chat_messages": "WhatsApp messages",
    "get_latest_messages": "WhatsApp messages",
    "search_whatsapp_contact_or_chat": "WhatsApp chat and group names chosen by others",
    "list_telegram_unread": "Telegram messages",
    "read_telegram_chat": "Telegram messages",
    "summarize_telegram_unread": "Telegram messages",
    "search_emails": "emails",
    "list_unread_emails": "emails",
    "read_email": "emails",
    "read_email_thread": "emails",
    "search_notifications": "other apps' notifications (messages, emails)",
    "list_notifications": "other apps' notifications",
    "summarize_notifications": "other apps' notifications",
    "search_calendar": "event titles and descriptions from invitations",
    "list_recent_calls": "summaries of what the other side said",
    "search_calls": "summaries and transcripts of calls",
    "get_call_transcript": "the other side's words",
    "get_person": "briefs written from that person's messages",
    "get_knowledge_item": "summaries and transcripts of linked pages, videos and received files",
}

failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


names = {t.name for t in all_tools()}
missing = sorted(set(THIRD_PARTY_TEXT) - names)
check("every listed tool exists (rename the entry with its tool)", not missing, missing)
unflagged = sorted(n for n in THIRD_PARTY_TEXT if n in names and not find_tool(n).untrusted_result)
check("every tool returning third-party text has untrusted_result=True", not unflagged, unflagged)

# The message sources: every read tool of these addons returns others' words.
SOURCES = ("gmail", "telegram_account")
reads = sorted(t.name for t in all_tools() if t.addon in SOURCES and not t.is_write and not t.untrusted_result)
check(f"every read tool of {', '.join(SOURCES)} is untrusted", not reads, reads)

senders = sorted(t.name for t in all_tools()
                 if t.name.startswith(("send_", "forward_", "reply_")) and not t.confirm)
check("every tool that sends to other people asks first (confirm)", not senders, senders)

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
