"""Durable, retry-safe ingestion for Tencent Cloud Observability alarms.

This service deliberately does not call an agent or Tencent API. TCOP retries
failed callbacks and gives receivers only five seconds, so the HTTP path must
only validate, normalize, persist, and enqueue later work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from uuid import uuid4


class AlarmValidationError(ValueError):
    """Raised when a callback cannot be normalized safely."""


@dataclass(frozen=True)
class NormalizedAlarm:
    provider: str
    session_id: str | None
    alarm_type: str | None
    status: str
    namespace: str | None
    region: str | None
    app_id: str | None
    account_uin: str | None
    policy_id: str | None
    policy_name: str | None
    policy_type: str | None
    metric_name: str | None
    metric_display_name: str | None
    current_value: str | None
    threshold_value: str | None
    unit: str | None
    first_occurred_at: str | None
    recovered_at: str | None
    duration_seconds: int | None
    dimensions: dict[str, Any]


@dataclass(frozen=True)
class IngestionResult:
    delivery_id: int
    incident_id: str
    duplicate: bool
    created_incident: bool
    queued_investigation: bool
    source_status: str


def _as_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AlarmValidationError(f"{name} must be a JSON object")
    return value


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_status(value: Any) -> str:
    if value in (1, "1"):
        return "active"
    if value in (0, "0"):
        return "resolved"
    raise AlarmValidationError("alarmStatus must be 1 (active) or 0 (resolved)")


def _parse_template_status(value: Any) -> str:
    normalized = (_text(value) or "").casefold()
    if normalized in {"trigger", "triggered", "alarm", "alert", "active", "firing"}:
        return "active"
    if normalized in {"recovery", "recover", "resolved", "resolve", "ok"}:
        return "resolved"
    raise AlarmValidationError("status must describe a trigger or recovery")


def _parse_timestamp(value: Any, field_name: str) -> str | None:
    text = _text(value)
    if text in (None, "0"):
        return None
    # TCOP's documented format has no offset. Rendered notification-template
    # values may append a display offset, use slashes, or omit the year.
    # They are all Beijing time unless an explicit UTC offset is supplied.
    normalized = re.sub(r"\s*\(UTC[^)]+\)\s*$", "", text, flags=re.IGNORECASE).replace("T", " ")
    parsed: datetime | None = None
    try:
        iso_candidate = datetime.fromisoformat(normalized)
        if iso_candidate.tzinfo is not None:
            parsed = iso_candidate
    except ValueError:
        pass
    if parsed is None:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, pattern).replace(tzinfo=timezone(timedelta(hours=8)))
                break
            except ValueError:
                pass
    if parsed is None:
        for pattern in ("%m-%d %H:%M:%S", "%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, pattern).replace(
                    year=datetime.now(timezone(timedelta(hours=8))).year,
                    tzinfo=timezone(timedelta(hours=8)),
                )
                break
            except ValueError:
                pass
    if parsed is None:
        raise AlarmValidationError(
            f"{field_name} must use a TCOP date/time display format"
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_duration(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise AlarmValidationError("durationTime must be an integer") from exc
    if parsed < 0:
        raise AlarmValidationError("durationTime cannot be negative")
    return parsed


def normalize_alarm(payload: Mapping[str, Any]) -> NormalizedAlarm:
    """Normalize documented TCOP metric callback fields without discarding unknown data."""
    if not isinstance(payload, Mapping):
        raise AlarmValidationError("callback body must be a JSON object")

    alarm_object = _as_mapping(payload.get("alarmObjInfo"), "alarmObjInfo")
    policy = _as_mapping(payload.get("alarmPolicyInfo"), "alarmPolicyInfo")
    conditions = _as_mapping(policy.get("conditions"), "alarmPolicyInfo.conditions")
    dimensions = dict(_as_mapping(alarm_object.get("dimensions"), "alarmObjInfo.dimensions"))

    return NormalizedAlarm(
        provider="tencent_tcop",
        session_id=_text(payload.get("sessionId")),
        alarm_type=_text(payload.get("alarmType")),
        status=_parse_status(payload.get("alarmStatus")),
        namespace=_text(alarm_object.get("namespace")),
        region=_text(alarm_object.get("region")),
        app_id=_text(alarm_object.get("appId")),
        account_uin=_text(alarm_object.get("uin")),
        policy_id=_text(policy.get("policyId")),
        policy_name=_text(policy.get("policyName")),
        policy_type=_text(policy.get("policyType")),
        metric_name=_text(conditions.get("metricName")),
        metric_display_name=_text(conditions.get("metricShowName")),
        current_value=_text(conditions.get("currentValue")),
        threshold_value=_text(conditions.get("calcValue")),
        unit=_text(conditions.get("unit") or conditions.get("calcUnit")),
        first_occurred_at=_parse_timestamp(payload.get("firstOccurTime"), "firstOccurTime"),
        recovered_at=_parse_timestamp(payload.get("recoverTime"), "recoverTime"),
        duration_seconds=_parse_duration(payload.get("durationTime")),
        dimensions=dimensions,
    )


def normalize_template_alarm(payload: Mapping[str, Any]) -> NormalizedAlarm:
    """Normalize the user-configurable TCOP notification-template shape.

    It is less rich than the documented callback schema, so fields absent
    from the template remain ``None``. Zone and domain are recovered only
    from TCOP's conventional ``object`` string when present.
    """
    object_text = _text(payload.get("object")) or ""
    dimensions: dict[str, Any] = {}
    for field, key in (("AppId", "appid"), ("ZoneId", "zoneid"), ("domain", "domain")):
        match = re.search(rf"(?:^|\|){re.escape(field)}:([^|]+)", object_text, flags=re.IGNORECASE)
        if match:
            dimensions[key] = match.group(1).strip()
    content = _text(payload.get("content"))
    threshold = None
    if content:
        threshold_match = re.search(r"(?:>|>=|<|<=)\s*([\d.]+)\s*([^\s]*)", content)
        if threshold_match:
            threshold = threshold_match.group(1)
    metric = content.split(" >", 1)[0].strip() if content and " >" in content else content
    status = _parse_template_status(payload.get("status"))
    trigger_time = _parse_timestamp(payload.get("trigger_time"), "trigger_time")
    # ``trigger_time`` changes for every TCOP notification.  The first
    # trigger time is stable for the alarm lifecycle and must therefore be
    # preferred when matching a later recovery callback to its active case.
    first_trigger_time = (
        _parse_timestamp(payload.get("first_trigger_time"), "first_trigger_time")
        or trigger_time
    )
    recovery_time = _parse_timestamp(payload.get("recovery_time"), "recovery_time")
    return NormalizedAlarm(
        provider="tencent_tcop_template",
        session_id=_text(payload.get("console_link")),
        alarm_type="metric",
        status=status,
        namespace=_text(payload.get("server_name")),
        region=None,
        app_id=_text(payload.get("app_id")) or _text(dimensions.get("appid")),
        account_uin=None,
        policy_id=_text(payload.get("policy_id")),
        policy_name=_text(payload.get("policy_name")),
        policy_type=_text(payload.get("server_name")),
        metric_name=metric,
        metric_display_name=metric,
        current_value=_text(payload.get("current_value")),
        threshold_value=threshold,
        unit=None,
        first_occurred_at=first_trigger_time,
        recovered_at=(recovery_time or trigger_time) if status == "resolved" else None,
        duration_seconds=None,
        dimensions=dimensions,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def delivery_fingerprint(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def correlation_key(alarm: NormalizedAlarm) -> str:
    """Return a stable key for one TCOP alarm lifecycle.

    ``sessionId``/``alertId`` identify a callback delivery in the live TCOP
    payloads; they are not stable across the active and recovery callbacks for
    the same policy violation.  Including either would split one lifecycle
    into two case files.
    """
    stable_dimensions = {
        key: value
        for key, value in alarm.dimensions.items()
        if key.lower() not in {"timestamp", "time", "currentvalue", "value"}
    }
    value = {
        "provider": alarm.provider,
        # Custom notification templates do not include policyId. Their policy
        # name plus target dimensions is the best stable correlation input.
        "policy": alarm.policy_id or alarm.policy_name or alarm.metric_name,
        "namespace": alarm.namespace,
        "first_occurred_at": alarm.first_occurred_at,
        "dimensions": stable_dimensions,
    }
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _legacy_template_correlation_key(alarm: NormalizedAlarm) -> str | None:
    """Return the template key used before template policy IDs were normalized.

    Recovery callbacks may arrive after this service is upgraded while their
    active ticket was created by the previous implementation.  Keep that
    historical case resolvable without weakening the primary policy-ID key for
    newly created tickets.
    """
    if alarm.provider != "tencent_tcop_template" or not alarm.policy_id:
        return None
    stable_dimensions = {
        key: value
        for key, value in alarm.dimensions.items()
        if key.lower() not in {"timestamp", "time", "currentvalue", "value"}
    }
    value = {
        "provider": alarm.provider,
        "policy": alarm.policy_name or alarm.metric_name,
        "namespace": alarm.namespace,
        "first_occurred_at": alarm.first_occurred_at,
        "dimensions": stable_dimensions,
    }
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class IncidentStore:
    """A small SQLite persistence boundary for the first incident vertical slice."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    correlation_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    namespace TEXT,
                    policy_id TEXT,
                    status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    source_resolved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alarm_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    raw_payload TEXT NOT NULL,
                    normalized_payload TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alarm_occurrences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    delivery_id INTEGER NOT NULL UNIQUE REFERENCES alarm_deliveries(id),
                    event_kind TEXT NOT NULL,
                    occurred_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS incident_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    completed_at TEXT,
                    available_at TEXT,
                    UNIQUE(incident_id, job_type)
                );

                CREATE TABLE IF NOT EXISTS reconciliation_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    window_start_at TEXT NOT NULL,
                    window_end_at TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    matched_count INTEGER NOT NULL,
                    missing_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reconciliation_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES reconciliation_runs(id),
                    external_alarm_id TEXT,
                    policy_id TEXT,
                    namespace TEXT,
                    first_occurred_at TEXT,
                    match_status TEXT NOT NULL,
                    incident_id TEXT REFERENCES incidents(id),
                    source_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target TEXT NOT NULL,
                    proposed_rule TEXT NOT NULL,
                    baseline_policy_fingerprint TEXT,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    executed_at TEXT,
                    UNIQUE(incident_id, version)
                );

                CREATE TABLE IF NOT EXISTS incident_automation_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    proactive_enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                -- This application has no authenticated users yet. A stable
                -- browser-generated viewer id lets the demo retain read state
                -- without treating it as an authorization boundary.
                CREATE TABLE IF NOT EXISTS incident_read_state (
                    viewer_id TEXT NOT NULL,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    last_read_event_id INTEGER NOT NULL DEFAULT 0,
                    last_read_at TEXT NOT NULL,
                    PRIMARY KEY (viewer_id, incident_id)
                );

                CREATE INDEX IF NOT EXISTS idx_incident_events_activity
                    ON incident_events (incident_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_incident_read_state_viewer
                    ON incident_read_state (viewer_id, incident_id);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO incident_automation_settings (id, proactive_enabled, updated_at)
                VALUES (1, 0, ?)
                """,
                (_utc_now(),),
            )
            # The demo database may already have the original Stage 1 jobs
            # table. SQLite has no ADD COLUMN IF NOT EXISTS, so migrate each
            # additive field defensively at startup.
            self._add_column_if_missing(connection, "jobs", "lease_expires_at TEXT")
            self._add_column_if_missing(connection, "jobs", "attempts INTEGER NOT NULL DEFAULT 0")
            self._add_column_if_missing(connection, "jobs", "last_error TEXT")
            self._add_column_if_missing(connection, "jobs", "completed_at TEXT")
            self._add_column_if_missing(connection, "jobs", "available_at TEXT")

    def get_proactive_mode(self) -> dict[str, Any]:
        """Return the persisted operator preference for demo incident autonomy."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT proactive_enabled, updated_at FROM incident_automation_settings WHERE id = 1"
            ).fetchone()
        return {
            "enabled": bool(row["proactive_enabled"]) if row else False,
            "updated_at": row["updated_at"] if row else None,
        }

    def set_proactive_mode(self, *, enabled: bool) -> dict[str, Any]:
        """Persist the operator preference; caller enforces the server kill switch."""
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE incident_automation_settings
                SET proactive_enabled = ?, updated_at = ?
                WHERE id = 1
                """,
                (int(enabled), now),
            )
        return {"enabled": enabled, "updated_at": now}

    def queue_proactive_agent_assessment(self, incident_id: str) -> None:
        """Queue the next read-only advisory stage once dry-run eligibility passes."""
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs (incident_id, job_type, status, created_at)
                VALUES (?, 'proactive_agent_assessment', 'queued', ?)
                """,
                (incident_id, now),
            )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'proactive.agent_queued', ?, ?, ?)
                """,
                (incident_id, "Restricted Incident Response Team assessment queued", _canonical_json({"mode": "advisory_only"}), now),
            )

    def queue_proactive_dry_run_assessment(self, incident_id: str) -> bool:
        """Queue/requeue qualification only after deterministic IP evidence exists.

        A TCOP callback can arrive before either analytics or offline logs
        contain source-IP data.  Do not let the proactive flow conclude that
        such an incident is ineligible merely because evidence is delayed.
        """
        now = _utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT id, status FROM jobs WHERE incident_id = ? AND job_type = 'proactive_dry_run_assessment'",
                (incident_id,),
            ).fetchone()
            if existing and existing["status"] in {"queued", "leased"}:
                return False
            if existing:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', available_at = NULL, lease_expires_at = NULL,
                        completed_at = NULL, last_error = NULL
                    WHERE id = ?
                    """,
                    (existing["id"],),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO jobs (incident_id, job_type, status, created_at)
                    VALUES (?, 'proactive_dry_run_assessment', 'queued', ?)
                    """,
                    (incident_id, now),
                )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'proactive.queued', ?, ?, ?)
                """,
                (
                    incident_id,
                    "Proactive Agent Team qualification queued after client-IP evidence was collected",
                    _canonical_json({"mode": "dry_run", "evidence_gate": "passed"}),
                    now,
                ),
            )
        return True

    def record_proactive_recommendation(self, incident_id: str, recommendation_id: str, action_type: str) -> None:
        """Record that a restricted team materialized a human-review draft."""
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'proactive.recommendation_created', ?, ?, ?)
                """,
                (
                    incident_id,
                    f"Agent Team created a draft recommendation: {action_type.replace('_', ' ')}",
                    _canonical_json({"recommendation_id": recommendation_id, "action_type": action_type}),
                    now,
                ),
            )

    def record_job_started(self, job: Mapping[str, Any]) -> None:
        """Append a concise, user-visible workflow stage start event."""
        job_type = str(job.get("job_type") or "")
        details = {
            "investigate_incident": (
                "workflow.evidence.running",
                "Deterministic evidence collection is running",
                {"sources": ["traffic_overview", "top_client_ips", "security_policy"]},
            ),
            "recheck_client_ips": (
                "workflow.evidence.running",
                "Delayed client-IP evidence recheck is running",
                {"sources": ["top_client_ips"]},
            ),
            "proactive_dry_run_assessment": (
                "workflow.qualification.running",
                "Proactive demo qualification is running",
                {"mode": "dry_run"},
            ),
            "proactive_agent_assessment": (
                "workflow.agent_team.running",
                "Restricted Agent Team is assessing the incident",
                {"roles": ["traffic analyst", "security advisor", "incident commander"]},
            ),
        }.get(job_type)
        if details is None:
            return
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(job["incident_id"]), details[0], details[1], _canonical_json(details[2]), now),
            )

    def ingest(self, payload: Mapping[str, Any]) -> IngestionResult:
        alarm = (
            normalize_alarm(payload)
            if "alarmStatus" in payload
            else normalize_template_alarm(payload)
        )
        fingerprint = delivery_fingerprint(payload)
        raw_payload = _canonical_json(payload)
        normalized_payload = _canonical_json(asdict(alarm))
        now = _utc_now()

        with self._connection() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO alarm_deliveries (fingerprint, raw_payload, normalized_payload, received_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (fingerprint, raw_payload, normalized_payload, now),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT delivery_id, incident_id
                    FROM alarm_occurrences
                    WHERE delivery_id = (
                        SELECT id FROM alarm_deliveries WHERE fingerprint = ?
                    )
                    """,
                    (fingerprint,),
                ).fetchone()
                if row is None:
                    raise
                return IngestionResult(
                    delivery_id=int(row["delivery_id"]),
                    incident_id=str(row["incident_id"]),
                    duplicate=True,
                    created_incident=False,
                    queued_investigation=False,
                    source_status=alarm.status,
                )

            delivery_id = int(cursor.lastrowid)
            incident_key = correlation_key(alarm)
            incident = connection.execute(
                "SELECT id FROM incidents WHERE correlation_key = ?", (incident_key,)
            ).fetchone()
            # A recovery can be the first callback received after deployment,
            # while its active ticket was created before template ``policy_id``
            # was included in the primary correlation key.
            if incident is None and alarm.status == "resolved":
                legacy_key = _legacy_template_correlation_key(alarm)
                if legacy_key and legacy_key != incident_key:
                    incident = connection.execute(
                        "SELECT id FROM incidents WHERE correlation_key = ?", (legacy_key,)
                    ).fetchone()
            created_incident = incident is None
            if created_incident:
                incident_id = f"INC-{uuid4().hex[:12].upper()}"
                unmatched_recovery = alarm.status == "resolved"
                connection.execute(
                    """
                    INSERT INTO incidents (
                        id, correlation_key, title, namespace, policy_id, status,
                        first_seen_at, last_seen_at, source_resolved_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id,
                        incident_key,
                        f"Unmatched recovery notification: {_incident_title(alarm)}" if unmatched_recovery else _incident_title(alarm),
                        alarm.namespace,
                        alarm.policy_id,
                        "received" if alarm.status == "active" else "recovery_unmatched",
                        alarm.first_occurred_at or now,
                        now,
                        alarm.recovered_at if alarm.status == "resolved" else None,
                        now,
                        now,
                    ),
                )
            else:
                incident_id = str(incident["id"])
                connection.execute(
                    """
                    UPDATE incidents
                    SET last_seen_at = ?, status = ?, source_resolved_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        "source_resolved" if alarm.status == "resolved" else "received",
                        alarm.recovered_at if alarm.status == "resolved" else None,
                        now,
                        incident_id,
                    ),
                )

            event_type = "alarm.resolved" if alarm.status == "resolved" else "alarm.received"
            connection.execute(
                """
                INSERT INTO alarm_occurrences (incident_id, delivery_id, event_kind, occurred_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, delivery_id, alarm.status, alarm.recovered_at or alarm.first_occurred_at, now),
            )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, event_type, _event_summary(alarm), normalized_payload, now),
            )
            if alarm.status == "resolved" and not created_incident:
                cancelled = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled', lease_expires_at = NULL, completed_at = ?,
                        last_error = 'cancelled: TCOP recovery notification received'
                    WHERE incident_id = ? AND status = 'queued'
                    """,
                    (now, incident_id),
                ).rowcount
                if cancelled:
                    connection.execute(
                        """
                        INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                        VALUES (?, 'workflow.cancelled', ?, ?, ?)
                        """,
                        (
                            incident_id,
                            f"TCOP recovery cancelled {cancelled} pending proactive workflow job(s)",
                            _canonical_json({"cancelled_job_count": cancelled, "reason": "tcop_recovery"}),
                            now,
                        ),
                    )

            queued_investigation = False
            if alarm.status == "active" and created_incident:
                proactive_mode = connection.execute(
                    "SELECT proactive_enabled FROM incident_automation_settings WHERE id = 1"
                ).fetchone()
                proactive_enabled = bool(proactive_mode and proactive_mode["proactive_enabled"])
                delay_minutes = _proactive_evidence_delay_minutes() if proactive_enabled else 0
                available_at = (
                    (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).isoformat().replace("+00:00", "Z")
                    if delay_minutes else None
                )
                connection.execute(
                    """
                    INSERT INTO jobs (incident_id, job_type, status, created_at, available_at)
                    VALUES (?, 'investigate_incident', 'queued', ?, ?)
                    """,
                    (incident_id, now, available_at),
                )
                queued_investigation = True
                if proactive_enabled:
                    connection.execute(
                        """
                        INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                        VALUES (?, 'proactive.queued', ?, ?, ?)
                        """,
                        (
                            incident_id,
                            f"Proactive Agent Team evidence collection is scheduled after EdgeOne's {delay_minutes}-minute reporting delay",
                            _canonical_json({"mode": "dry_run", "evidence_gate": "waiting", "available_at": available_at, "delay_minutes": delay_minutes}),
                            now,
                        ),
                    )

            return IngestionResult(
                delivery_id=delivery_id,
                incident_id=incident_id,
                duplicate=False,
                created_incident=created_incident,
                queued_investigation=queued_investigation,
                source_status=alarm.status,
            )

    def count_rows(self, table_name: str) -> int:
        if table_name not in {"incidents", "alarm_deliveries", "alarm_occurrences", "incident_events", "jobs", "reconciliation_runs", "reconciliation_observations"}:
            raise ValueError("unsupported table")
        with self._connection() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])

    def incident_status(self, incident_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute("SELECT status FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            raise KeyError(incident_id)
        return str(row["status"])

    def is_incident_actionable(self, incident_id: str) -> bool:
        """Whether TCOP still considers this case safe to remediate."""
        return self.incident_status(incident_id) not in {"source_resolved", "recovery_unmatched"}

    def cancel_leased_job(self, job_id: int, *, summary: str) -> None:
        """Stop a claimed job when a TCOP recovery makes further work unsafe."""
        now = _utc_now()
        with self._connection() as connection:
            job = connection.execute("SELECT incident_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            connection.execute(
                """
                UPDATE jobs
                SET status = 'cancelled', lease_expires_at = NULL, completed_at = ?, last_error = ?
                WHERE id = ? AND status = 'leased'
                """,
                (now, "cancelled: TCOP recovery notification received", job_id),
            )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'workflow.cancelled', ?, ?, ?)
                """,
                (str(job["incident_id"]), summary, _canonical_json({"job_id": job_id}), now),
            )

    def delete_incidents(self, incident_ids: list[str]) -> dict[str, list[str]]:
        """Delete one or more case files and their derived operational data.

        Raw TCOP deliveries are deliberately retained for callback idempotency
        and provider-audit purposes. A case file cannot be removed while a
        worker holds one of its jobs; callers must retry after that work ends.
        """
        unique_ids = list(dict.fromkeys(str(value) for value in incident_ids if str(value)))
        if not unique_ids:
            raise AlarmValidationError("provide at least one incident id")
        if len(unique_ids) > 100:
            raise AlarmValidationError("at most 100 incident tickets can be deleted at once")
        placeholders = ", ".join("?" for _ in unique_ids)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_rows = connection.execute(
                f"SELECT id FROM incidents WHERE id IN ({placeholders})", unique_ids
            ).fetchall()
            existing = [str(row["id"]) for row in existing_rows]
            existing_set = set(existing)
            missing = [incident_id for incident_id in unique_ids if incident_id not in existing_set]
            if not existing:
                return {"deleted_ids": [], "missing_ids": missing}
            existing_placeholders = ", ".join("?" for _ in existing)
            leased = connection.execute(
                f"SELECT DISTINCT incident_id FROM jobs WHERE incident_id IN ({existing_placeholders}) AND status = 'leased'",
                existing,
            ).fetchall()
            if leased:
                busy = ", ".join(str(row["incident_id"]) for row in leased)
                raise AlarmValidationError(f"cannot delete ticket(s) with active worker jobs: {busy}")

            # Delete dependent case-file data first. The delivery rows remain
            # as an immutable record of what TCOP sent to this service.
            connection.execute(f"DELETE FROM recommendations WHERE incident_id IN ({existing_placeholders})", existing)
            connection.execute(f"DELETE FROM jobs WHERE incident_id IN ({existing_placeholders})", existing)
            connection.execute(f"DELETE FROM incident_read_state WHERE incident_id IN ({existing_placeholders})", existing)
            connection.execute(f"DELETE FROM incident_events WHERE incident_id IN ({existing_placeholders})", existing)
            connection.execute(
                f"UPDATE reconciliation_observations SET incident_id = NULL WHERE incident_id IN ({existing_placeholders})",
                existing,
            )
            connection.execute(f"DELETE FROM alarm_occurrences WHERE incident_id IN ({existing_placeholders})", existing)
            connection.execute(f"DELETE FROM incidents WHERE id IN ({existing_placeholders})", existing)
        return {"deleted_ids": existing, "missing_ids": missing}

    def list_incidents(
        self, status: str | None = None, limit: int = 50, *, viewer_id: str = "default-operator"
    ) -> list[dict[str, Any]]:
        """Return activity-ordered case summaries with per-viewer unread state."""
        safe_limit = max(1, min(limit, 100))
        query = """
            SELECT incidents.*, EXISTS(
                SELECT 1 FROM jobs
                WHERE jobs.incident_id = incidents.id AND jobs.status IN ('queued', 'leased')
            ) AS investigation_queued,
            EXISTS(
                SELECT 1 FROM recommendations
                WHERE recommendations.incident_id = incidents.id AND recommendations.status = 'executed'
            ) AS remediation_executed,
            COALESCE(activity.latest_event_id, 0) AS latest_event_id,
            COALESCE(activity.last_activity_at, incidents.updated_at) AS last_activity_at,
            CASE WHEN COALESCE(activity.latest_event_id, 0) > COALESCE(read_state.last_read_event_id, 0)
                THEN 1 ELSE 0 END AS is_unread
            FROM incidents
            LEFT JOIN (
                SELECT incident_id, MAX(id) AS latest_event_id, MAX(created_at) AS last_activity_at
                FROM incident_events
                GROUP BY incident_id
            ) AS activity ON activity.incident_id = incidents.id
            LEFT JOIN incident_read_state AS read_state
                ON read_state.incident_id = incidents.id AND read_state.viewer_id = ?
        """
        parameters: list[Any] = [self._normalize_viewer_id(viewer_id)]
        if status:
            query += " WHERE status = ?"
            parameters.append(status)
        query += " ORDER BY last_activity_at DESC, latest_event_id DESC, incidents.id DESC LIMIT ?"
        parameters.append(safe_limit)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._incident_summary(row) for row in rows]

    def incident_attention(self, *, viewer_id: str, limit: int = 5) -> dict[str, Any]:
        """Return the unread badge count and most recently active case files."""
        viewer_id = self._normalize_viewer_id(viewer_id)
        with self._connection() as connection:
            unread_count = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM incidents
                LEFT JOIN (
                    SELECT incident_id, MAX(id) AS latest_event_id FROM incident_events GROUP BY incident_id
                ) AS activity ON activity.incident_id = incidents.id
                LEFT JOIN incident_read_state AS read_state
                    ON read_state.incident_id = incidents.id AND read_state.viewer_id = ?
                WHERE COALESCE(activity.latest_event_id, 0) > COALESCE(read_state.last_read_event_id, 0)
                """,
                (viewer_id,),
            ).fetchone()[0])
        return {"unread_count": unread_count, "items": self.list_incidents(limit=limit, viewer_id=viewer_id)}

    def mark_incident_read(self, incident_id: str, *, viewer_id: str, through_event_id: int) -> dict[str, Any]:
        """Mark only the events the browser actually loaded as read.

        Resolving the requested id against this incident prevents a later event
        arriving between GET and POST from being accidentally acknowledged.
        """
        if through_event_id < 0:
            raise AlarmValidationError("through_event_id must be non-negative")
        viewer_id = self._normalize_viewer_id(viewer_id)
        now = _utc_now()
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if exists is None:
                raise KeyError(incident_id)
            covered = connection.execute(
                """
                SELECT COALESCE(MAX(id), 0) FROM incident_events
                WHERE incident_id = ? AND id <= ?
                """,
                (incident_id, through_event_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO incident_read_state (viewer_id, incident_id, last_read_event_id, last_read_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(viewer_id, incident_id) DO UPDATE SET
                    last_read_event_id = MAX(incident_read_state.last_read_event_id, excluded.last_read_event_id),
                    last_read_at = excluded.last_read_at
                """,
                (viewer_id, incident_id, int(covered), now),
            )
        return {"incident_id": incident_id, "last_read_event_id": int(covered), "last_read_at": now}

    def mark_all_incidents_read(self, *, viewer_id: str) -> dict[str, Any]:
        """Acknowledge the current event snapshot for every incident."""
        viewer_id = self._normalize_viewer_id(viewer_id)
        now = _utc_now()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT incident_id, MAX(id) AS latest_event_id FROM incident_events GROUP BY incident_id"
            ).fetchall()
            connection.executemany(
                """
                INSERT INTO incident_read_state (viewer_id, incident_id, last_read_event_id, last_read_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(viewer_id, incident_id) DO UPDATE SET
                    last_read_event_id = MAX(incident_read_state.last_read_event_id, excluded.last_read_event_id),
                    last_read_at = excluded.last_read_at
                """,
                [(viewer_id, row["incident_id"], int(row["latest_event_id"]), now) for row in rows],
            )
        return {"marked_incident_count": len(rows), "last_read_at": now}

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        """Return one case file with its normalized source alarm and audit timeline."""
        with self._connection() as connection:
            incident = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            if incident is None:
                return None
            latest_alarm = connection.execute(
                """
                SELECT alarm_deliveries.normalized_payload
                FROM alarm_occurrences
                JOIN alarm_deliveries ON alarm_deliveries.id = alarm_occurrences.delivery_id
                WHERE alarm_occurrences.incident_id = ?
                ORDER BY alarm_occurrences.id DESC
                LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
            events = connection.execute(
                """
                SELECT id, event_type, summary, payload, created_at
                FROM incident_events
                WHERE incident_id = ?
                ORDER BY id ASC
                """,
                (incident_id,),
            ).fetchall()
            job = connection.execute(
                """
                SELECT id, job_type, status, created_at, available_at
                FROM jobs
                WHERE incident_id = ? AND job_type IN ('investigate_incident', 'recheck_client_ips')
                ORDER BY id DESC LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
            workflow_jobs = connection.execute(
                """
                SELECT id, job_type, status, created_at, available_at, completed_at, last_error
                FROM jobs WHERE incident_id = ? ORDER BY id ASC
                """,
                (incident_id,),
            ).fetchall()
            proactive_job = connection.execute(
                """
                SELECT id, job_type, status, created_at
                FROM jobs
                WHERE incident_id = ? AND job_type = 'proactive_dry_run_assessment'
                ORDER BY id DESC LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
            proactive_agent_job = connection.execute(
                """
                SELECT id, job_type, status, created_at
                FROM jobs
                WHERE incident_id = ? AND job_type = 'proactive_agent_assessment'
                ORDER BY id DESC LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
            recommendations = connection.execute(
                "SELECT * FROM recommendations WHERE incident_id = ? ORDER BY version DESC",
                (incident_id,),
            ).fetchall()

        detail = self._incident_summary(incident)
        detail["latest_event_id"] = int(events[-1]["id"]) if events else 0
        detail["last_activity_at"] = events[-1]["created_at"] if events else incident["updated_at"]
        detail["source_alarm"] = json.loads(latest_alarm["normalized_payload"]) if latest_alarm else None
        detail["timeline"] = [
            {
                "id": row["id"],
                "type": row["event_type"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in events
        ]
        detail["job"] = dict(job) if job else None
        waiting_for_evidence = bool(
            job
            and job["status"] == "queued"
            and job["available_at"]
            and str(job["available_at"]) > _utc_now()
        )
        detail["investigation_queued"] = bool(job and job["status"] in {"queued", "leased"} and not waiting_for_evidence)
        detail["evidence_recheck_waiting"] = waiting_for_evidence
        detail["next_evidence_recheck_at"] = str(job["available_at"]) if waiting_for_evidence else None
        detail["proactive_assessment_queued"] = bool(
            proactive_job and proactive_job["status"] in {"queued", "leased"}
        )
        detail["proactive_agent_assessment_queued"] = bool(
            proactive_agent_job and proactive_agent_job["status"] in {"queued", "leased"}
        )
        detail["recommendations"] = [self._recommendation_dict(row) for row in recommendations]
        # Keep TCOP's source-alarm lifecycle separate from remediation. A
        # recovery callback remains safety-relevant even after EdgeOne action
        # succeeded, but the UI needs both facts to avoid calling a mitigated
        # case merely "resolved by TCOP".
        detail["remediation_executed"] = any(
            recommendation["status"] == "executed" for recommendation in detail["recommendations"]
        )
        detail["proactive_workflow"] = self._proactive_workflow_dict(workflow_jobs, detail["recommendations"])
        return detail

    def record_reconciliation(
        self,
        *,
        window_start_at: str,
        window_end_at: str,
        histories: list[Mapping[str, Any]],
        backfill_missing: bool = True,
    ) -> dict[str, Any]:
        """Persist a read-only TCOP/history comparison for audit and follow-up.

        Missing callback deliveries are observations only. Reconciliation must
        never enqueue an investigation because history records lack the full
        callback payload required for a safe response.
        """
        now = _utc_now()
        observations: list[dict[str, Any]] = []
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reconciliation_runs (
                    window_start_at, window_end_at, source_count, matched_count,
                    missing_count, status, created_at, completed_at
                ) VALUES (?, ?, 0, 0, 0, 'completed', ?, ?)
                """,
                (window_start_at, window_end_at, now, now),
            )
            run_id = int(cursor.lastrowid)
            matched_count = 0
            backfilled_count = 0
            for history in histories:
                policy_id = _text(history.get("PolicyId"))
                occurred_at = _unix_timestamp_to_utc(history.get("FirstOccurTime"))
                incident = None
                if policy_id and occurred_at:
                    incident = connection.execute(
                        """
                        SELECT id FROM incidents
                        WHERE policy_id = ? AND first_seen_at = ?
                        LIMIT 1
                        """,
                        (policy_id, occurred_at),
                    ).fetchone()
                incident_id = str(incident["id"]) if incident else None
                match_status = "matched" if incident_id else "missing_callback"
                matched_count += int(incident_id is not None)
                if incident_id is None and backfill_missing:
                    incident_id = self._backfill_history_incident(
                        connection,
                        history=history,
                        first_occurred_at=occurred_at,
                        now=now,
                    )
                    match_status = "backfilled"
                    backfilled_count += 1
                external_alarm_id = _text(history.get("AlarmId"))
                connection.execute(
                    """
                    INSERT INTO reconciliation_observations (
                        run_id, external_alarm_id, policy_id, namespace,
                        first_occurred_at, match_status, incident_id,
                        source_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        external_alarm_id,
                        policy_id,
                        _text(history.get("Namespace")),
                        occurred_at,
                        match_status,
                        incident_id,
                        _canonical_json(dict(history)),
                        now,
                    ),
                )
                observations.append(
                    {
                        "external_alarm_id": external_alarm_id,
                        "policy_id": policy_id,
                        "first_occurred_at": occurred_at,
                        "status": match_status,
                        "incident_id": incident_id,
                    }
                )
            missing_count = len(histories) - matched_count
            connection.execute(
                """
                UPDATE reconciliation_runs
                SET source_count = ?, matched_count = ?, missing_count = ?
                WHERE id = ?
                """,
                (len(histories), matched_count, missing_count, run_id),
            )
        return {
            "run_id": run_id,
            "window_start_at": window_start_at,
            "window_end_at": window_end_at,
            "source_count": len(histories),
            "matched_count": matched_count,
            "missing_count": len(histories) - matched_count,
            "backfilled_count": backfilled_count,
            "observations": observations,
        }

    def create_block_ip_recommendation(
        self,
        incident_id: str,
        *,
        client_ip: str,
        proposed_rule: Mapping[str, Any] | None = None,
        baseline_policy_fingerprint: str | None = None,
        action_type: str = "block_client_ip",
    ) -> dict[str, Any]:
        """Create an immutable, unapproved block-IP recommendation version."""
        incident = self.get_incident(incident_id)
        if incident is None:
            raise KeyError(incident_id)
        alarm = incident.get("source_alarm") or {}
        dimensions = alarm.get("dimensions") or {}
        zone_id = dimensions.get("zoneid") or dimensions.get("zoneId")
        host = dimensions.get("domain")
        if not zone_id:
            raise AlarmValidationError("incident source alarm does not identify a zone")
        now = _utc_now()
        with self._connection() as connection:
            version = int(connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM recommendations WHERE incident_id = ?", (incident_id,)).fetchone()[0])
            recommendation_id = f"REC-{uuid4().hex[:12].upper()}"
            rule = dict(proposed_rule or {
                "Id": None,
                "Name": f"Incident {incident_id}: block {client_ip}",
                "Condition": f"${{http.request.ip}} in ['{client_ip}']",
                "Action": {"Name": "Deny"},
                "Priority": None,
                "Enabled": "on",
            })
            target = {"zone_id": zone_id, "host": host, "client_ip": client_ip}
            connection.execute(
                """
                INSERT INTO recommendations (
                    id, incident_id, version, status, action_type, target,
                    proposed_rule, baseline_policy_fingerprint, created_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                """,
                (recommendation_id, incident_id, version, action_type, _canonical_json(target), _canonical_json(rule), baseline_policy_fingerprint, now),
            )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'recommendation.created', ?, ?, ?)
                """,
                (
                    incident_id,
                    (
                        f"Draft recommendation: rate limit client IP {client_ip}"
                        if action_type == "rate_limit_client_ip"
                        else f"Draft recommendation: block client IP {client_ip}"
                    ),
                    _canonical_json({"recommendation_id": recommendation_id, "version": version, "target": target}),
                    now,
                ),
            )
        return self.get_recommendation(recommendation_id)  # type: ignore[return-value]

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
        if row is None:
            return None
        return self._recommendation_dict(row)

    def approve_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "draft":
                raise AlarmValidationError("only a draft recommendation can be approved")
            connection.execute("UPDATE recommendations SET status = 'approved', approved_at = ? WHERE id = ?", (now, recommendation_id))
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'recommendation.approved', ?, ?, ?)
                """,
                (row["incident_id"], "Recommendation approved; awaiting guarded execution", _canonical_json({"recommendation_id": recommendation_id}), now),
            )
        return self.get_recommendation(recommendation_id)

    def auto_approve_proactive_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        """Approve a qualified demo recommendation and record the non-human actor."""
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "draft":
                raise AlarmValidationError("only a draft recommendation can be auto-approved")
            connection.execute("UPDATE recommendations SET status = 'approved', approved_at = ? WHERE id = ?", (now, recommendation_id))
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'proactive.recommendation_auto_approved', ?, ?, ?)
                """,
                (
                    row["incident_id"],
                    "Proactive Demo mode auto-approved the validated IP-block recommendation; execution is still pending.",
                    _canonical_json({"recommendation_id": recommendation_id, "action_type": row["action_type"]}),
                    now,
                ),
            )
        return self.get_recommendation(recommendation_id)

    def mark_recommendation_executed(
        self, recommendation_id: str, *, result: Mapping[str, Any], source: str = "operator"
    ) -> dict[str, Any] | None:
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "approved":
                raise AlarmValidationError("only an approved recommendation can be executed")
            connection.execute("UPDATE recommendations SET status = 'executed', executed_at = ? WHERE id = ?", (now, recommendation_id))
            autonomous = source == "proactive_demo"
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["incident_id"],
                    "proactive.remediation.executed" if autonomous else "remediation.executed",
                    "Proactive Demo mode applied the validated IP block" if autonomous else "Approved IP block applied",
                    _canonical_json({"recommendation_id": recommendation_id, "source": source, **dict(result)}),
                    now,
                ),
            )
        return self.get_recommendation(recommendation_id)

    def mark_recommendation_stale(self, recommendation_id: str, *, reason: str) -> dict[str, Any] | None:
        """Retire an invalidated draft so it cannot be retried accidentally."""
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "approved":
                connection.execute("UPDATE recommendations SET status = 'stale' WHERE id = ?", (recommendation_id,))
                connection.execute(
                    """
                    INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                    VALUES (?, 'recommendation.stale', ?, ?, ?)
                    """,
                    (row["incident_id"], "Recommendation invalidated by a policy change", _canonical_json({"recommendation_id": recommendation_id, "reason": reason}), now),
                )
        return self.get_recommendation(recommendation_id)

    def record_remediation_failure(self, recommendation_id: str, *, error: str, request_id: str | None) -> None:
        """Keep approval intact while making a rejected provider write auditable."""
        now = _utc_now()
        with self._connection() as connection:
            row = connection.execute("SELECT incident_id FROM recommendations WHERE id = ?", (recommendation_id,)).fetchone()
            if row is None:
                return
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'remediation.failed', ?, ?, ?)
                """,
                (row["incident_id"], "EdgeOne rejected the approved rule", _canonical_json({"recommendation_id": recommendation_id, "error": error, "request_id": request_id}), now),
            )

    @staticmethod
    def _recommendation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "incident_id": row["incident_id"], "version": row["version"],
            "status": row["status"], "action_type": row["action_type"],
            "target": json.loads(row["target"]), "proposed_rule": json.loads(row["proposed_rule"]),
            "baseline_policy_fingerprint": row["baseline_policy_fingerprint"], "created_at": row["created_at"],
            "approved_at": row["approved_at"], "executed_at": row["executed_at"],
        }

    @staticmethod
    def _proactive_workflow_dict(jobs: list[sqlite3.Row], recommendations: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Expose a compact status view without exposing model reasoning."""
        by_type = {str(job["job_type"]): job for job in jobs}

        def job_status(*types: str) -> str:
            matches = [by_type[item] for item in types if item in by_type]
            if not matches:
                return "not_started"
            statuses = {str(item["status"]) for item in matches}
            if "leased" in statuses:
                return "running"
            if "queued" in statuses:
                now = _utc_now()
                queued = [item for item in matches if item["status"] == "queued"]
                if queued and all(
                    item["available_at"]
                    and str(item["available_at"]) > now
                    for item in queued
                ):
                    return "waiting"
                return "queued"
            if "cancelled" in statuses:
                return "cancelled"
            if "failed" in statuses:
                return "failed"
            return "completed"

        latest = recommendations[0] if recommendations else None
        execution_status = "not_started"
        if latest:
            execution_status = {
                "executed": "completed",
                "approved": "queued",
                "stale": "failed",
            }.get(str(latest.get("status")), "not_started")
        stages = [
            {"id": "evidence", "label": "Evidence collection", "status": job_status("investigate_incident", "recheck_client_ips")},
            {"id": "qualification", "label": "Proactive qualification", "status": job_status("proactive_dry_run_assessment")},
            {"id": "agent_team", "label": "Agent Team assessment", "status": job_status("proactive_agent_assessment")},
            {"id": "execution", "label": "Deterministic execution", "status": execution_status},
            {"id": "verification", "label": "Post-change verification", "status": "pending" if execution_status == "completed" else "not_started"},
        ]
        return {
            "stages": stages,
            "is_active": any(stage["status"] in {"queued", "running", "pending"} for stage in stages[:4]),
            "recommendation_id": latest.get("id") if latest else None,
        }

    @staticmethod
    def _backfill_history_incident(
        connection: sqlite3.Connection,
        *,
        history: Mapping[str, Any],
        first_occurred_at: str | None,
        now: str,
    ) -> str:
        """Create an audit-only case file for a confirmed missed callback."""
        policy_id = _text(history.get("PolicyId"))
        namespace = _text(history.get("Namespace"))
        external_id = _text(history.get("AlarmId"))
        key = sha256(
            _canonical_json(
                {
                    "provider": "tencent_tcop_history",
                    "policy_id": policy_id,
                    "alarm_id": external_id,
                    "first_occurred_at": first_occurred_at,
                }
            ).encode("utf-8")
        ).hexdigest()
        existing = connection.execute("SELECT id FROM incidents WHERE correlation_key = ?", (key,)).fetchone()
        if existing:
            return str(existing["id"])
        source_status = (_text(history.get("AlarmStatus")) or "").upper()
        incident_id = f"INC-{uuid4().hex[:12].upper()}"
        title = _text(history.get("PolicyName")) or _text(history.get("Content")) or "TCOP historical alarm"
        status = "source_resolved" if source_status == "OK" else "history_imported"
        connection.execute(
            """
            INSERT INTO incidents (
                id, correlation_key, title, namespace, policy_id, status,
                first_seen_at, last_seen_at, source_resolved_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id, key, title, namespace, policy_id, status,
                first_occurred_at or now, now,
                first_occurred_at if status == "source_resolved" else None,
                now, now,
            ),
        )
        connection.execute(
            """
            INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
            VALUES (?, 'reconciliation.backfilled', ?, ?, ?)
            """,
            (incident_id, "TCOP history backfilled a missed callback", _canonical_json(dict(history)), now),
        )
        return incident_id

    def claim_next_job(
        self, *, lease_seconds: int = 120, incident_id: str | None = None
    ) -> dict[str, Any] | None:
        """Lease one queued job; expired leases are safely returned to queue."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        now_text = now.isoformat().replace("+00:00", "Z")
        expires_at = (now + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs SET status = 'queued', lease_expires_at = NULL
                WHERE status = 'leased' AND lease_expires_at < ?
                """,
                (now_text,),
            )
            query = """
                SELECT * FROM jobs
                WHERE status = 'queued' AND (available_at IS NULL OR available_at <= ?)
            """
            parameters: list[Any] = [now_text]
            if incident_id:
                query += " AND incident_id = ?"
                parameters.append(incident_id)
            query += " ORDER BY id ASC LIMIT 1"
            row = connection.execute(query, parameters).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'leased', lease_expires_at = ?, attempts = attempts + 1, last_error = NULL
                WHERE id = ?
                """,
                (expires_at, row["id"]),
            )
            claimed = dict(row)
            claimed.update({"status": "leased", "lease_expires_at": expires_at, "attempts": int(row["attempts"]) + 1})
            return claimed

    def complete_job(self, job_id: int, *, summary: str, payload: Mapping[str, Any]) -> None:
        """Complete a leased job and append its redacted public outcome."""
        now = _utc_now()
        with self._connection() as connection:
            job = connection.execute("SELECT id, incident_id, job_type, attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            incident_id = str(job["incident_id"])
            connection.execute(
                """
                UPDATE jobs
                SET status = 'completed', lease_expires_at = NULL, completed_at = ?
                WHERE id = ? AND status = 'leased'
                """,
                (now, job_id),
            )
            event_type = {
                "proactive_dry_run_assessment": "proactive.assessment.completed",
                "proactive_agent_assessment": "proactive.agent_assessment.completed",
            }.get(job["job_type"], "investigation.completed")
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, event_type, summary, _canonical_json(dict(payload)), now),
            )
            if not str(job["job_type"]).startswith("proactive_") and self._should_schedule_ip_recheck(payload):
                self._schedule_ip_recheck(connection, incident_id=incident_id, completed_job=job, now=now)
            if not str(job["job_type"]).startswith("proactive_"):
                incident_status = (
                    "insufficient_evidence"
                    if payload.get("outcome") == "insufficient_evidence"
                    else "investigated"
                )
                connection.execute(
                    """
                    UPDATE incidents SET status = ?, updated_at = ?
                    WHERE id = ? AND status NOT IN ('source_resolved', 'recovery_unmatched')
                    """,
                    (incident_status, now, incident_id),
                )

    def fail_job(self, job_id: int, *, error: str, max_attempts: int = 3) -> None:
        """Return a failed lease to the queue, or make terminal failure visible."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = _utc_now()
        with self._connection() as connection:
            job = connection.execute("SELECT incident_id, attempts FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            terminal = int(job["attempts"]) >= max_attempts
            next_status = "failed" if terminal else "queued"
            connection.execute(
                """
                UPDATE jobs SET status = ?, lease_expires_at = NULL, last_error = ?, completed_at = ?
                WHERE id = ?
                """,
                (next_status, error[:500], now if terminal else None, job_id),
            )
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(job["incident_id"]),
                    "investigation.failed" if terminal else "investigation.retry_scheduled",
                    "Investigation failed" if terminal else "Investigation will retry",
                    _canonical_json({"error": error[:500], "attempt": int(job["attempts"]), "terminal": terminal}),
                    now,
                ),
            )

    @staticmethod
    def _incident_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "namespace": row["namespace"],
            "policy_id": row["policy_id"],
            "status": row["status"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "source_resolved_at": row["source_resolved_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "investigation_queued": bool(row["investigation_queued"])
            if "investigation_queued" in row.keys()
            else False,
            "remediation_executed": bool(row["remediation_executed"])
            if "remediation_executed" in row.keys()
            else False,
            "latest_event_id": int(row["latest_event_id"]) if "latest_event_id" in row.keys() else 0,
            "last_activity_at": row["last_activity_at"] if "last_activity_at" in row.keys() else row["updated_at"],
            "is_unread": bool(row["is_unread"]) if "is_unread" in row.keys() else False,
        }

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _add_column_if_missing(connection: sqlite3.Connection, table: str, definition: str) -> None:
        column = definition.split()[0]
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _normalize_viewer_id(value: str | None) -> str:
        candidate = (value or "default-operator").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", candidate):
            raise AlarmValidationError("invalid incident viewer id")
        return candidate

    @staticmethod
    def _should_schedule_ip_recheck(payload: Mapping[str, Any]) -> bool:
        if payload.get("outcome") != "insufficient_evidence" or payload.get("failures"):
            return False
        return any(
            isinstance(item, Mapping) and item.get("source") == "top_client_ips" and not item.get("candidates")
            for item in (payload.get("evidence") or [])
        )

    @staticmethod
    def _schedule_ip_recheck(
        connection: sqlite3.Connection, *, incident_id: str, completed_job: sqlite3.Row, now: str
    ) -> None:
        """Schedule at most three delayed client-IP evidence attempts.

        The same durable recheck job is reused, preserving its attempt count
        and avoiding an unbounded loop when EdgeOne has no logs for the zone.
        """
        max_attempts = 3
        job_type = str(completed_job["job_type"])
        attempts = int(completed_job["attempts"])
        if job_type == "recheck_client_ips" and attempts >= max_attempts:
            connection.execute(
                """
                INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
                VALUES (?, 'evidence.recheck_exhausted', ?, ?, ?)
                """,
                (
                    incident_id,
                    "Client-IP evidence is still unavailable after the bounded reporting-delay rechecks",
                    _canonical_json({"attempts": attempts, "max_attempts": max_attempts}),
                    now,
                ),
            )
            return

        next_attempt = attempts + 1 if job_type == "recheck_client_ips" else 1
        available_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
        if job_type == "recheck_client_ips":
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', available_at = ?, lease_expires_at = NULL,
                    completed_at = NULL, last_error = NULL
                WHERE id = ?
                """,
                (available_at, completed_job["id"]),
            )
        else:
            existing = connection.execute(
                "SELECT id, status, attempts FROM jobs WHERE incident_id = ? AND job_type = 'recheck_client_ips'",
                (incident_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO jobs (incident_id, job_type, status, created_at, available_at)
                    VALUES (?, 'recheck_client_ips', 'queued', ?, ?)
                    """,
                    (incident_id, now, available_at),
                )
            elif existing["status"] in {"completed", "failed"} and int(existing["attempts"]) < max_attempts:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', available_at = ?, lease_expires_at = NULL,
                        completed_at = NULL, last_error = NULL
                    WHERE id = ?
                    """,
                    (available_at, existing["id"]),
                )
            else:
                return
        connection.execute(
            """
            INSERT INTO incident_events (incident_id, event_type, summary, payload, created_at)
            VALUES (?, 'evidence.recheck_scheduled', ?, ?, ?)
            """,
            (
                incident_id,
                f"Client-IP data is not ready; automatic recheck {next_attempt} of {max_attempts} is scheduled after EdgeOne's reporting delay",
                _canonical_json({"attempt": next_attempt, "max_attempts": max_attempts, "available_at": available_at}),
                now,
            ),
        )


def _incident_title(alarm: NormalizedAlarm) -> str:
    return alarm.policy_name or alarm.metric_display_name or alarm.metric_name or "TCOP alarm"


def _event_summary(alarm: NormalizedAlarm) -> str:
    action = "resolved" if alarm.status == "resolved" else "received"
    return f"TCOP alarm {action}: {_incident_title(alarm)}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _proactive_evidence_delay_minutes() -> int:
    """Return the bounded initial delay for proactive top-N evidence."""
    try:
        configured = int(os.environ.get("INCIDENT_PROACTIVE_EVIDENCE_DELAY_MINUTES", "15"))
    except ValueError:
        configured = 15
    return max(10, min(configured, 60))


def _unix_timestamp_to_utc(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError) as exc:
        raise AlarmValidationError("FirstOccurTime must be a Unix timestamp") from exc
