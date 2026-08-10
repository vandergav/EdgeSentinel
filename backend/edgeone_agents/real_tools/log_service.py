"""Read-only EdgeOne L7 offline-log support for incident investigation.

The top-N analytics endpoint is convenient but eventually consistent.  This
module provides a bounded fallback that asks EdgeOne for the L7 log archives
covering a specific incident window and derives a public client-IP ranking
locally.  It deliberately persists neither raw access-log records nor the
short-lived download URLs returned by EdgeOne.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import gzip
from io import BytesIO
from ipaddress import ip_address
import json
import os
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

import requests

from ._client import call_edgeone_api, clean, tencent_api


MAX_LOG_FILES = 3
MAX_COMPRESSED_BYTES_PER_FILE = 20 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 80 * 1024 * 1024
LOG_DOWNLOAD_TIMEOUT_SECONDS = 20


def _enabled() -> bool:
    return os.environ.get("INCIDENT_L7_LOG_FALLBACK_ENABLED", "true").lower() in {"1", "true", "yes"}


def _safe_log_download_url(value: Any) -> str | None:
    """Accept only HTTPS download locations in EdgeOne's log-download domain."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "edgeone.qcloud.com" or host.endswith(".edgeone.qcloud.com")):
        return None
    return value


def _json_records(data: bytes) -> Iterable[Mapping[str, Any]]:
    """Yield JSON-object access-log records from a bounded decompressed file.

    EdgeOne's default L7 log format is JSON.  In practice archives may be
    JSON Lines or a JSON array, so support both without attempting to retain
    malformed records.
    """
    text = data.decode("utf-8", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            records = json.loads(stripped)
        except json.JSONDecodeError:
            return
        if isinstance(records, list):
            for record in records:
                if isinstance(record, Mapping):
                    yield record
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, Mapping):
            yield record


def _public_client_ip_counts_from_archives(archives: Iterable[bytes]) -> tuple[list[dict[str, Any]], int]:
    """Return ranked public source IPs and the number of parsed log records."""
    counts: Counter[str] = Counter()
    parsed_records = 0
    remaining = MAX_DECOMPRESSED_BYTES
    for archive in archives:
        try:
            with gzip.GzipFile(fileobj=BytesIO(archive), mode="rb") as compressed:
                data = compressed.read(remaining + 1)
        except OSError:
            continue
        if len(data) > remaining:
            data = data[:remaining]
        remaining -= len(data)
        for record in _json_records(data):
            parsed_records += 1
            try:
                candidate = ip_address(str(record.get("ClientIP", "")))
            except ValueError:
                continue
            if candidate.is_global:
                counts[str(candidate)] += 1
        if remaining <= 0:
            break
    return ([{"ip": ip, "requests": count} for ip, count in counts.most_common(10)], parsed_records)


@tencent_api("DownloadL7Logs")
def download_l7_logs(
    zone_ids: list[str],
    *,
    start_time: str,
    end_time: str,
    domains: list[str] | None = None,
    limit: int = MAX_LOG_FILES,
) -> dict[str, Any]:
    """Request bounded L7 offline-log archives for a historical window.

    Args:
        zone_ids: EdgeOne ZoneIds to query. Required by current EdgeOne APIs.
        start_time: ISO-8601 UTC start of the incident evidence window.
        end_time: ISO-8601 UTC end of the incident evidence window.
        domains: Optional domain filter for the affected host.
        limit: Maximum archive descriptors to return; capped at three.

    Returns:
        The EdgeOne response containing archive metadata and short-lived URLs.
        Callers must never persist URLs or raw archive contents.
    """
    if not _enabled():
        return {"RequestId": None, "Data": [], "disabled": True}
    body = clean({
        "ZoneIds": zone_ids,
        "Domains": domains or None,
        "StartTime": start_time,
        "EndTime": end_time,
        "Limit": min(max(limit, 1), MAX_LOG_FILES),
        "Offset": 0,
    })
    return call_edgeone_api("DownloadL7Logs", body)


def collect_l7_log_client_ips(
    *, zone_id: str, start_time: str, end_time: str, domain: str | None = None
) -> dict[str, Any]:
    """Download and locally rank public client IPs for one incident window.

    This function is intentionally an evidence collector, not an agent tool:
    it returns only aggregate counts and safe metadata.  Provider URLs and
    individual request records remain in memory only for the duration of the
    call.
    """
    response = download_l7_logs(
        [zone_id], start_time=start_time, end_time=end_time, domains=[domain] if domain else None
    )
    if response.get("disabled"):
        return {"source": "l7_offline_logs", "status": "disabled", "candidates": []}
    archives: list[bytes] = []
    skipped_archives = 0
    for descriptor in (response.get("Data") or [])[:MAX_LOG_FILES]:
        if not isinstance(descriptor, Mapping):
            continue
        url = _safe_log_download_url(descriptor.get("Url"))
        if not url:
            skipped_archives += 1
            continue
        try:
            with requests.get(url, timeout=LOG_DOWNLOAD_TIMEOUT_SECONDS, stream=True) as download:
                download.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                for chunk in download.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_COMPRESSED_BYTES_PER_FILE:
                        raise ValueError("archive exceeds compressed-size limit")
                    chunks.append(chunk)
                archives.append(b"".join(chunks))
        except (requests.RequestException, ValueError):
            skipped_archives += 1
    candidates, parsed_records = _public_client_ip_counts_from_archives(archives)
    return {
        "source": "l7_offline_logs",
        "request_id": response.get("RequestId"),
        "archive_count": len(archives),
        "skipped_archive_count": skipped_archives,
        "parsed_record_count": parsed_records,
        "candidates": candidates,
        "window": {"start_time": start_time, "end_time": end_time},
    }


TOOLS = [download_l7_logs]
