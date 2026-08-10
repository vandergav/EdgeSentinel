"""Public callback endpoints. Keep handlers fast; workflows run from persisted jobs."""
from __future__ import annotations

import base64
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.services.alarm_ingestion import AlarmValidationError, IncidentStore

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_CALLBACK_BYTES = 64 * 1024


def _configured_auth_is_valid(request: Request, callback_token: str | None) -> bool:
    expected_token = os.environ.get("TCOP_CALLBACK_TOKEN")
    expected_user = os.environ.get("TCOP_CALLBACK_BASIC_AUTH_USER")
    expected_password = os.environ.get("TCOP_CALLBACK_BASIC_AUTH_PASSWORD")

    if expected_token and callback_token and hmac.compare_digest(expected_token, callback_token):
        return True
    if expected_user and expected_password:
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(expected_user, username) and hmac.compare_digest(
                expected_password, password
            )
    return False


def _auth_required() -> bool:
    return os.environ.get("TCOP_CALLBACK_AUTH_REQUIRED", "false").lower() in {"1", "true", "yes"}


@router.post("/tcop/alarm", status_code=status.HTTP_202_ACCEPTED)
async def receive_tcop_alarm(request: Request, token: str | None = None) -> dict[str, Any]:
    """Accept a TCOP alarm delivery and enqueue a later investigation.

    The callback token is intentionally supported both as a query parameter and
    `X-Callback-Token` because TCOP deployments may have different auth support.
    Prefer Basic Auth when confirmed by the account integration.
    """
    callback_token = request.headers.get("x-callback-token") or token
    if _auth_required() and not _configured_auth_is_valid(request, callback_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid callback credentials")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="invalid Content-Length"
            ) from exc
        if declared_length > MAX_CALLBACK_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="callback too large")

    body = await request.body()
    if len(body) > MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="callback too large")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON callback") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="callback JSON must be an object")

    store: IncidentStore = request.app.state.incident_store
    try:
        result = store.ingest(payload)
    except AlarmValidationError as exc:
        # Preserve enough signal to diagnose template changes without logging
        # alarm objects, domains, console URLs, or the raw callback body.
        logger.warning(
            "TCOP callback validation failed: %s; keys=%s; trigger_time=%r",
            exc,
            sorted(payload.keys()),
            payload.get("trigger_time"),
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    return {
        "delivery_id": result.delivery_id,
        "incident_id": result.incident_id,
        "duplicate": result.duplicate,
        "queued_investigation": result.queued_investigation,
        "source_status": result.source_status,
    }
