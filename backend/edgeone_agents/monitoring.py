"""Read-only Tencent Cloud Observability (TCOP) history access.

This is deliberately outside the agent tool lists: reconciliation is an
application reliability task, not an LLM decision.  Keeping the Tencent API
call under ``edgeone_agents`` preserves the repository's single boundary for
Tencent-specific protocol and field knowledge.
"""
from __future__ import annotations

import os
from typing import Any

from .real_tools._client import call_tencent_api


MONITOR_SERVICE = "monitor"
MONITOR_ENDPOINT = "monitor.intl.tencentcloudapi.com"
MONITOR_API_VERSION = "2018-07-24"


def describe_alarm_histories(
    *,
    start_time: int,
    end_time: int,
    page_number: int = 1,
    page_size: int = 100,
    policy_id: str | None = None,
) -> dict[str, Any]:
    """Return one page of TCOP alarm records for a bounded Unix-time window.

    Args:
        start_time: Inclusive Unix timestamp of first occurrence.
        end_time: Exclusive Unix timestamp of first occurrence.
        page_number: TCOP page number, starting at one.
        page_size: Number of records, from one to 100.

    Returns:
        TCOP's unwrapped ``DescribeAlarmHistories`` response.
    """
    if start_time >= end_time:
        raise ValueError("start_time must precede end_time")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    params: dict[str, Any] = {
        "Module": "monitor",
        "PageNumber": page_number,
        "PageSize": page_size,
        "Order": "ASC",
        "StartTime": start_time,
        "EndTime": end_time,
    }
    if policy_id:
        params["PolicyIds"] = [policy_id]
    return call_tencent_api(
        "DescribeAlarmHistories",
        params,
        service=MONITOR_SERVICE,
        endpoint=os.environ.get("TCOP_MONITOR_ENDPOINT", MONITOR_ENDPOINT),
        version=os.environ.get("TCOP_MONITOR_API_VERSION", MONITOR_API_VERSION),
        timeout=float(os.environ.get("TCOP_RECONCILIATION_TIMEOUT_SECONDS", "15")),
    )
