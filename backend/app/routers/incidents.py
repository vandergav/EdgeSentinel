"""Read-only Stage 2 incident case-file endpoints.

Approval and execution endpoints remain intentionally absent until the agent
workflow, recommendation versioning, and safe policy merge are implemented.
"""
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
import os
from ipaddress import ip_address
from pydantic import BaseModel
from typing import Literal

from app.services.alarm_ingestion import AlarmValidationError, IncidentStore
from app.services.tcop_reconciliation import (
    ReconciliationDisabledError,
    reconcile_alarm_history,
)
from app.workers.incident_worker import run_once

router = APIRouter()


class BlockIpRecommendationRequest(BaseModel):
    client_ip: str
    strategy: Literal["modern", "legacy_acl"] = "modern"


class RateLimitRecommendationRequest(BaseModel):
    client_ip: str


class ProactiveModeRequest(BaseModel):
    enabled: bool


class IncidentReadRequest(BaseModel):
    through_event_id: int


def _viewer_id(x_incident_viewer_id: str | None) -> str:
    """Return the anonymous, browser-scoped UI preference identifier.

    This is intentionally not a user identity or an authorization mechanism.
    """
    return x_incident_viewer_id or "default-operator"


def _proactive_demo_available() -> bool:
    """Server-side kill switch for the future autonomous demo workflow."""
    return os.environ.get("INCIDENT_PROACTIVE_DEMO_ENABLED", "false").lower() in {"1", "true", "yes"}


def _require_actionable_incident(store: IncidentStore, incident_id: str) -> dict:
    incident = store.get_incident(incident_id)
    if incident is None:
        raise KeyError(incident_id)
    if not store.is_incident_actionable(incident_id):
        raise AlarmValidationError("TCOP has resolved this incident; recommendations and remediation are disabled")
    return incident


@router.get("")
def list_incidents(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    x_incident_viewer_id: str | None = Header(default=None),
) -> dict:
    """List durable incident case-file summaries created by TCOP callbacks."""
    store: IncidentStore = request.app.state.incident_store
    try:
        return {"items": store.list_incidents(status=status, limit=limit, viewer_id=_viewer_id(x_incident_viewer_id))}
    except AlarmValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/attention")
def get_incident_attention(
    request: Request,
    limit: int = Query(default=5, ge=1, le=10),
    x_incident_viewer_id: str | None = Header(default=None),
) -> dict:
    """Return the cross-page unread badge and recently active case files."""
    store: IncidentStore = request.app.state.incident_store
    try:
        return store.incident_attention(viewer_id=_viewer_id(x_incident_viewer_id), limit=limit)
    except AlarmValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.post("/read-all")
def mark_all_incidents_read(
    request: Request,
    x_incident_viewer_id: str | None = Header(default=None),
) -> dict:
    """Acknowledge all incident events currently visible to this browser."""
    store: IncidentStore = request.app.state.incident_store
    try:
        return store.mark_all_incidents_read(viewer_id=_viewer_id(x_incident_viewer_id))
    except AlarmValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("/proactive-mode")
def get_proactive_mode(request: Request) -> dict:
    """Read the persisted Incidents-page demo autonomy preference."""
    store: IncidentStore = request.app.state.incident_store
    return {**store.get_proactive_mode(), "available": _proactive_demo_available()}


@router.put("/proactive-mode")
def set_proactive_mode(body: ProactiveModeRequest, request: Request) -> dict:
    """Set demo autonomy preference, subject to the server-side kill switch.

    This stage persists and displays the preference only. Later stages will
    consume it when deciding whether a qualified incident may be processed.
    """
    if body.enabled and not _proactive_demo_available():
        raise HTTPException(
            status_code=409,
            detail="proactive demo mode is disabled by the server; set INCIDENT_PROACTIVE_DEMO_ENABLED=true to enable it",
        )
    store: IncidentStore = request.app.state.incident_store
    return {**store.set_proactive_mode(enabled=body.enabled), "available": _proactive_demo_available()}


@router.get("/{incident_id}")
def get_incident(incident_id: str, request: Request) -> dict:
    """Get one incident, normalized source alarm, queued work, and public timeline."""
    store: IncidentStore = request.app.state.incident_store
    incident = store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    return incident


