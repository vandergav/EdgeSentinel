"""
Shared response shape for phase-2+ router placeholders (monitoring,
incidents, settings). Deliberately mirrors edgeone_agents.tool_factory's
not_implemented tool: the agent team and the REST API should be honest
about what's live in the same voice, rather than each inventing their own
"coming soon" convention.
"""
from typing import Optional


def not_implemented(feature: str, notes: Optional[str] = None) -> dict:
    return {
        "status": "not_implemented",
        "feature": feature,
        "notes": notes or "",
        "message": (
            f"{feature} is scaffolded but not implemented in this phase — see the "
            f"top-level README's \"Future Extensibility\" section."
        ),
    }
