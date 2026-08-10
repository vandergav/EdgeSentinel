"""
Turns each CategorySpec in api_catalog.CATALOG into a ready-to-use ADK Agent.

The wiring convention for adding real tools to a category:
  1. Create edgeone_agents/real_tools/<category key>.py.
  2. Write plain functions with type-hinted args and a Google-style
     docstring (Args/Returns) — same shape ADK's own tutorial uses.
  3. Decorate each with @tencent_api("ExactApiName") from real_tools._client
     so the builder knows which catalog entry it satisfies.
  4. Export them in a module-level `TOOLS = [fn1, fn2, ...]` list.

Nothing else needs to change — build_category_agent() below picks the
module up automatically by category key, includes its real tools first,
and fills every remaining (not-yet-implemented) API in that category with
the honest catalog stub tools from tool_factory.py.
"""
import importlib
from typing import Any

from google.adk.agents import Agent

from .api_catalog import CATALOG, CategorySpec
from .callbacks import inject_current_time
from .model_provider import GEMINI_DEFAULT_MODEL, configured_model_from_env
from .real_tools._time import get_current_time
from .tool_factory import build_catalog_tools

# The resolver returns a plain Gemini string by default, or an ADK LiteLlm
# instance when EDGEONE_LLM_PROVIDER=openai.
DEFAULT_MODEL_NAME = GEMINI_DEFAULT_MODEL
DEFAULT_MODEL = configured_model_from_env("EDGEONE_AGENT_MODEL", default=DEFAULT_MODEL_NAME)


def _load_real_tools(category_key: str) -> tuple[list, set]:
    """Imports real_tools/<category_key>.py if it exists and returns
    (tools, set-of-Tencent-API-names-covered)."""
    try:
        module = importlib.import_module(f"edgeone_agents.real_tools.{category_key}")
    except ModuleNotFoundError:
        return [], set()

    tools = list(getattr(module, "TOOLS", []))
    covered = {getattr(t, "_tencent_api", t.__name__) for t in tools}
    return tools, covered


def _result_presentation_guidance(category: CategorySpec) -> str:
    """Returns narrow, category-specific result presentation guidance.

    This belongs with the agent definitions (rather than the HTTP bridge or
    frontend) because it governs how a specialist explains a live EdgeOne API
    result to a user.
    """
    if category.key == "edge_function":
        return (
            "\n\nFor DescribeFunctions specifically: state the returned total, then list each "
            "function's Name, FunctionId, preview Domain, and Remark when available. Mention "
            "that source is available, but do not paste full function source unless the user "
            "explicitly asks for it. If no functions are returned, say so plainly."
        )
    return ""


def build_category_agent(category: CategorySpec, model: Any = DEFAULT_MODEL) -> Agent:
    """Builds the single sub-agent responsible for one API category."""
    real_tools, implemented = _load_real_tools(category.key)
    list_op_tool, not_impl_tool = build_catalog_tools(category)

    remaining = [a for a in category.apis if a.name not in implemented]
    tools: list[Any] = list(real_tools) + [list_op_tool, get_current_time]
    if remaining:
        tools.append(not_impl_tool)

    status = f"{len(implemented)}/{len(category.apis)} APIs live" if implemented else "catalog-only, 0 APIs live"

    return Agent(
        name=f"{category.key}_agent",
        model=model,
        description=f"{category.description} ({status})",
        instruction=(
            f"You are the {category.display_name} specialist on a Tencent Cloud EdgeOne "
            f"CDN/WAF engineering team.\n\n"
            f"Scope: only handle requests that map to a {category.display_name} operation. "
            f"If you're not sure what's in scope, call list_{category.key}_operations first — "
            f"it lists every operation this category will ever cover, live or not.\n\n"
            f"If an operation has a real tool, call it directly with the best arguments you "
            f"can infer from the conversation, and ask the user for anything required you "
            f"don't have (e.g. a ZoneId).\n\n"
            "After a real tool returns, synthesize a concise, user-facing answer from its "
            "returned fields. For a list or query, state what was found and the most relevant "
            "identifiers/counts. Do not respond with only a tool name, a status, an empty string, "
            "or punctuation. If the returned collection is empty, state that no matching data "
            "was found. Do not expose credentials, tokens, or source/configuration content that "
            "the user did not ask to inspect."
            f"{_result_presentation_guidance(category)}\n\n"
            f"If an operation only exists in the catalog (no real tool yet), call "
            f"{category.key}_not_implemented with the operation name and a note about what "
            f"the user wanted — do NOT fabricate a plausible-looking result.\n\n"
            f"Never invent data — that includes dates and times. The current UTC time is "
            f"stamped into your context automatically on every turn; call get_current_time "
            f"yourself if you want to double check before computing a relative window like "
            f"\"the last 30 minutes\" or \"since yesterday\"."
        ),
        tools=tools,
        before_model_callback=inject_current_time,
    )


def build_all_category_agents(model: Any = DEFAULT_MODEL) -> list[Agent]:
    """Builds all 25 category sub-agents, in the same order as api_catalog.CATALOG."""
    return [build_category_agent(category, model=model) for category in CATALOG.values()]
