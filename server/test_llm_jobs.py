"""LLM jobs: each job's default model (Claude; Gemini for audio), env overrides, and Claude's effort / max_tokens shaping (core/llm.py).

Run: python test_llm_jobs.py
"""

import os
import sys

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("llm_jobs_test")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import llm
from core.agent import AgentOrchestrator

results = []


def check(name, condition, detail=""):
    results.append(bool(condition))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


# Defaults: the agent and search on Sonnet, background jobs on Haiku, audio on Gemini.
defaults = {job: llm.model(job) for job in llm._CHAINS}
check("defaults: chat and search on Sonnet", defaults["chat"] == defaults["search"] == llm.SONNET, str(defaults))
check("defaults: background jobs on Haiku",
      all(defaults[j] == llm.HAIKU for j in ("calls", "notifications", "briefs", "shopping", "money", "telegram")), str(defaults))
check("defaults: transcription stays on a model that hears audio", not llm.is_claude(defaults["transcribe"]), defaults["transcribe"])

# Env chains still win.
os.environ["BACKGROUND_MODEL"] = "anthropic/claude-sonnet-5-5"
os.environ["CALL_ANALYSIS_MODEL"] = "anthropic/claude-opus-5-5"
os.environ["MODEL"] = "anthropic/claude-opus-5-5"
check("env: a job's own variable first", llm.model("calls") == "anthropic/claude-opus-5-5")
check("env: then BACKGROUND_MODEL", llm.model("briefs") == "anthropic/claude-sonnet-5-5")
check("env: search falls back to MODEL", llm.model("search") == "anthropic/claude-opus-5-5")
for var in ("BACKGROUND_MODEL", "CALL_ANALYSIS_MODEL", "MODEL"):
    del os.environ[var]

# Claude shaping.
helper = llm.claude_kwargs(llm.SONNET, "chat", {"temperature": 0.0, "max_tokens": 5})
check("claude: chat helpers think at low effort", helper.get("output_config") == {"effort": "low"}, str(helper))
check("claude: a tiny max_tokens leaves room for thinking", helper["max_tokens"] >= 1024, str(helper))
check("claude: a job without a set effort keeps the model's default",
      "output_config" not in llm.claude_kwargs(llm.HAIKU, "notifications", {}))
check("claude: search thinks at medium", llm.claude_kwargs(llm.SONNET, "search", {}).get("output_config") == {"effort": "medium"})
mine = llm.claude_kwargs(llm.SONNET, "chat", {"output_config": {"effort": "high"}})
check("claude: the caller's effort wins", mine["output_config"] == {"effort": "high"}, str(mine))
check("claude: reasoning_effort is left to LiteLLM",
      "output_config" not in llm.claude_kwargs(llm.SONNET, "chat", {"reasoning_effort": "high"}))
plain = {"temperature": 0.0, "max_tokens": 5}
check("other providers: kwargs untouched", llm.claude_kwargs("gemini/gemini-3.1-flash-lite", "chat", plain) == plain)

# Telegram / trigger runs (the server profile) think at medium on Claude.
server = AgentOrchestrator(model=llm.SONNET)._profile(None).completion_kwargs()
check("server profile: Claude at medium effort", server.get("output_config") == {"effort": "medium"}, str(server))
gemini = AgentOrchestrator(model="gemini/server-default")._profile(None).completion_kwargs()
check("server profile: other providers unchanged", gemini == {"model": "gemini/server-default"}, str(gemini))

# Cache breakpoint on the tool list for Claude only.
tools = [{"type": "function", "function": {"name": "a"}}, {"type": "function", "function": {"name": "b"}}]
marked = llm.cacheable_tools(tools, llm.HAIKU)
check("cache: Claude's last tool is a breakpoint", marked[-1].get("cache_control") == {"type": "ephemeral"} and "cache_control" not in marked[0])
check("cache: other providers untouched", llm.cacheable_tools(tools, "gemini/x") is tools)

passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
