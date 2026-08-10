"""
Root orchestrator for the EdgeOne CDN/WAF engineer agent team.

This module is the ADK CLI entry point: `adk web` / `adk run` discover
agents by looking for a `root_agent` inside a package with an __init__.py
that imports this module (see edgeone_agents/__init__.py).

    From the repo root:
        adk web        # browser UI, auto-discovers edgeone_agents/
        adk run edgeone_agents

For the manual Runner/SessionService flow (closer control, easy to embed
in your own app), see main.py at the repo root instead.
"""
from typing import Any

from google.adk.agents import Agent

from .agent_builder import DEFAULT_MODEL_NAME, build_all_category_agents
from .api_catalog import CATALOG, total_api_count
from .callbacks import inject_current_time
from .model_provider import configured_model_from_env
from .real_tools._time import get_current_time

ORCHESTRATOR_MODEL = configured_model_from_env("EDGEONE_ORCHESTRATOR_MODEL", default=DEFAULT_MODEL_NAME)


def build_orchestrator(model: Any | None = None) -> Agent:
    # Normal configuration keeps the orchestrator and specialist knobs
    # independent. An explicit builder argument remains useful for tests and
    # deliberately applies the supplied model to the entire Chat team.
    selected_model = ORCHESTRATOR_MODEL if model is None else model
    sub_agents = build_all_category_agents() if model is None else build_all_category_agents(model=model)
    roster = "\n".join(f"- {a.name}: {a.description}" for a in sub_agents)

    return Agent(
        name="edgeone_engineer_orchestrator",
        model=selected_model,
        description=(
            "Autonomous Tencent Cloud EdgeOne CDN/WAF engineer. Diagnoses site issues by "
            "combining traffic, cache, origin, and security data, then — with the user's "
            "explicit approval — remediates them through the right specialist sub-agent."
        ),
        instruction=(
            "You are an autonomous Tencent Cloud EdgeOne CDN/WAF engineer coordinating a team "
            f"of {len(sub_agents)} specialists, one per EdgeOne API category "
            f"({total_api_count()} API actions catalogued in total, a handful already live).\n\n"
            "How to work a request:\n"
            "1. If the user describes a symptom ('why is my site slow', 'am I under attack', "
            "'is my cache working') rather than naming an operation, plan a short diagnostic "
            "sequence across the relevant specialists (data_analysis_agent for traffic/cache/"
            "origin signals, security_agent for WAF/DDoS state, site_agent if you need a "
            "ZoneId first) and synthesize a root-cause explanation from what comes back.\n"
            "2. If the user names a specific operation, delegate to the one specialist whose "
            "description matches it.\n"
            "   A ZoneId supplied by the user (for example, a value beginning with 'zone-') is "
            "already sufficient for a zone-scoped operation. Do not call the site specialist "
            "merely to look up or confirm that ZoneId.\n"
            "3. Before calling any tool that changes configuration (e.g. modify_security_policy, "
            "create_purge_task, create_zone), state exactly what you're about to do in plain "
            "language and wait for the user to confirm before proceeding.\n"
            "4. If a specialist reports status='not_implemented', relay that honestly instead of "
            "guessing at what the result would have been.\n"
            "5. After a specialist completes a real query, give the user a direct, grounded "
            "answer based on its returned fields. Never answer with only a tool status, an "
            "empty response, or punctuation. If the result is empty, say that plainly.\n"
            "6. Never fabricate metrics, logs, or API results.\n\n"
            "Your specialists:\n" + roster
        ),
        tools=[get_current_time],
        sub_agents=sub_agents,
        before_model_callback=inject_current_time,
    )


root_agent = build_orchestrator()
