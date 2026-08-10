"""
Generates the two generic tools a category falls back on for any API that
doesn't have a real implementation yet:

  - list_<category>_operations(): tells the LLM everything this category
    will eventually be able to do, sourced straight from api_catalog.
  - <category>_not_implemented(operation, notes): a safe, honest fallback
    the LLM calls instead of guessing at a result when the user wants an
    operation that has no live implementation.

This is what lets agent_builder.py give every one of the 25 category
agents a complete, accurate picture of its own API surface on day one,
even though only a handful of categories have real tools wired up.
"""
from typing import Callable, Optional

from .api_catalog import CategorySpec


def build_catalog_tools(category: CategorySpec) -> tuple[Callable, Callable]:
    """Returns (list_operations_fn, not_implemented_fn) bound to one category."""

    def list_operations() -> dict:
        return {
            "category": category.display_name,
            "operations": [
                {"api": a.name, "feature": a.feature}
                for a in category.apis
            ],
        }

    def not_implemented(operation: str, notes: Optional[str] = None) -> dict:
        return {
            "status": "not_implemented",
            "category": category.display_name,
            "operation": operation,
            "notes": notes or "",
            "message": (
                f"{operation} is catalogued under {category.display_name} but isn't wired to "
                f"the live Tencent Cloud API yet. Implement it in "
                f"edgeone_agents/real_tools/{category.key}.py and add it to that module's "
                f"TOOLS list — agent_builder.py will pick it up automatically."
            ),
        }

    list_operations.__name__ = f"list_{category.key}_operations"
    list_operations.__doc__ = (
        f"Lists every {category.display_name} operation this agent will eventually support, "
        f"whether or not it is wired up to the live API yet. Call this if you're unsure "
        f"whether a request belongs to this category, or which exact API action it maps to.\n\n"
        f"Returns:\n"
        f"    dict: {{'category': str, 'operations': [{{'api': str, 'feature': str}}, ...]}}"
    )

    not_implemented.__name__ = f"{category.key}_not_implemented"
    not_implemented.__doc__ = (
        f"Call this when the user's {category.display_name} request has no live implementation "
        f"yet, instead of guessing at what the result would be.\n\n"
        f"Args:\n"
        f"    operation (str): The Tencent API name the request maps to, e.g. one of the names "
        f"returned by list_{category.key}_operations, like \"{category.apis[0].name}\".\n"
        f"    notes (str, optional): Anything about the request worth surfacing, e.g. the "
        f"arguments the user gave and why they wanted this.\n\n"
        f"Returns:\n"
        f"    dict: A status='not_implemented' record describing what was requested."
    )

    return list_operations, not_implemented
