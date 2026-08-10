"""
Universal `get_current_time` tool — added to every category agent (and the
orchestrator) by agent_builder.py, not tied to any one API category.

This exists alongside edgeone_agents/callbacks.py's `inject_current_time`
before_model_callback as a second, independent layer against the same
failure mode: an LLM has no innate sense of "now" and, left alone, will
happily invent a plausible-looking date (we saw this land as a fabricated
StartTime/EndTime a couple months in the past on a "last 30 minutes" style
query). The callback stamps the real time into context automatically on
every turn; this tool lets an agent explicitly check/re-check it, which
matters most for relative language anchored to something other than right
now — "since yesterday", "last Tuesday", "this billing cycle".
"""
from datetime import datetime, timezone


def get_current_time() -> dict:
    """Returns the real current UTC time. Call this before computing any
    relative date/time range — "the last 30 minutes", "since yesterday",
    "this week" — instead of guessing, recalling a date from training, or
    reusing a timestamp mentioned earlier in the conversation.

    Returns:
        dict: {"utc_iso": "2026-08-07T14:32:00Z", "unix_seconds": int}
    """
    now = datetime.now(timezone.utc)
    return {
        "utc_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unix_seconds": int(now.timestamp()),
    }
