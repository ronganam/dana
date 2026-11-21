"""Checks for tool-output truncation and size-bounded history pruning.

Run: bin/test test_history_limits
"""

import os

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("limits_test")

from core import db, session, users
from core.agent import MAX_TOOL_RESULT_CHARS, limit_tool_output
from core.history import MAX_HISTORY_CHARS, MAX_HISTORY_MESSAGES, prune_history

failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    failures += 0 if cond else 1


# --- limit_tool_output ---
check("short output untouched", limit_tool_output("abc") == "abc")
big = "x" * (MAX_TOOL_RESULT_CHARS * 5)
out = limit_tool_output(big)
check("long output truncated with a hint",
      out.startswith("x" * MAX_TOOL_RESULT_CHARS) and "[Output truncated" in out
      and len(out) < MAX_TOOL_RESULT_CHARS + 300)


# --- prune_history (core/history.py) ---
def prune(history: list) -> list:
    return prune_history(history, max_len=MAX_HISTORY_MESSAGES, max_chars=MAX_HISTORY_CHARS)


def turn(i: int, tool_size: int = 10) -> list:
    return [
        {"role": "user", "content": f"q{i}"},
        {"role": "assistant", "tool_calls": [{"id": f"c{i}", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "name": "t", "content": "y" * tool_size},
        {"role": "assistant", "content": f"a{i}"},
    ]


small = [m for i in range(5) for m in turn(i)]
check("short history kept intact", prune(small) == small)

many = [m for i in range(30) for m in turn(i)]
pruned = prune(many)
check("message cap respected", len(pruned) <= 40)
check("pruned history starts at a user message", pruned[0]["role"] == "user")

heavy = [m for i in range(10) for m in turn(i, tool_size=15_000)]
pruned = prune(heavy)
size = sum(len(str(m)) for m in pruned)
check("size budget respected", size <= MAX_HISTORY_CHARS)
check("size-pruned history starts at a user message", pruned[0]["role"] == "user")
check("latest turn always kept", pruned[-1] == heavy[-1])

huge_last = turn(0) + turn(1, tool_size=MAX_HISTORY_CHARS * 2)
pruned = prune(huge_last)
check("over-budget latest turn kept whole", pruned == huge_last[4:])

# --- stored history (session.append_history prunes ordinary sessions) ---
db.migrate()
users.bootstrap()
uid = users.first_admin().id
session.append_history(uid, "tg_1", many)
stored = session.get_history(uid, "tg_1")
check("stored session history bounded the same way", stored == prune(many))

if failures:
    raise SystemExit(f"{failures} check(s) failed")
print("\nAll history/tool-output limit checks passed")