@router.post("/{incident_id}/read")
def mark_incident_read(
    incident_id: str,
    body: IncidentReadRequest,
    request: Request,
    x_incident_viewer_id: str | None = Header(default=None),
) -> dict:
    """Mark exactly the event version loaded by the incident detail view as read."""
    store: IncidentStore = request.app.state.incident_store
    try:
        return store.mark_incident_read(
            incident_id,
            viewer_id=_viewer_id(x_incident_viewer_id),
            through_event_id=body.through_event_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="incident not found") from exc
    except AlarmValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.delete("/{incident_id}")
def delete_one_incident(incident_id: str, request: Request) -> dict:
    """Permanently remove one case file and its derived workflow data."""
    store: IncidentStore = request.app.state.incident_store
    try:
        result = store.delete_incidents([incident_id])
    except AlarmValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not result["deleted_ids"]:
        raise HTTPException(status_code=404, detail="incident not found")
    return result


@router.post("/reconcile", status_code=status.HTTP_200_OK)
def reconcile_incidents(
    request: Request,
    lookback_minutes: int = Query(default=180, ge=1, le=1440),
    policy_id: str | None = Query(default=None),
) -> dict:
    """Compare a bounded TCOP history window with stored callback records.

    Disabled by default and strictly read-only: mismatches are persisted as
    audit observations, never converted into incidents or remediation jobs.
    """
    store: IncidentStore = request.app.state.incident_store
    try:
        return reconcile_alarm_history(store, lookback_minutes=lookback_minutes, policy_id=policy_id)
    except ReconciliationDisabledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/jobs/run-once")
def run_one_investigation(
    request: Request,
    incident_id: str | None = Query(default=None),
) -> dict:
    """Run one leased, read-only investigation when explicitly enabled.

    This operator endpoint exists for the demo until a separately supervised
    worker process is deployed. It cannot perform remediation.
    """
    if os.environ.get("INCIDENT_WORKER_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=409, detail="incident worker is disabled")
    store: IncidentStore = request.app.state.incident_store
    result = run_once(store, incident_id=incident_id)
    return result or {"status": "idle"}


@router.post("/{incident_id}/recommendations/block-ip", status_code=status.HTTP_201_CREATED)
def create_block_ip_recommendation(
    incident_id: str,
    body: BlockIpRecommendationRequest,
    request: Request,
) -> dict:
    """Create a draft IP-block recommendation; this endpoint never writes EdgeOne."""
    try:
        client_ip = str(ip_address(body.client_ip))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="client_ip must be a valid IPv4 or IPv6 address") from exc
    store: IncidentStore = request.app.state.incident_store
    try:
        incident = _require_actionable_incident(store, incident_id)
        dimensions = (incident.get("source_alarm") or {}).get("dimensions") or {}
        zone_id = dimensions.get("zoneid") or dimensions.get("zoneId")
        if not zone_id:
            raise AlarmValidationError("incident source alarm does not identify a zone")
        from edgeone_agents.incident_workflow import (
            prepare_block_ip_recommendation,
            prepare_legacy_acl_block_ip_recommendation,
        )

        prepare = prepare_legacy_acl_block_ip_recommendation if body.strategy == "legacy_acl" else prepare_block_ip_recommendation
        prepared = prepare(
            incident_id=incident_id,
            zone_id=str(zone_id),
            host=dimensions.get("domain"),
            client_ip=client_ip,
        )
        recommendation = store.create_block_ip_recommendation(
            incident_id,
            client_ip=client_ip,
            proposed_rule=prepared["proposed_rule"],
            baseline_policy_fingerprint=prepared["baseline_policy_fingerprint"],
            action_type="block_client_ip_legacy_acl" if body.strategy == "legacy_acl" else "block_client_ip",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="incident not found") from exc
    except AlarmValidationError as exc:
        code = status.HTTP_409_CONFLICT if "resolved this incident" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return recommendation


