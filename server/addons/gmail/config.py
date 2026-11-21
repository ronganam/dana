"""Gmail configuration: the addon's name and the limits its tools keep
(docs/gmail.md). The refresh token is the connection's credential and the
account's address its ``account``; nothing here reads env."""

ADDON = "gmail"
SERVICE = "Gmail"  # the service's name in a failure reason

# Messages one search / unread listing returns at most.
MAX_RESULTS = 25
# Messages one archive / mark / trash call changes at most.
MAX_BATCH = 30
# Recipients (to + cc + bcc) one message goes to at most.
MAX_RECIPIENTS = 20
# A tool result stays under this many characters (below core/agent.py's
# MAX_TOOL_RESULT_CHARS, so the agent loop never cuts one in the middle).
RESULT_MAX_CHARS = 11_000
# Characters of a body ``read_email`` shows, and of the bodies of one thread together.
BODY_MAX_CHARS = 6000
THREAD_BODY_CHARS = 8000
# A thread shows its newest messages, each with at least / at most this much of its body.
THREAD_MAX_MESSAGES = 20
THREAD_MESSAGE_MIN_CHARS = 300
THREAD_MESSAGE_MAX_CHARS = 1500
# A subject and a body the tools send at most (characters).
SUBJECT_MAX_CHARS = 500
OUTGOING_BODY_MAX_CHARS = 50_000
