"""
Real, live-API tools for the "Content Management APIs" category
(see api_catalog.CATALOG["content_management"]).

Implemented: CreatePurgeTask.
Everything else (DescribePurgeTasks, CreatePrefetchTask,
DescribePrefetchTasks, DescribeContentQuota, prefetch origin-speed-limit
config) falls back to the catalog stub tools until someone adds it here.
"""
from typing import Optional

from ._client import call_edgeone_api, clean, tencent_api


@tencent_api("CreatePurgeTask")
def create_purge_task(
    zone_id: str,
    targets: list,
    purge_type: str = "purge_url",
    method: str = "invalidate",
) -> dict:
    """Purges cached content at the edge — the standard remediation when
    diagnostics show a stale or bad response is being served from cache.
    This is a real, live change; the orchestrator should have the user's
    go-ahead before calling it.

    Args:
        zone_id (str): The site's ZoneId.
        targets (list[str]): What to purge. Shape depends on purge_type:
            purge_url -> full URLs, e.g. ["https://www.example.com/a.jpg"].
            purge_prefix -> URL prefixes, e.g. ["https://www.example.com/images/"].
            purge_host -> hostnames, e.g. ["www.example.com"].
            purge_all -> ignored; every cached object under the zone is purged
                (not supported when zone_id is "*").
            purge_cache_tag -> cache-tag values.
        purge_type (str): One of "purge_url" (default), "purge_prefix",
            "purge_host", "purge_all", "purge_cache_tag".
        method (str): "invalidate" (default — only re-validate on next
            request) or "delete" (force-purge regardless of freshness).

    Returns:
        dict: {"PurgeTaskId": str} — use DescribePurgeTasks (not yet
        implemented in this starter) to check progress.
    """
    body = clean({
        "ZoneId": zone_id,
        "Type": purge_type,
        "Method": method,
        "Targets": targets,
    })
    return call_edgeone_api("CreatePurgeTask", body)


TOOLS = [create_purge_task]
