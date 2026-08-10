"""Deterministic, read-only evidence collection for an incident case file.

This module is intentionally not an ADK agent. A durable worker invokes it
with a persisted incident, which makes the evidence path auditable and keeps
all Tencent/EdgeOne API knowledge inside ``edgeone_agents``.
"""
from __future__ import annotations

from hashlib import sha256
from ipaddress import ip_address
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .real_tools.data_analysis import (
    describe_overview_l7_data,
    describe_top_l7_analysis_data,
)
from .real_tools.log_service import collect_l7_log_client_ips
from .real_tools.security import (
    describe_security_policy,
    describe_security_template_bindings,
    describe_web_security_template,
    describe_web_security_templates,
)
from .real_tools.security import modify_security_policy


class StalePolicyError(RuntimeError):
    """The policy changed after approval, so an operator must re-review it."""


def inspect_custom_rule_capability(
    *, zone_id: str, proposed_rule: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return a read-only schema and write-payload preview for a custom rule.

    The preview deliberately uses the same modern ``SecurityPolicy`` shape as
    ``execute_approved_block_ip``.  It lets an operator compare the exact
    request with EdgeOne's API Explorer before a live change is attempted.
    """
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    policy = response.get("SecurityPolicy") or {}
    config = response.get("SecurityConfig") or {}
    custom_rules = policy.get("CustomRules") or {}
    acl_config = config.get("AclConfig") or {}
    existing_rules = list(custom_rules.get("Rules") or []) if isinstance(custom_rules, Mapping) else []
    merged_rules = existing_rules
    if proposed_rule is not None:
        merged_rules = [*existing_rules, dict(proposed_rule)]
    summary = {
        "request_id": response.get("RequestId"),
        "modern_security_policy_keys": sorted(policy.keys()) if isinstance(policy, Mapping) else [],
        "modern_custom_rule_keys": sorted(custom_rules.keys()) if isinstance(custom_rules, Mapping) else [],
        "modern_custom_rule_count": len(existing_rules),
        "legacy_security_config_keys": sorted(config.keys()) if isinstance(config, Mapping) else [],
        "legacy_acl_keys": sorted(acl_config.keys()) if isinstance(acl_config, Mapping) else [],
        "legacy_acl_rule_count": len(acl_config.get("UserRules") or []) if isinstance(acl_config, Mapping) else 0,
    }
    if proposed_rule is not None:
        summary["write_payload_preview"] = _custom_rules_write_payload(
            zone_id=zone_id,
            rules=merged_rules,
        )
    return summary


def prepare_block_ip_recommendation(*, incident_id: str, zone_id: str, host: str | None, client_ip: str) -> dict[str, Any]:
    """Read a policy snapshot and prepare, but do not apply, an IP block rule."""
    # The approved demo action is intentionally site-level, not host-level.
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    policy = response.get("SecurityPolicy") or {}
    rules = ((policy.get("CustomRules") or {}).get("Rules") or [])
    priorities = [rule.get("Priority") for rule in rules if isinstance(rule, dict) and isinstance(rule.get("Priority"), int)]
    safe_ip_name = client_ip.replace(".", "-").replace(":", "-")
    rule = {
        # Keep the EdgeOne rule identifier conservative; incident context is
        # preserved in the local recommendation/timeline rather than encoded
        # with punctuation in a provider-facing name.
        "Name": f"block-client-ip-{safe_ip_name}",
        "Condition": f"${{http.request.ip}} in ['{client_ip}']",
        "Action": {"Name": "Deny"},
        # This account's EdgeOne Console/API accepts client-IP deny rules as
        # BasicAccessRule. Keep the emitted structure aligned with the
        # operator-verified request rather than relying on a service default.
        "RuleType": "BasicAccessRule",
        "Priority": max(priorities, default=0) + 10,
        "Enabled": "on",
    }
    return {"baseline_policy_fingerprint": _custom_rules_fingerprint(policy), "proposed_rule": rule}


def prepare_legacy_acl_block_ip_recommendation(*, incident_id: str, zone_id: str, host: str | None, client_ip: str) -> dict[str, Any]:
    """Prepare a legacy ACL version for plans without modern CustomRules quota.

    This remains a draft-only operation.  The legacy ACL list is read and
    merged before any later approved execution, because the API replaces that
    list wholesale.
    """
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    config = response.get("SecurityConfig") or {}
    acl_config = config.get("AclConfig") or {}
    rules = acl_config.get("AclUserRules") or []
    safe_ip_name = client_ip.replace(".", "_").replace(":", "_")
    existing_rule_ids = [
        rule.get("RuleID", 0)
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("RuleID", 0), int)
    ]
    existing_priorities = [
        rule.get("RulePriority", 0)
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("RulePriority", 0), int)
    ]
    rule = {
        "RuleID": max(existing_rule_ids, default=100000) + 1,
        "RuleName": f"block_client_ip_{safe_ip_name}",
        "Action": "drop",
        "RuleStatus": "on",
        "RulePriority": max(existing_priorities, default=0) + 1,
        "AclConditions": [{
            "MatchFrom": "ip",
            "Operator": "equal",
            "MatchContent": client_ip,
        }],
    }


def prepare_rate_limit_recommendation(*, incident_id: str, zone_id: str, host: str | None, client_ip: str) -> dict[str, Any]:
    """Prepare a bounded per-client-IP request-rate-limit recommendation.

    The demo baseline is 100 requests per two minutes, with a 20-minute
    temporary deny.  It targets the investigated public IP only; it does not
    create a broad site-wide limit from an incomplete incident signal.
    """
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    policy = response.get("SecurityPolicy") or {}
    rules = ((policy.get("RateLimitingRules") or {}).get("Rules") or [])
    priorities = [rule.get("Priority") for rule in rules if isinstance(rule, dict) and isinstance(rule.get("Priority"), int)]
    priority = max(priorities, default=0) + 1
    if priority > 100:
        raise ValueError("rate-limit priority space is exhausted; review existing rate-limit rules")
    safe_ip_name = client_ip.replace(".", "-").replace(":", "-")
    rule = {
        "Enabled": "on",
        "Name": f"rate-limit-client-ip-{safe_ip_name}",
        "Condition": f"${{http.request.ip}} in ['{client_ip}']",
        "CountBy": ["http.request.ip"],
        "MaxRequestThreshold": 100,
        "CountingPeriod": "2m",
        "ActionDuration": "20m",
        "Action": {"Name": "Deny"},
        "Priority": priority,
    }
    return {"baseline_policy_fingerprint": _rate_limiting_rules_fingerprint(policy), "proposed_rule": rule}


def prepare_proactive_recommendation(
    incident: Mapping[str, Any], assessment: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Turn a bounded agent choice into a fresh, read-only policy snapshot.

    The agent can select only an action type and an IP already present in the
    deterministic evidence. This function owns all EdgeOne field knowledge
    and creates no recommendation or live change by itself.
    """
    action_type = assessment.get("recommended_action")
    client_ip = assessment.get("candidate_ip")
    alarm = incident.get("source_alarm") or {}
    dimensions = alarm.get("dimensions") or {}
    zone_id = dimensions.get("zoneid") or dimensions.get("zoneId")
    if action_type != "block_client_ip" or not isinstance(client_ip, str) or not zone_id:
        return None
    common = {
        "incident_id": str(incident.get("id") or ""),
        "zone_id": str(zone_id),
        "host": dimensions.get("domain"),
        "client_ip": client_ip,
    }
    prepared = prepare_block_ip_recommendation(**common)
    return {"action_type": action_type, **prepared}
    return {
        "baseline_policy_fingerprint": _legacy_acl_fingerprint(config),
        "proposed_rule": rule,
    }


def execute_approved_block_ip(*, target: Mapping[str, Any], proposed_rule: Mapping[str, Any], expected_fingerprint: str) -> dict[str, Any]:
    """Re-read, compare, merge, and write exactly one previously approved rule."""
    zone_id = str(target["zone_id"])
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    policy = response.get("SecurityPolicy") or {}
    if _custom_rules_fingerprint(policy) != expected_fingerprint:
        raise StalePolicyError("custom rules changed since recommendation; create and approve a new version")
    rules = list(((policy.get("CustomRules") or {}).get("Rules") or []))
    if any(isinstance(rule, dict) and rule.get("Name") == proposed_rule.get("Name") for rule in rules):
        raise StalePolicyError("an equivalent rule now exists; do not apply a duplicate")
    rules.append(dict(proposed_rule))
    result = modify_security_policy(zone_id, entity="ZoneDefaultPolicy", custom_rules=rules)
    return {"request_id": result.get("RequestId"), "applied_rule": dict(proposed_rule)}


def execute_approved_legacy_acl_block_ip(*, target: Mapping[str, Any], proposed_rule: Mapping[str, Any], expected_fingerprint: str) -> dict[str, Any]:
    """Re-read, compare, merge, and apply one approved legacy ACL rule."""
    zone_id = str(target["zone_id"])
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    config = response.get("SecurityConfig") or {}
    if _legacy_acl_fingerprint(config) != expected_fingerprint:
        raise StalePolicyError("legacy ACL rules changed since recommendation; create and approve a new version")
    acl_config = config.get("AclConfig") or {}
    rules = list(acl_config.get("AclUserRules") or [])
    if any(isinstance(rule, dict) and rule.get("RuleName") == proposed_rule.get("RuleName") for rule in rules):
        raise StalePolicyError("an equivalent legacy ACL rule now exists; do not apply a duplicate")
    rules.append(dict(proposed_rule))
    result = modify_security_policy(
        zone_id,
        entity="ZoneDefaultPolicy",
        legacy_acl_rules=rules,
        legacy_acl_switch=str(acl_config.get("Switch") or "on"),
    )
    return {"request_id": result.get("RequestId"), "applied_rule": dict(proposed_rule)}


def execute_approved_rate_limit(*, target: Mapping[str, Any], proposed_rule: Mapping[str, Any], expected_fingerprint: str) -> dict[str, Any]:
    """Re-read, compare, merge, and apply one approved rate-limit rule."""
    zone_id = str(target["zone_id"])
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    policy = response.get("SecurityPolicy") or {}
    if _rate_limiting_rules_fingerprint(policy) != expected_fingerprint:
        raise StalePolicyError("rate-limit rules changed since recommendation; create and approve a new version")
    rules = list(((policy.get("RateLimitingRules") or {}).get("Rules") or []))
    if any(isinstance(rule, dict) and rule.get("Name") == proposed_rule.get("Name") for rule in rules):
        raise StalePolicyError("an equivalent rate-limit rule now exists; do not apply a duplicate")
    rules.append(dict(proposed_rule))
    result = modify_security_policy(zone_id, entity="ZoneDefaultPolicy", rate_limiting_rules=rules)
    return {"request_id": result.get("RequestId"), "applied_rule": dict(proposed_rule)}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _custom_rules_fingerprint(policy: Mapping[str, Any]) -> str:
    """Fingerprint only the rule set this action will read-merge-write."""
    return _fingerprint({"CustomRules": policy.get("CustomRules") or {}})


def _legacy_acl_fingerprint(config: Mapping[str, Any]) -> str:
    """Fingerprint exactly the legacy ACL section a fallback write replaces."""
    return _fingerprint({"AclConfig": config.get("AclConfig") or {}})


def _rate_limiting_rules_fingerprint(policy: Mapping[str, Any]) -> str:
    """Fingerprint only the rate-limit set this action will replace."""
    return _fingerprint({"RateLimitingRules": policy.get("RateLimitingRules") or {}})


def _custom_rules_write_payload(*, zone_id: str, rules: list[Any]) -> dict[str, Any]:
    """Build the provider payload used by ``modify_security_policy``.

    This helper is intentionally kept in the agent layer: neither the router
    nor frontend needs to know EdgeOne field names.
    """
    return {
        "ZoneId": zone_id,
        "Entity": "ZoneDefaultPolicy",
        "SecurityConfig": {},
        "SecurityPolicy": {"CustomRules": {"Rules": rules}},
    }


def inspect_legacy_acl_capability(
    *, zone_id: str, host: str | None = None, proposed_rule: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Read-only legacy ACL schema and exact write-payload preview."""
    response = describe_security_policy(zone_id, entity="ZoneDefaultPolicy")
    config = response.get("SecurityConfig") or {}
    acl_config = config.get("AclConfig") or {}
    existing_rules = list(acl_config.get("AclUserRules") or []) if isinstance(acl_config, Mapping) else []
    managed_rules = list(acl_config.get("Customizes") or []) if isinstance(acl_config, Mapping) else []
    result = {
        "request_id": response.get("RequestId"),
        "policy_mode": "legacy_acl",
        "legacy_acl_keys": sorted(acl_config.keys()) if isinstance(acl_config, Mapping) else [],
        "legacy_acl_switch": acl_config.get("Switch") if isinstance(acl_config, Mapping) else None,
        "legacy_acl_rule_count": len(existing_rules),
        "legacy_managed_custom_rule_count": len(managed_rules),
        "legacy_managed_custom_rule_names": [
            rule.get("RuleName") for rule in managed_rules
            if isinstance(rule, Mapping) and isinstance(rule.get("RuleName"), str)
        ],
    }
    if proposed_rule is not None:
        result["write_payload_preview"] = {
            "ZoneId": zone_id,
            "Entity": "ZoneDefaultPolicy",
            "SecurityConfig": {"AclConfig": {
                "Switch": acl_config.get("Switch") or "on",
                "AclUserRules": [*existing_rules, dict(proposed_rule)],
            }},
        }
    # A Console rule that is absent from ZoneDefaultPolicy is commonly stored
    # at domain scope.  Compare the two scopes read-only before attempting a
    # write: modifying the wrong one is both ineffective and unsafe.
    if host:
        try:
            host_response = describe_security_policy(zone_id, entity="Host", host=host)
            host_acl = ((host_response.get("SecurityConfig") or {}).get("AclConfig") or {})
            host_rules = host_acl.get("AclUserRules") or []
            host_managed_rules = host_acl.get("Customizes") or []
            result["host_policy_comparison"] = {
                "host": host,
                "request_id": host_response.get("RequestId"),
                "legacy_acl_switch": host_acl.get("Switch"),
                "legacy_acl_rule_count": len(host_rules),
                "legacy_acl_rule_names": [
                    rule.get("RuleName") for rule in host_rules
                    if isinstance(rule, Mapping) and isinstance(rule.get("RuleName"), str)
                ],
                "legacy_managed_custom_rule_count": len(host_managed_rules),
                "legacy_managed_custom_rule_names": [
                    rule.get("RuleName") for rule in host_managed_rules
                    if isinstance(rule, Mapping) and isinstance(rule.get("RuleName"), str)
                ],
            }
        except Exception as exc:
            result["host_policy_comparison"] = {
                "host": host,
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            templates_response = describe_web_security_templates([zone_id])
            templates = templates_response.get("SecurityPolicyTemplates") or []
            template_ids = [
                item.get("TemplateId") for item in templates
                if isinstance(item, Mapping) and isinstance(item.get("TemplateId"), str)
            ]
            template_matches: list[dict[str, Any]] = []
            if template_ids:
                bindings_response = describe_security_template_bindings(zone_id, template_ids)
                for binding in bindings_response.get("SecurityTemplate") or []:
                    if not isinstance(binding, Mapping):
                        continue
                    entities = [
                        entity for scope in binding.get("TemplateScope") or [] if isinstance(scope, Mapping)
                        for entity in scope.get("EntityStatus") or []
                        if isinstance(entity, Mapping) and entity.get("Entity") == host and entity.get("Status") == "online"
                    ]
                    if entities:
                        template_id = binding.get("TemplateId")
                        detail = describe_web_security_template(zone_id, str(template_id))
                        rules = (((detail.get("SecurityPolicy") or {}).get("CustomRules") or {}).get("Rules") or [])
                        template_matches.append({
                            "template_id": template_id,
                            "request_id": detail.get("RequestId"),
                            "modern_custom_rule_count": len(rules),
                            "modern_custom_rule_names": [
                                rule.get("Name") for rule in rules
                                if isinstance(rule, Mapping) and isinstance(rule.get("Name"), str)
                            ],
                        })
            result["template_policy_comparison"] = {
                "template_count": len(template_ids),
                "matching_online_templates": template_matches,
            }
        except Exception as exc:
            result["template_policy_comparison"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def _public_ip_candidates(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract only public IPs from TopDataRecord.DetailData rankings.

    ``TypeKey`` is an outer grouping value (often an App ID), while the
    ranked client IP and request count are ``DetailData[].Key`` / ``Value``.
    """
    data = response.get("Data")
    if not isinstance(data, list):
        return []
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in data:
        if not isinstance(record, Mapping):
            continue
        for detail in record.get("DetailData") or []:
            if not isinstance(detail, Mapping):
                continue
            try:
                candidate = ip_address(str(detail.get("Key", "")))
            except ValueError:
                continue
            if not candidate.is_global or str(candidate) in seen:
                continue
            seen.add(str(candidate))
            candidates.append({"ip": str(candidate), "requests": detail.get("Value")})
    return candidates[:10]


def _incident_evidence_window(incident: Mapping[str, Any]) -> tuple[str, str]:
    """Give fallback log retrieval a concrete, bounded UTC alarm window."""
    alarm = incident.get("source_alarm") or {}
    now = datetime.now(timezone.utc)
    raw_start = alarm.get("first_occurred_at")
    try:
        start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00")) if raw_start else now - timedelta(minutes=30)
    except ValueError:
        start = now - timedelta(minutes=30)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    # Include a small buffer before the alarm and never request future logs.
    start = max(start - timedelta(minutes=5), now - timedelta(hours=2))
    return (
        start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _top_client_ip_evidence_window() -> tuple[str, str]:
    """Return a top-N window that excludes EdgeOne's latest delayed data.

    EdgeOne documents a roughly ten-minute delay for L7 top-N dimensions.
    Ending the query ten minutes before now avoids repeatedly asking the API
    for a period it has not aggregated yet.
    """
    end = datetime.now(timezone.utc) - timedelta(minutes=10)
    start = end - timedelta(minutes=30)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def investigate_incident(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Collect bounded, read-only traffic and policy evidence for one incident.

    No result from this function can block an IP or modify configuration. A
    later approval workflow will be responsible for turning evidence into a
    reviewed recommendation.
    """
    alarm = incident.get("source_alarm") or {}
    dimensions = alarm.get("dimensions") or {}
    zone_id = dimensions.get("zoneid") or dimensions.get("zoneId")
    if not zone_id:
        return {
            "outcome": "insufficient_evidence",
            "summary": "Investigation could not identify an EdgeOne zone from the source alarm.",
            "evidence": [],
        }

    evidence: list[dict[str, Any]] = []
    failures: list[str] = []
    top_start_time, top_end_time = _top_client_ip_evidence_window()
    calls = (
        ("traffic_overview", lambda: describe_overview_l7_data([zone_id], lookback_minutes=30)),
        (
            "top_client_ips",
            lambda: describe_top_l7_analysis_data(
                [zone_id], "l7Flow_request_sip", start_time=top_start_time, end_time=top_end_time
            ),
        ),
        ("security_policy", lambda: describe_security_policy(zone_id, entity="ZoneDefaultPolicy")),
    )
    for name, read_call in calls:
        try:
            response = read_call()
            item = {
                "source": name,
                "request_id": response.get("RequestId"),
                "result_keys": sorted(response.keys()),
            }
            if name == "top_client_ips":
                item["candidates"] = _public_ip_candidates(response)
                item["window"] = {"start_time": top_start_time, "end_time": top_end_time, "reporting_delay_minutes": 10}
            evidence.append(item)
        except Exception as exc:  # Persist a concise operational failure, never secrets/response bodies.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    ip_candidates = next((item.get("candidates", []) for item in evidence if item["source"] == "top_client_ips"), [])
    if not ip_candidates:
        start_time, end_time = _incident_evidence_window(incident)
        try:
            log_evidence = collect_l7_log_client_ips(
                zone_id=str(zone_id),
                domain=dimensions.get("domain"),
                start_time=start_time,
                end_time=end_time,
            )
            evidence.append(log_evidence)
            ip_candidates = log_evidence.get("candidates") or []
        except Exception as exc:
            # Offline logs are a best-effort fallback. A missing entitlement,
            # archive delay, or rejected archive must not conceal the primary
            # analytics evidence or turn this read-only investigation unsafe.
            evidence.append({"source": "l7_offline_logs", "status": "unavailable", "candidates": []})
    if failures or not ip_candidates:
        return {
            "outcome": "insufficient_evidence",
            "summary": (
                "Read-only investigation completed, but no valid public client-IP candidate was available from "
                "top-N analytics or the bounded L7 offline-log fallback. Retry after EdgeOne's reporting delay."
                if not failures
                else "Read-only investigation completed with unavailable evidence sources."
            ),
            "evidence": evidence,
            "failures": failures,
        }
    return {
        "outcome": "evidence_collected",
        "summary": "Read-only traffic and current-policy evidence collected; no remediation was performed.",
        "evidence": evidence,
    }


def proactive_dry_run_assessment(incident: Mapping[str, Any]) -> dict[str, Any]:
    """Assess demo-autonomy eligibility without invoking a model or changing EdgeOne.

    This is the deterministic hand-off contract for the future restricted
    Incident Response Team. The narrow qualification avoids treating arbitrary
    alarms as autonomous remediation candidates.
    """
    alarm = incident.get("source_alarm") or {}
    metric = str(alarm.get("metric_name") or "")
    namespace = str(incident.get("namespace") or "")
    investigation = next(
        (
            event for event in reversed(incident.get("timeline") or [])
            if event.get("type") == "investigation.completed"
        ),
        None,
    )
    evidence = (investigation or {}).get("payload", {}).get("evidence") or []
    candidates = next(
        (item.get("candidates") or [] for item in evidence if isinstance(item, Mapping) and item.get("source") == "top_client_ips"),
        [],
    )
    candidate = next(
        (item for item in candidates if isinstance(item, Mapping) and isinstance(item.get("ip"), str)),
        None,
    )
    minimum_requests = 50
    request_count = candidate.get("requests") if candidate else None
    accepted_namespaces = {"qce/edgeone_l7", "Site Acceleration-host"}
    qualified = (
        metric == "host_requests"
        and namespace in accepted_namespaces
        and isinstance(request_count, (int, float))
        and request_count >= minimum_requests
    )
    if not qualified:
        return {
            "outcome": "proactive_not_eligible",
            "summary": "Proactive demo dry run did not qualify this incident for autonomous response.",
            "eligibility": {
                "required_metric": "host_requests",
                "accepted_namespaces": sorted(accepted_namespaces),
                "minimum_client_requests": minimum_requests,
                "candidate": candidate,
            },
        }
    return {
        "outcome": "proactive_dry_run_qualified",
        "summary": "Proactive demo dry run qualified a requests-flood incident; no model or remediation was invoked.",
        "eligibility": {
            "classification": "ddos_request_flood",
            "candidate": candidate,
            "minimum_client_requests": minimum_requests,
            "next_stage": "restricted_agent_assessment",
        },
    }
