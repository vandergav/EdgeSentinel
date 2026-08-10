"""Read-only comparison of TCOP alarm history with callback-backed case files."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Callable

from .alarm_ingestion import IncidentStore


class ReconciliationDisabledError(RuntimeError):
    """Raised when an operator has not explicitly enabled remote history reads."""


HistoryFetcher = Callable[..., dict[str, Any]]


def reconciliation_enabled() -> bool:
    return os.environ.get("TCOP_RECONCILIATION_ENABLED", "false").lower() in {"1", "true", "yes"}


def reconcile_alarm_history(
    store: IncidentStore,
    *,
    lookback_minutes: int,
    policy_id: str | None = None,
    fetch_history: HistoryFetcher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read TCOP history and record unmatched callback deliveries.

    This function never changes an incident, creates a job, or calls an agent.
    It is deliberately operator-triggered until a monitored scheduler is added.
    """
    if not reconciliation_enabled():
        raise ReconciliationDisabledError("TCOP reconciliation is disabled")
    if not 1 <= lookback_minutes <= 24 * 60:
        raise ValueError("lookback_minutes must be between 1 and 1440")

    if fetch_history is None:
        # Import only for a real run. edgeone_agents' package initializer loads
        # ADK, which is deliberately unavailable in lightweight ingestion tests.
        from edgeone_agents.monitoring import describe_alarm_histories

        fetch_history = describe_alarm_histories

    end = now or datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())
    histories: list[dict[str, Any]] = []
    page = 1
    while True:
        response = fetch_history(
            start_time=start_epoch,
            end_time=end_epoch,
            page_number=page,
            page_size=100,
            policy_id=policy_id,
        )
        batch = response.get("Histories", [])
        if not isinstance(batch, list):
            raise ValueError("TCOP DescribeAlarmHistories returned an invalid Histories field")
        histories.extend(item for item in batch if isinstance(item, dict))
        total = response.get("TotalCount", len(histories))
        if len(batch) < 100 or len(histories) >= int(total):
            break
        page += 1

    return store.record_reconciliation(
        window_start_at=start.isoformat().replace("+00:00", "Z"),
        window_end_at=end.isoformat().replace("+00:00", "Z"),
        histories=histories,
        backfill_missing=True,
    )
