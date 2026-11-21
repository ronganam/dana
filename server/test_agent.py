"""The full ReAct loop against the real MODEL (needs its key; not part of bin/test)."""

import asyncio
import logging
import os

from dotenv import load_dotenv

# The real model key comes from .env (this test calls the real MODEL); then
# testenv removes the integration values .env brought, so no real Home
# Assistant / WAHA / Google credentials reach the test database.
load_dotenv()
import testenv  # clean env, temp DB removed at exit (see testenv.py)
testenv.isolate("agent_test")

# Setup logging to see the ReAct loop progression
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("agent-test")

from core import db, users
from integrations.tasks.groups import ensure_default_groups_for_everyone
from integrations import bootstrap as integrations_bootstrap

db.migrate()
users.bootstrap()
integrations_bootstrap.bootstrap()  # the first admin's integrations from env
ensure_default_groups_for_everyone()
from integrations.framework import registry
from core import llm
from core.agent import AgentOrchestrator

OWNER = users.first_admin()


async def run_agent_test():
    if not llm.configured("chat"):
        print(f"\n[!] No API key for {llm.model('chat')} (MODEL); set it in .env to run this test.")
        return

    agent = AgentOrchestrator()
    logger.info(f"Initialized AgentOrchestrator with model: {agent.model}")
    
    # Define a complex query that requires multi-step reasoning:
    # 1. Search calendar for Dentist Appointment (returns venue: Smile Clinic)
    # 2. Search web for Smile Clinic address (returns address: 123 Main St)
    # 3. Formulate final answer containing the address
    user_query = "What is the address of the clinic for my dentist appointment next week?"
    
    logger.info(f"Running agent with query: '{user_query}'")
    
    try:
        with users.acting_as(OWNER):
            result = await agent.run(user_query, [])
        while isinstance(result, dict) and result.get("status") in ("pending_approval", "tool_call"):
            tool_name = result["tool_name"]
            tool_args = result["tool_args"]
            tool_call_id = result["tool_call_id"]
            
            logger.info(f"[Test runner] Executing {tool_name} with {tool_args}...")
            tool_result = await registry.execute(OWNER, tool_name, tool_args)
            tool_result_str = str(tool_result)
            
            with users.acting_as(OWNER):
                result = await agent.resume_run(
                    messages=result["messages"],
                    new_entries=result["new_entries"],
                    iteration=result["iteration"],
                    max_iterations=10,
                    tool_result_str=tool_result_str,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    pending_tool_calls=result["pending_tool_calls"]
                )
            
        response, history = result
        print("\n" + "=" * 40)
        print(f"User Query: {user_query}")
        print("=" * 40)
        print(f"Agent Final Response:\n{response}")
        print("=" * 40)
        print(f"Total history entries generated: {len(history)}")
    except Exception as e:
        logger.error(f"Failed running ReAct loop: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(run_agent_test())
