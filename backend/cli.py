"""
Manual Runner/SessionService CLI for the EdgeOne agent team — same pattern
as ADK's own "Build Your First Intelligent Agent Team" tutorial
(session_service.create_session + Runner + an async event loop reading
event.is_final_response()), just wired to edgeone_agents.agent.root_agent
and turned into an interactive prompt instead of a few canned queries.

Handy for testing agent changes directly, without the FastAPI server,
copilot-runtime bridge, or frontend running.

If you'd rather use ADK's built-in web UI / CLI runner instead of this
script, run `adk web` from inside backend/ — see edgeone_agents/agent.py.
For the actual HTTP/AG-UI server this app uses, see main.py instead.

Usage (from backend/):
    cp .env.example .env   # then fill in your keys
    pip install -r requirements.txt
    python cli.py
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from edgeone_agents.agent import root_agent

APP_NAME = "edgeone_agent_team"
USER_ID = "local_user"
SESSION_ID = "local_session"


async def call_agent_async(query: str, runner: Runner, user_id: str, session_id: str) -> None:
    """Sends one query to the root agent and prints its final response."""
    content = types.Content(role="user", parts=[types.Part(text=query)])
    final_response_text = "Agent did not produce a final response."

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        if event.is_final_response():
            if event.content and event.content.parts:
                final_response_text = event.content.parts[0].text
            elif event.actions and event.actions.escalate:
                final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"
            break

    print(f"\n{final_response_text}\n")


async def run_repl() -> None:
    missing = [v for v in ("GOOGLE_API_KEY", "TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY") if not os.environ.get(v)]
    if missing:
        print(f"⚠️  Missing env vars: {', '.join(missing)}. Copy .env.example to .env and fill them in.")
        print("   (The team will still start, but calls needing these will fail.)\n")

    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)

    print(f"EdgeOne CDN/WAF agent team ready ({len(root_agent.sub_agents)} specialists).")
    print("Type a request, or 'exit' to quit.\n")

    while True:
        try:
            query = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query:
            continue
        if query.lower() in {"exit", "quit"}:
            break
        await call_agent_async(query, runner, USER_ID, SESSION_ID)


if __name__ == "__main__":
    asyncio.run(run_repl())
