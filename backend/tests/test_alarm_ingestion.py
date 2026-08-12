import tempfile
import unittest
import os
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path

from app.services.alarm_ingestion import AlarmValidationError, IncidentStore, normalize_alarm, normalize_template_alarm
from app.services.tcop_reconciliation import reconcile_alarm_history
from app.workers.incident_worker import run_once
from edgeone_agents.incident_workflow import investigate_incident
from edgeone_agents.real_tools.log_service import _public_client_ip_counts_from_archives
import gzip
import json


def active_payload(duration: str | int = "60") -> dict:
    return {
        "sessionId": "session-123",
        "alarmStatus": "1",
        "alarmType": "metric",
        "alarmObjInfo": {
            "region": "gz",
            "namespace": "qce/cdn",
            "appId": "10001",
            "uin": "10001",
            "dimensions": {"domain": "example.com", "zoneid": "zone-test", "objId": "example.com"},
        },
        "alarmPolicyInfo": {
            "policyId": "policy-1",
            "policyName": "Traffic spike",
            "policyType": "cdn",
            "conditions": {
                "metricName": "request_count",
                "metricShowName": "Requests",
                "currentValue": "1200",
                "calcValue": 1000,
                "unit": "count",
            },
        },
        "firstOccurTime": "2026-08-09 10:00:00",
        "durationTime": duration,
        "recoverTime": 0,
    }


class AlarmIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = IncidentStore(Path(self.temp_dir.name) / "incidents.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_normalization_accepts_documented_mixed_scalar_types(self) -> None:
        alarm = normalize_alarm(active_payload(duration=60))
        self.assertEqual(alarm.status, "active")
        self.assertEqual(alarm.duration_seconds, 60)
        self.assertEqual(alarm.threshold_value, "1000")
        self.assertEqual(alarm.first_occurred_at, "2026-08-09T02:00:00Z")

    def test_normalization_accepts_configured_notification_template(self) -> None:
        alarm = normalize_template_alarm(
            {
                "app_id": "1462486034",
                "policy_id": "policy-host-requests",
                "policy_name": "host_requests",
                "server_name": "Site Acceleration-host",
                "object": "AppId:1462486034|ZoneId:zone-3tans1fkqja5|ZoneName:idealtest.site|domain:cloudflared.idealtest.site",
                "content": "host_requests > 3 Count",
                "current_value": "0 (host_requests)",
                "status": "Resolved",
                "trigger_time": "2026-08-09 12:36:00",
                "first_trigger_time": "2026-08-09 12:00:00",
                "recovery_time": "2026-08-09 12:37:00",
            }
        )
        self.assertEqual(alarm.status, "resolved")
        self.assertEqual(alarm.dimensions["zoneid"], "zone-3tans1fkqja5")
        self.assertEqual(alarm.dimensions["domain"], "cloudflared.idealtest.site")
        self.assertEqual(alarm.policy_id, "policy-host-requests")
        self.assertEqual(alarm.first_occurred_at, "2026-08-09T04:00:00Z")
        self.assertEqual(alarm.recovered_at, "2026-08-09T04:37:00Z")

    def test_template_timestamp_accepts_display_timezone_suffix(self) -> None:
        alarm = normalize_template_alarm(
            {
                "status": "Recovery",
                "trigger_time": "2026-08-09 12:36:00 (UTC+08:00)",
            }
        )
        self.assertEqual(alarm.first_occurred_at, "2026-08-09T04:36:00Z")

    def test_template_recovery_correlates_using_first_trigger_time(self) -> None:
        active = {
            "app_id": "1462486034",
            "policy_id": "policy-host-requests",
            "policy_name": "host_requests",
            "server_name": "Site Acceleration-host",
            "object": "AppId:1462486034|ZoneId:zone-3tans1fkqja5|domain:cloudflared.idealtest.site",
            "content": "host_requests > 3 Count",
            "current_value": "18 Count (host_requests)",
            "status": "Trigger",
            "trigger_time": "2026-08-09 12:20:00",
            "first_trigger_time": "2026-08-09 12:00:00",
            "recovery_time": "",
        }
        recovery = {
            **active,
            "status": "Recovery",
            "current_value": "0 Count (host_requests)",
            "trigger_time": "2026-08-09 12:36:00",
            "first_trigger_time": "2026-08-09 12:00:00",
            "recovery_time": "2026-08-09 12:37:00",
        }

        opened = self.store.ingest(active)
        resolved = self.store.ingest(recovery)

        self.assertEqual(opened.incident_id, resolved.incident_id)
        self.assertFalse(resolved.created_incident)
        self.assertEqual(self.store.incident_status(opened.incident_id), "source_resolved")

    def test_template_recovery_falls_back_to_pre_policy_id_case_file(self) -> None:
        active = {
            "app_id": "1462486034",
            "policy_name": "host_requests",
            "server_name": "Site Acceleration-host",
            "object": "AppId:1462486034|ZoneId:zone-3tans1fkqja5|domain:cloudflared.idealtest.site",
            "content": "host_requests > 3 Count",
            "status": "Trigger",
            "trigger_time": "2026-08-09 12:20:00",
            "first_trigger_time": "2026-08-09 12:00:00",
        }
        recovery = {
            **active,
            "policy_id": "policy-host-requests",
            "status": "Recovery",
            "trigger_time": "2026-08-09 12:36:00",
            "recovery_time": "2026-08-09 12:37:00",
        }

        opened = self.store.ingest(active)
        resolved = self.store.ingest(recovery)

        self.assertEqual(opened.incident_id, resolved.incident_id)
        self.assertFalse(resolved.created_incident)

    def test_duplicate_delivery_is_idempotent(self) -> None:
        first = self.store.ingest(active_payload())
        duplicate = self.store.ingest(active_payload())

        self.assertFalse(first.duplicate)
        self.assertTrue(first.created_incident)
        self.assertTrue(first.queued_investigation)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(first.incident_id, duplicate.incident_id)
        self.assertEqual(self.store.count_rows("incidents"), 1)
        self.assertEqual(self.store.count_rows("alarm_deliveries"), 1)
        self.assertEqual(self.store.count_rows("jobs"), 1)

    def test_incident_attention_is_per_viewer_and_preserves_later_events(self) -> None:
        created = self.store.ingest(active_payload())
        summary = self.store.list_incidents(viewer_id="viewer-a")[0]
        self.assertTrue(summary["is_unread"])

        detail = self.store.get_incident(created.incident_id)
        assert detail is not None
        self.store.mark_incident_read(
            created.incident_id,
            viewer_id="viewer-a",
            through_event_id=detail["latest_event_id"],
        )
        self.assertFalse(self.store.list_incidents(viewer_id="viewer-a")[0]["is_unread"])
        self.assertTrue(self.store.list_incidents(viewer_id="viewer-b")[0]["is_unread"])

        with self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'test.later_update', 'Later workflow update', '{}', ?)
                """,
                (created.incident_id, "2026-08-09T04:05:00Z"),
            )
        # The earlier read cursor does not acknowledge the event that arrived
        # after the detail response was loaded.
        self.assertTrue(self.store.list_incidents(viewer_id="viewer-a")[0]["is_unread"])

    def test_incident_list_orders_by_latest_event_activity(self) -> None:
        first = self.store.ingest(active_payload())
        second_payload = active_payload()
        second_payload["sessionId"] = "session-second"
        second_payload["alarmPolicyInfo"] = {**second_payload["alarmPolicyInfo"], "policyId": "policy-2"}
        second_payload["firstOccurTime"] = "2026-08-09 10:01:00"
        second = self.store.ingest(second_payload)
        with self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'test.latest_update', 'Newest event', '{}', ?)
                """,
                (first.incident_id, "2099-08-09T05:00:00Z"),
            )
        ordered = self.store.list_incidents(viewer_id="viewer-order")
        self.assertEqual([item["id"] for item in ordered[:2]], [first.incident_id, second.incident_id])

    def test_remediation_outcome_is_exposed_alongside_tcop_source_status(self) -> None:
        created = self.store.ingest(active_payload())
        with self.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO recommendations (
                    id, incident_id, version, status, action_type, target, proposed_rule,
                    created_at, executed_at
                ) VALUES (?, ?, 1, 'executed', 'block_client_ip', '{}', '{}', ?, ?)
                """,
                ("REC-TESTEXECUTED", created.incident_id, "2026-08-09T04:00:00Z", "2026-08-09T04:01:00Z"),
            )
        recovery = active_payload()
        recovery["sessionId"] = "session-recovery"
        recovery["alarmStatus"] = 0
        recovery["recoverTime"] = "2026-08-09 10:03:00"
        self.store.ingest(recovery)
        summary = self.store.list_incidents(viewer_id="viewer-remediation")[0]
        detail = self.store.get_incident(created.incident_id)
        assert detail is not None
        self.assertEqual(summary["status"], "source_resolved")
        self.assertTrue(summary["remediation_executed"])
        self.assertTrue(detail["remediation_executed"])

    def test_repeated_and_resolved_callbacks_share_the_incident(self) -> None:
        first = self.store.ingest(active_payload("60"))
        repeated_payload = active_payload("120")
        repeated_payload["sessionId"] = "session-456"
        repeated = self.store.ingest(repeated_payload)
        resolved_payload = active_payload("180")
        # Live TCOP callbacks use a new session/alert id for another delivery
        # in the same alarm lifecycle, including its recovery notification.
        resolved_payload["sessionId"] = "session-789"
        resolved_payload["alarmStatus"] = 0
        resolved_payload["recoverTime"] = "2026-08-09 10:03:00"
        resolved = self.store.ingest(resolved_payload)

        self.assertEqual(first.incident_id, repeated.incident_id)
        self.assertEqual(first.incident_id, resolved.incident_id)
        self.assertFalse(repeated.queued_investigation)
        self.assertEqual(self.store.count_rows("alarm_occurrences"), 3)
        self.assertEqual(self.store.incident_status(first.incident_id), "source_resolved")
        with self.store._connection() as connection:
            job_status = connection.execute(
                "SELECT status FROM jobs WHERE incident_id = ? AND job_type = 'investigate_incident'",
                (first.incident_id,),
            ).fetchone()["status"]
        self.assertEqual(job_status, "cancelled")
        detail = self.store.get_incident(first.incident_id)
        assert detail is not None
        self.assertTrue(any(event["type"] == "workflow.cancelled" for event in detail["timeline"]))

    def test_unmatched_recovery_is_audit_only_case_without_work(self) -> None:
        recovered = active_payload()
        recovered["alarmStatus"] = 0
        recovered["recoverTime"] = "2026-08-09 10:03:00"
        result = self.store.ingest(recovered)

        self.assertTrue(result.created_incident)
        self.assertEqual(self.store.incident_status(result.incident_id), "recovery_unmatched")
        self.assertEqual(self.store.count_rows("jobs"), 0)

    def test_delete_case_file_removes_derived_data_but_retains_delivery_audit(self) -> None:
        created = self.store.ingest(active_payload())
        result = self.store.delete_incidents([created.incident_id])

        self.assertEqual(result["deleted_ids"], [created.incident_id])
        self.assertIsNone(self.store.get_incident(created.incident_id))
        self.assertEqual(self.store.count_rows("incidents"), 0)
        self.assertEqual(self.store.count_rows("jobs"), 0)
        self.assertEqual(self.store.count_rows("alarm_occurrences"), 0)
        self.assertEqual(self.store.count_rows("alarm_deliveries"), 1)

    def test_delete_case_file_refuses_to_race_an_active_worker(self) -> None:
        created = self.store.ingest(active_payload())
        self.assertIsNotNone(self.store.claim_next_job(incident_id=created.incident_id))

        with self.assertRaisesRegex(AlarmValidationError, "active worker jobs"):
            self.store.delete_incidents([created.incident_id])

        self.assertIsNotNone(self.store.get_incident(created.incident_id))

    def test_case_file_returns_source_alarm_and_timeline(self) -> None:
        result = self.store.ingest(active_payload())
        incident = self.store.get_incident(result.incident_id)

        self.assertIsNotNone(incident)
        assert incident is not None
        self.assertEqual(incident["source_alarm"]["namespace"], "qce/cdn")
        self.assertEqual(incident["timeline"][0]["type"], "alarm.received")
        self.assertEqual(incident["job"]["status"], "queued")
        self.assertTrue(incident["investigation_queued"])

    def test_reconciliation_records_missing_callbacks_without_creating_incidents(self) -> None:
        known = self.store.ingest(active_payload())
        remote_history = {
            "Histories": [
                {"AlarmId": "known", "PolicyId": "policy-1", "FirstOccurTime": 1786240800},
                {"AlarmId": "missing", "PolicyId": "policy-2", "FirstOccurTime": 1786240860},
            ],
            "TotalCount": 2,
        }
        with patch.dict("os.environ", {"TCOP_RECONCILIATION_ENABLED": "true"}):
            result = reconcile_alarm_history(
                self.store,
                lookback_minutes=60,
                now=datetime(2026, 8, 9, 2, 30, tzinfo=timezone.utc),
                fetch_history=lambda **_: remote_history,
            )

        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["backfilled_count"], 1)
        self.assertEqual(result["observations"][0]["incident_id"], known.incident_id)
        self.assertEqual(result["observations"][1]["status"], "backfilled")
        self.assertEqual(self.store.count_rows("incidents"), 2)
        self.assertEqual(self.store.count_rows("jobs"), 1)
        self.assertEqual(self.store.count_rows("reconciliation_observations"), 2)

    def test_worker_leases_and_completes_one_read_only_investigation(self) -> None:
        created = self.store.ingest(active_payload())
        result = run_once(
            self.store,
            investigator=lambda _: {
                "outcome": "evidence_collected",
                "summary": "Read-only evidence collected.",
                "evidence": [{"source": "test"}],
            },
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.incident_status(created.incident_id), "investigated")
        incident = self.store.get_incident(created.incident_id)
        assert incident is not None
        self.assertEqual(incident["job"]["status"], "completed")
        self.assertEqual(incident["timeline"][-1]["type"], "investigation.completed")

    def test_proactive_flow_starts_evidence_collection_immediately(self) -> None:
        self.store.set_proactive_mode(enabled=True)
        created = self.store.ingest(active_payload())
        with self.store._connection() as connection:
            job = connection.execute(
                "SELECT job_type, available_at FROM jobs WHERE incident_id = ?", (created.incident_id,)
            ).fetchone()
        assert job is not None
        self.assertEqual(job["job_type"], "investigate_incident")
        self.assertIsNone(job["available_at"])
        incident = self.store.get_incident(created.incident_id)
        assert incident is not None
        self.assertEqual(incident["timeline"][-1]["type"], "proactive.queued")
        self.assertEqual(incident["timeline"][-1]["payload"]["evidence_gate"], "immediate")

        with patch.dict(os.environ, {"INCIDENT_PROACTIVE_DEMO_ENABLED": "true"}):
            run_once(
                self.store,
                incident_id=created.incident_id,
                investigator=lambda _: {
                    "outcome": "evidence_collected",
                    "summary": "Read-only evidence collected.",
                    "evidence": [{"source": "top_client_ips", "candidates": [{"ip": "101.78.81.233", "requests": 50}]}],
                },
            )
        with self.store._connection() as connection:
            types = [row["job_type"] for row in connection.execute("SELECT job_type FROM jobs WHERE incident_id = ? ORDER BY id", (created.incident_id,))]
        self.assertEqual(types, ["investigate_incident", "proactive_dry_run_assessment"])

    def test_initialize_releases_only_legacy_initial_proactive_waits(self) -> None:
        created = self.store.ingest(active_payload())
        legacy_available_at = "2099-01-01T00:00:00Z"
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE jobs SET available_at = ? WHERE incident_id = ? AND job_type = 'investigate_incident'",
                (legacy_available_at, created.incident_id),
            )

        self.store.initialize()

        with self.store._connection() as connection:
            job = connection.execute(
                "SELECT available_at FROM jobs WHERE incident_id = ? AND job_type = 'investigate_incident'",
                (created.incident_id,),
            ).fetchone()
        assert job is not None
        self.assertIsNone(job["available_at"])
        incident = self.store.get_incident(created.incident_id)
        assert incident is not None
        migration_events = [event for event in incident["timeline"] if event["type"] == "proactive.evidence_wait_removed"]
        self.assertEqual(len(migration_events), 1)
        self.assertEqual(migration_events[0]["payload"]["previous_available_at"], legacy_available_at)

    def test_worker_failure_returns_job_to_queue(self) -> None:
        self.store.ingest(active_payload())
        result = run_once(self.store, investigator=lambda _: (_ for _ in ()).throw(RuntimeError("offline")))

        self.assertEqual(result["status"], "retry_scheduled")
        self.assertEqual(self.store.claim_next_job()["status"], "leased")

    def test_block_ip_recommendation_requires_explicit_approval(self) -> None:
        incident = self.store.ingest(active_payload())
        recommendation = self.store.create_block_ip_recommendation(
            incident.incident_id, client_ip="203.0.113.7"
        )

        self.assertEqual(recommendation["status"], "draft")
        self.assertEqual(recommendation["proposed_rule"]["Action"]["Name"], "Deny")
        approved = self.store.approve_recommendation(recommendation["id"])
        assert approved is not None
        self.assertEqual(approved["status"], "approved")
        detail = self.store.get_incident(incident.incident_id)
        assert detail is not None
        self.assertEqual(detail["recommendations"][0]["id"], recommendation["id"])

    def test_l7_offline_log_parser_ranks_only_public_client_ips(self) -> None:
        archive = gzip.compress(b"\n".join([
            json.dumps({"ClientIP": "101.78.81.233"}).encode(),
            json.dumps({"ClientIP": "101.78.81.233"}).encode(),
            json.dumps({"ClientIP": "10.0.0.1"}).encode(),
            json.dumps({"ClientIP": "not-an-ip"}).encode(),
        ]))
        candidates, parsed = _public_client_ip_counts_from_archives([archive])
        self.assertEqual(parsed, 4)
        self.assertEqual(candidates, [{"ip": "101.78.81.233", "requests": 2}])

    def test_investigation_uses_l7_log_fallback_when_top_n_is_empty(self) -> None:
        incident = {
            "id": "INC-test",
            "source_alarm": {
                "first_occurred_at": "2026-08-09T02:00:00Z",
                "dimensions": {"zoneid": "zone-test", "domain": "example.com"},
            },
        }
        with (
            patch("edgeone_agents.incident_workflow.describe_overview_l7_data", return_value={"RequestId": "overview"}),
            patch("edgeone_agents.incident_workflow.describe_top_l7_analysis_data", return_value={"RequestId": "top", "Data": []}),
            patch("edgeone_agents.incident_workflow.describe_security_policy", return_value={"RequestId": "policy"}),
            patch(
                "edgeone_agents.incident_workflow.collect_l7_log_client_ips",
                return_value={
                    "source": "l7_offline_logs",
                    "request_id": "logs",
                    "archive_count": 1,
                    "parsed_record_count": 45,
                    "candidates": [{"ip": "101.78.81.233", "requests": 42}],
                },
            ),
        ):
            result = investigate_incident(incident)

        self.assertEqual(result["outcome"], "evidence_collected")
        self.assertEqual(result["evidence"][-1]["source"], "l7_offline_logs")
        self.assertEqual(result["evidence"][-1]["candidates"][0]["ip"], "101.78.81.233")
