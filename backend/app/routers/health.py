"""Liveness/readiness check for the API layer itself."""
from fastapi import APIRouter

router = APIRouter()


@router.get("")
def health_check() -> dict:
    """Confirms the FastAPI process is up. Does not check the agent team or
    Tencent connectivity — this is a plain process-liveness probe."""
    return {"status": "ok", "service": "edgeone-platform-backend"}
