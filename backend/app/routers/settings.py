"""
Placeholder for an application settings API (e.g. managing Tencent
credentials, notification channels, or which Gemini/Claude/GPT model each
agent uses, from the UI instead of hand-editing .env files). Intentionally
not implemented in this phase — see the top-level README's "Future
Extensibility" section.
"""
from fastapi import APIRouter

from ._stub import not_implemented

router = APIRouter()


@router.get("")
def get_settings() -> dict:
    """Will return the app's current configuration (a safe subset — no
    secret values, just what's set vs. missing)."""
    return not_implemented("settings API")