@router.post("/{incident_id}/recommendations/rate-limit", status_code=status.HTTP_201_CREATED)
def create_rate_limit_recommendation(
    incident_id: str,
    body: RateLimitRecommendationRequest,
    request: Request,
) -> dict:
    """Create a draft, per-client-IP rate-limit recommendation; never writes EdgeOne."""
    try:
        client_ip = str(ip_address(body.client_ip))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="client_ip must be a valid IPv4 or IPv6 address") from exc
    store: IncidentStore = request.app.state.incident_store
    try:
        incident = _require_actionable_incident(store, incident_id)
        dimensions = (incident.get("source_alarm") or {}).get("dimensions") or {}
        zone_id = dimensions.get("zoneid") or dimensions.get("zoneId")
        if not zone_id:
            raise AlarmValidationError("incident source alarm does not identify a zone")
        from edgeone_agents.incident_workflow import prepare_rate_limit_recommendation

        prepared = prepare_rate_limit_recommendation(
            incident_id=incident_id,
            zone_id=str(zone_id),
            host=dimensions.get("domain"),
            client_ip=client_ip,
        )
        recommendation = store.create_block_ip_recommendation(
            incident_id,
            client_ip=client_ip,
            proposed_rule=prepared["proposed_rule"],
            baseline_policy_fingerprint=prepared["baseline_policy_fingerprint"],
            action_type="rate_limit_client_ip",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="incident not found") from exc
    except AlarmValidationError as exc:
        code = status.HTTP_409_CONFLICT if "resolved this incident" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return recommendation


@router.post("/recommendations/{recommendation_id}/approve")
def approve_recommendation(recommendation_id: str, request: Request) -> dict:
    """Record explicit approval for a draft; execution remains separately gated."""
    store: IncidentStore = request.app.state.incident_store
    try:
        existing = store.get_recommendation(recommendation_id)
        if existing is None:
            raise KeyError(recommendation_id)
        _require_actionable_incident(store, str(existing["incident_id"]))
        recommendation = store.approve_recommendation(recommendation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="recommendation not found") from exc
    except AlarmValidationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    return recommendation


@router.get("/recommendations/{recommendation_id}/preflight")
def preflight_recommendation(recommendation_id: str, request: Request) -> dict:
    """Read-only schema check before troubleshooting a provider policy write."""
    store: IncidentStore = request.app.state.incident_store
    recommendation = store.get_recommendation(recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    from edgeone_agents.incident_workflow import inspect_custom_rule_capability, inspect_legacy_acl_capability

    if recommendation["action_type"] == "block_client_ip_legacy_acl":
        return inspect_legacy_acl_capability(
            zone_id=recommendation["target"]["zone_id"],
            host=recommendation["target"].get("host"),
            proposed_rule=recommendation["proposed_rule"],
        )
    return inspect_custom_rule_capability(
        zone_id=recommendation["target"]["zone_id"],
        proposed_rule=recommendation["proposed_rule"],
    )


@router.post("/recommendations/{recommendation_id}/execute")
def execute_recommendation(recommendation_id: str, request: Request) -> dict:
    """Apply one approved version only when remediation is explicitly enabled."""
    if os.environ.get("INCIDENT_REMEDIATION_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=409, detail="incident remediation is disabled")
    store: IncidentStore = request.app.state.incident_store
    recommendation = store.get_recommendation(recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    if recommendation["status"] != "approved":
        raise HTTPException(status_code=409, detail="recommendation is not approved")
    try:
        _require_actionable_incident(store, str(recommendation["incident_id"]))
    except AlarmValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not recommendation["baseline_policy_fingerprint"]:
        raise HTTPException(status_code=409, detail="recommendation lacks a baseline policy snapshot")
    try:
        from edgeone_agents.incident_workflow import (
            StalePolicyError,
            execute_approved_block_ip,
            execute_approved_legacy_acl_block_ip,
            execute_approved_rate_limit,
        )
        from edgeone_agents.real_tools._client import TencentCloudApiError

        execute = {
            "block_client_ip_legacy_acl": execute_approved_legacy_acl_block_ip,
            "rate_limit_client_ip": execute_approved_rate_limit,
        }.get(recommendation["action_type"], execute_approved_block_ip)
        result = execute(
            target=recommendation["target"],
            proposed_rule=recommendation["proposed_rule"],
            expected_fingerprint=recommendation["baseline_policy_fingerprint"],
        )
        executed = store.mark_recommendation_executed(recommendation_id, result=result)
        return {"recommendation": executed, "result": result}
    except StalePolicyError as exc:
        store.mark_recommendation_stale(recommendation_id, reason=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TencentCloudApiError as exc:
        store.record_remediation_failure(recommendation_id, error=exc.message, request_id=exc.request_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"EdgeOne rejected the rule ({exc.code}); request ID: {exc.request_id}",
        ) from exc
