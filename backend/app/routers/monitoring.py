"""
Placeholder for Phase 2: the live monitoring dashboard and background
monitoring service. Intentionally not implemented in this phase — see the
top-level README's "Future Extensibility" section. Each stub documents the
shape a future phase is expected to fill in, so the frontend dashboard has
a stable contract to build against today, before the real data exists.
"""
from fastapi import APIRouter

from ._stub import not_implemented

router = APIRouter()


@router.get("/status")
def monitoring_service_status() -> dict:
    """Will report whether the background monitoring service is running,
    and when it last polled EdgeOne."""
    return not_implemented("background monitoring service")


@router.get("/metrics")
def latest_metrics() -> dict:
    """Will return the latest polled EdgeOne metrics (traffic, cache, origin
    health, security events) for the dashboard to render."""
    return not_implemented("live EdgeOne metrics feed")
