"""Lease and process one queued incident investigation at a time."""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from app.services.alarm_ingestion import IncidentStore


Investigator = Callable[[Mapping[str, Any]], dict[str, Any]]


def _proactive_demo_enabled(store: IncidentStore) -> bool:
    return (
        os.environ.get("INCIDENT_PROACTIVE_DEMO_ENABLED", "false").lower() in {"1", "true", "yes"}
        and bool(store.get_proactive_mode().get("enabled"))
    )


def _proactive_auto_approve_enabled(store: IncidentStore) -> bool:
    return (
        _proactive_demo_enabled(store)
        and os.environ.get("INCIDENT_PROACTIVE_AUTO_APPROVE_ENABLED", "false").lower() in {"1", "true", "yes"}
        and bool(store.get_proactive_mode().get("enabled"))
    )


def _proactive_auto_execute_enabled(store: IncidentStore) -> bool:
    return (
        _proactive_auto_approve_enabled(store)
        and os.environ.get("INCIDENT_PROACTIVE_AUTO_EXECUTE_ENABLED", "false").lower() in {"1", "true", "yes"}
        and os.environ.get("INCIDENT_REMEDIATION_ENABLED", "false").lower() in {"1", "true", "yes"}
    )


def run_once(
    store: IncidentStore,
    *,
    incident_id: str | None = None,
    investigator: Investigator | None = None,
) -> dict[str, Any] | None:
    """Process one durable job, returning its public outcome or ``None``.

    The production investigator is imported only when work is claimed, keeping
    the ingestion tests independent of ADK and live Tencent credentials.
    """
    job = store.claim_next_job(incident_id=incident_id)
    if job is None:
        return None
    try:
        incident = store.get_incident(str(job["incident_id"]))
        if incident is None:
            raise KeyError(f"incident {job['incident_id']} not found")
        if not store.is_incident_actionable(str(job["incident_id"])):
            store.cancel_leased_job(
                int(job["id"]), summary="TCOP recovery was already recorded; proactive workflow job was skipped"
            )
            return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "cancelled"}
        store.record_job_started(job)
        if investigator is None:
            if job["job_type"] in {"proactive_dry_run_assessment", "proactive_agent_assessment"}:
                if os.environ.get("INCIDENT_PROACTIVE_DEMO_ENABLED", "false").lower() not in {"1", "true", "yes"}:
                    outcome = {
                        "outcome": "proactive_skipped",
                        "summary": "Proactive dry-run assessment skipped because the server demo kill switch is off.",
                    }
                    store.complete_job(int(job["id"]), summary=outcome["summary"], payload=outcome)
                    return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "completed", "outcome": outcome}
                if job["job_type"] == "proactive_dry_run_assessment":
                    from edgeone_agents.incident_workflow import proactive_dry_run_assessment

                    investigator = proactive_dry_run_assessment
                else:
                    if os.environ.get("INCIDENT_PROACTIVE_AGENT_ENABLED", "false").lower() not in {"1", "true", "yes"}:
                        outcome = {
                            "outcome": "proactive_skipped",
                            "summary": "Restricted Incident Response Team assessment skipped because its server feature flag is off.",
                        }
                        store.complete_job(int(job["id"]), summary=outcome["summary"], payload=outcome)
                        return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "completed", "outcome": outcome}
                    from edgeone_agents.incident_response_team import proactive_agent_assessment

                    investigator = proactive_agent_assessment
            else:
                from edgeone_agents.incident_workflow import investigate_incident

                investigator = investigate_incident
        outcome = investigator(incident)
        # Recovery can arrive while a read-only call or bounded agent run is
        # in progress. Never turn stale evidence into a recommendation/write.
        if not store.is_incident_actionable(str(job["incident_id"])):
            store.cancel_leased_job(
                int(job["id"]), summary="TCOP recovery arrived while workflow work was running; remaining actions were skipped"
            )
            return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "cancelled"}
        if job["job_type"] == "proactive_agent_assessment":
            assessment = outcome.get("assessment") or {}
            from edgeone_agents.incident_workflow import prepare_proactive_recommendation

            prepared = prepare_proactive_recommendation(incident, assessment)
            if prepared is not None:
                recommendation = store.create_block_ip_recommendation(
                    str(job["incident_id"]),
                    client_ip=str(assessment["candidate_ip"]),
                    proposed_rule=prepared["proposed_rule"],
                    baseline_policy_fingerprint=prepared["baseline_policy_fingerprint"],
                    action_type=prepared["action_type"],
                )
                store.record_proactive_recommendation(
                    str(job["incident_id"]), recommendation["id"], str(prepared["action_type"])
                )
                if _proactive_auto_approve_enabled(store):
                    store.auto_approve_proactive_recommendation(recommendation["id"])
                if _proactive_auto_execute_enabled(store):
                    try:
                        from edgeone_agents.incident_workflow import StalePolicyError, execute_approved_block_ip
                        from edgeone_agents.real_tools._client import TencentCloudApiError

                        result = execute_approved_block_ip(
                            target=recommendation["target"],
                            proposed_rule=recommendation["proposed_rule"],
                            expected_fingerprint=recommendation["baseline_policy_fingerprint"],
                        )
                        store.mark_recommendation_executed(
                            recommendation["id"], result=result, source="proactive_demo"
                        )
                        outcome["autonomous_execution"] = {"status": "executed", "result": result}
                    except StalePolicyError as exc:
                        store.mark_recommendation_stale(recommendation["id"], reason=str(exc))
                        outcome["autonomous_execution"] = {"status": "stale", "reason": str(exc)}
                    except TencentCloudApiError as exc:
                        store.record_remediation_failure(
                            recommendation["id"], error=exc.message, request_id=exc.request_id
                        )
                        outcome["autonomous_execution"] = {
                            "status": "provider_rejected",
                            "request_id": exc.request_id,
                        }
        store.complete_job(int(job["id"]), summary=str(outcome.get("summary", "Investigation completed")), payload=outcome)
        if (
            job["job_type"] in {"investigate_incident", "recheck_client_ips"}
            and outcome.get("outcome") == "evidence_collected"
            and _proactive_demo_enabled(store)
            and store.is_incident_actionable(str(job["incident_id"]))
        ):
            # Qualification is intentionally downstream of evidence. If the
            # first pass sees no source IP, delayed rechecks get the chance to
            # supply one before the Agent Team is asked to classify it.
            store.queue_proactive_dry_run_assessment(str(job["incident_id"]))
        if (
            job["job_type"] == "proactive_dry_run_assessment"
            and outcome.get("outcome") == "proactive_dry_run_qualified"
            and os.environ.get("INCIDENT_PROACTIVE_AGENT_ENABLED", "false").lower() in {"1", "true", "yes"}
            and store.is_incident_actionable(str(job["incident_id"]))
        ):
            store.queue_proactive_agent_assessment(str(job["incident_id"]))
        return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "completed", "outcome": outcome}
    except Exception as exc:
        store.fail_job(int(job["id"]), error=f"{type(exc).__name__}: {exc}")
        return {"job_id": job["id"], "incident_id": job["incident_id"], "status": "retry_scheduled"}
