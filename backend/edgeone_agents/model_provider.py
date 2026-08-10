"""Central model selection for ADK agents.

Gemini remains the zero-configuration default. Setting
``EDGEONE_LLM_PROVIDER=openai`` converts configured model names into ADK
``LiteLlm`` instances, allowing the existing agents and tool contracts to run
through OpenAI without duplicating agent definitions.
"""
from __future__ import annotations

import os
from typing import Any


GEMINI_DEFAULT_MODEL = "gemini-flash-latest"
OPENAI_DEFAULT_MODEL = "gpt-5-mini"


def configured_model(model_name: str) -> Any:
    """Return an ADK-compatible Gemini name or LiteLLM-backed OpenAI model."""
    provider = os.environ.get("EDGEONE_LLM_PROVIDER", "gemini").strip().lower()
    if provider in {"", "gemini", "google"}:
        return model_name
    if provider != "openai":
        raise ValueError("EDGEONE_LLM_PROVIDER must be 'gemini' or 'openai'")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("EDGEONE_LLM_PROVIDER=openai requires OPENAI_API_KEY")
    try:
        from google.adk.models.lite_llm import LiteLlm
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI model selection requires LiteLLM. Install backend requirements before starting the server."
        ) from exc
    # A copied .env commonly retains a Gemini model while the provider is
    # switched. Treat any Gemini identifier as an OpenAI default rather than
    # issuing the nonsensical LiteLLM request ``openai/gemini-…``.
    if model_name.lower().startswith("gemini"):
        model_name = OPENAI_DEFAULT_MODEL
    normalized_name = model_name if model_name.startswith("openai/") else f"openai/{model_name}"
    return LiteLlm(model=normalized_name)


def configured_model_from_env(variable: str, *, default: str) -> Any:
    """Resolve one model env var using the selected provider."""
    return configured_model(os.environ.get(variable, default))
