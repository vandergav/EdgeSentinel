"""
Shared before_model_callback(s) for the agent team.

Fixes: agents had no ground truth for "now" and were fabricating plausible
but wrong StartTime/EndTime values for data_analysis queries (e.g. a "last
30 minutes" request coming out as a date months in the past). A tool the
LLM has to remember to call isn't enough on its own — small/fast models
skip it under time pressure — so this callback stamps the real time into
*every single* LLM call automatically, with zero reliance on the model
choosing to ask.
"""
from datetime import datetime, timezone

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types


def inject_current_time(callback_context: CallbackContext, llm_request: LlmRequest):
    """Stamps the real current UTC time onto the system instruction of every
    outgoing LLM request.

    Modifies llm_request.config.system_instruction in place — deliberately
    NOT llm_request.contents. Appending to `contents` on every turn would
    duplicate the stamp once per turn of conversation history (contents is
    the full replayed history), bloating token usage for no benefit;
    system_instruction is the correct place for a fact that's true "as of
    right now" rather than part of the conversation itself.

    Always returns None so the (modified) request proceeds normally — this
    callback only ever adds context, it never blocks a call.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = (
        f"[Live context] The current UTC time is {now}. Treat this as ground truth. "
        f"Never guess, recall a date from training, or reuse a timestamp from an "
        f"earlier example when you need \"now\", \"today\", or a relative window like "
        f"\"the last 30 minutes\" — compute it from this timestamp, or call "
        f"get_current_time if you need to double check."
    )

    try:
        existing = llm_request.config.system_instruction
        if existing is None:
            llm_request.config.system_instruction = stamp
        elif hasattr(existing, "parts"):
            existing.parts.append(types.Part(text=stamp))
        else:
            llm_request.config.system_instruction = f"{existing}\n\n{stamp}"
    except AttributeError:
        # Fallback for ADK versions where llm_request.config isn't present:
        # append to the live turn instead. Less clean (accumulates per turn
        # of history rather than staying a single invariant fact) but keeps
        # agents time-correct even if the config path changes underneath us.
        llm_request.contents.append(types.Content(role="user", parts=[types.Part(text=stamp)]))

    return None
