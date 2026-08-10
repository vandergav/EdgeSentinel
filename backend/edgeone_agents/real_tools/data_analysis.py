"""
Real, live-API tools for the "Data Analysis APIs" category
(see api_catalog.CATALOG["data_analysis"]) — the read side an agent uses to
diagnose "why is my site slow / under attack / caching badly" questions.

Implemented: DescribeOverviewL7Data, DescribeTopL7AnalysisData.
Everything else (DDoS-specific series, origin-pull timing, cache timing,
L4 timing, ...) falls back to the catalog stub tools until someone adds it
here — see real_tools/README notes in agent_builder.py for the pattern.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from ._client import call_edgeone_api, clean, tencent_api

# The metric names EdgeOne documents for DescribeOverviewL7Data.
OVERVIEW_METRICS = (
    "l7Flow_outFlux",       # response traffic
    "l7Flow_inFlux",        # request traffic
    "l7Flow_outBandwidth",  # response bandwidth
    "l7Flow_inBandwidth",   # request bandwidth
    "l7Flow_hit_outFlux",   # cache-hit traffic
    "l7Flow_request",       # request count
    "l7Flow_flux",          # up+down traffic
    "l7Flow_bandwidth",     # up+down bandwidth
)


def _resolve_window(
    start_time: Optional[str], end_time: Optional[str], lookback_minutes: Optional[int]
) -> tuple[str, str]:
    """Turns (start_time, end_time, lookback_minutes) into a concrete
    (StartTime, EndTime) pair.

    `lookback_minutes`, when given, wins and is computed from the real
    system clock right here in Python — this is deliberate: it's what lets
    a request like "traffic in the last 30 minutes" resolve correctly even
    if the model calling this tool has no idea what today's date is.
    Explicit start_time/end_time are for when the user names a specific
    historical window instead (e.g. "last Tuesday 2-4pm") — for that case
    the agent should call get_current_time first to anchor the relative
    language, since this function can't do that anchoring for you.

    Raises:
        ValueError: if neither a lookback nor an explicit start+end was given.
    """
    if lookback_minutes is not None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=lookback_minutes)
        return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")
    if start_time and end_time:
        return start_time, end_time
    raise ValueError(
        "Provide either lookback_minutes (preferred for \"last N minutes/hours\" style "
        "requests) or both start_time and end_time (for a specific historical window — "
        "call get_current_time first to anchor relative language like \"yesterday\")."
    )


@tencent_api("DescribeOverviewL7Data")
def describe_overview_l7_data(
    zone_ids: list,
    lookback_minutes: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    metric_names: Optional[list] = None,
    interval: Optional[str] = None,
) -> dict:
    """Traffic/request time series for one or more sites — the general-purpose
    "what does load look like right now" call. Good first step when
    diagnosing a symptom like "site is slow" or "traffic looks weird".

    Args:
        zone_ids (list[str]): ZoneIds to query (from describe_zones). Required.
        lookback_minutes (int, optional): PREFERRED for "last N minutes/hours"
            style requests, e.g. 30 for "the last 30 minutes", 1440 for "the
            last day". The actual StartTime/EndTime are computed from the
            real current time in code — you do not need to know today's date
            for this to be correct. Takes priority over start_time/end_time
            if both are given.
        start_time (str, optional): ISO 8601 start, e.g. "2026-08-05T00:00:00Z".
            Only for a specific historical window the user named explicitly —
            call get_current_time first if you need to anchor relative
            language like "yesterday" or "last Tuesday" to a real date.
        end_time (str, optional): ISO 8601 end. Required alongside start_time
            if lookback_minutes isn't used.
        metric_names (list[str], optional): Subset of OVERVIEW_METRICS to fetch.
            Defaults to ["l7Flow_request", "l7Flow_flux"] (requests + total traffic)
            if omitted.
        interval (str, optional): One of "min", "5min", "hour", "day". If
            omitted, EdgeOne auto-picks a granularity based on the time range.

    Returns:
        dict: {"Data": [{"MetricName": str, "DetailData": [{"Timestamp": str,
        "Value": number}, ...]}, ...]}
    """
    resolved_start, resolved_end = _resolve_window(start_time, end_time, lookback_minutes)
    body = clean({
        "ZoneIds": zone_ids,
        "StartTime": resolved_start,
        "EndTime": resolved_end,
        "MetricNames": metric_names or ["l7Flow_request", "l7Flow_flux"],
        "Interval": interval,
    })
    return call_edgeone_api("DescribeOverviewL7Data", body)


@tencent_api("DescribeTopL7AnalysisData")
def describe_top_l7_analysis_data(
    zone_ids: list,
    metric_name: str,
    lookback_minutes: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """Top-N breakdown of traffic/requests by one dimension — this is how you
    find e.g. "which country is this traffic spike coming from" or "which
    client IP is hammering us".

    Args:
        zone_ids (list[str]): ZoneIds to query. Required.
        lookback_minutes (int, optional): PREFERRED for "last N minutes/hours"
            style requests — see describe_overview_l7_data for details. Takes
            priority over start_time/end_time if both are given.
        start_time (str, optional): ISO 8601 start. Only for a specific
            historical window; call get_current_time first to anchor
            relative language like "yesterday".
        end_time (str, optional): ISO 8601 end. (end_time - start_time) must
            be <= 31 days. Required alongside start_time if lookback_minutes
            isn't used.
        metric_name (str): The dimension to break down by. Common ones:
            "l7Flow_request_country" (by country/region),
            "l7Flow_request_sip" (by client IP),
            "l7Flow_request_url" (by URL path),
            "l7Flow_request_statusCode" (by HTTP status code),
            "l7Flow_request_domain" (by domain),
            "l7Flow_request_referers" (by Referer),
            "l7Flow_request_ua" (by User-Agent).
            Swap the "l7Flow_request_" prefix for "l7Flow_outFlux_" to rank
            by response bytes instead of request count for the same dimensions.
        limit (int): How many top entries to return, max 1000. Default 10.

    Returns:
        dict: {"Data": [{"TypeKey": str, "DetailData": [{"Key": str,
        "Value": number}, ...]}]}. For the client-IP metric, the candidate
        IP and ranked request count are in ``DetailData[].Key`` / ``Value``;
        ``TypeKey`` is an outer grouping value, not the client IP.
    """
    resolved_start, resolved_end = _resolve_window(start_time, end_time, lookback_minutes)
    body = clean({
        "ZoneIds": zone_ids,
        "StartTime": resolved_start,
        "EndTime": resolved_end,
        "MetricName": metric_name,
        "Limit": limit,
    })
    return call_edgeone_api("DescribeTopL7AnalysisData", body)


TOOLS = [describe_overview_l7_data, describe_top_l7_analysis_data]
