"""
Real, live-API tools for the "Site APIs" category (see api_catalog.CATALOG["site"]).

Implemented: CreateZone, DescribeZones.
Everything else in the category (ModifyZone, DeleteZone, ModifyZoneStatus,
CheckCnameStatus, IdentifyZone, ExportZoneConfig, ImportZoneConfig,
DescribeZoneConfigImportResult, DescribeIdentifications) falls back to the
catalog stub tools until someone adds it here.
"""
from typing import Optional

from ._client import call_edgeone_api, clean, tencent_api


@tencent_api("CreateZone")
def create_zone(
    zone_name: str,
    access_type: str = "partial",
    area: str = "overseas",
    plan_id: Optional[str] = None,
) -> dict:
    """Creates (onboards) a new EdgeOne site for a domain.

    Args:
        zone_name (str): The apex/second-level domain to onboard, e.g. "example.com".
        access_type (str): How the site connects to EdgeOne. One of:
            "partial" (CNAME access, default), "full" (NS access),
            "noDomainAccess" (no domain), "dnsPodAccess" (domain already
            hosted on DNSPod), "ai" (edge-inference access).
        area (str): Acceleration area. One of "global", "mainland", or
            "overseas" (default — global coverage excluding mainland China).
            Ignored when access_type is "noDomainAccess".
        plan_id (str, optional): An existing billing plan to bind the site to
            immediately. If omitted, the site is created in "init" status
            (not yet active/visible in the console) until BindZoneToPlan is
            called — see the billing category.

    Returns:
        dict: The created zone record, including its ZoneId and current Status.
    """
    body = clean({
        "ZoneName": zone_name,
        "Type": access_type,
        "Area": area,
        "PlanId": plan_id,
    })
    return call_edgeone_api("CreateZone", body)


@tencent_api("DescribeZones")
def describe_zones(
    zone_name_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """Lists the sites (zones) visible to this account — almost always the first
    call in any workflow, since every other category needs a ZoneId.

    Args:
        zone_name_filter (str, optional): Only return zones whose name contains
            this (supports fuzzy match on zone-name).
        status_filter (str, optional): One of "active" (NS switched over),
            "pending" (NS pending), "deleted".
        offset (int): Pagination offset. Default 0.
        limit (int): Max results per page (max 100). Default 20.

    Returns:
        dict: {"TotalCount": int, "Zones": [{"ZoneId": str, "ZoneName": str,
        "Status": str, "Type": str, ...}, ...]}
    """
    filters = []
    if zone_name_filter:
        filters.append({"Name": "zone-name", "Values": [zone_name_filter]})
    if status_filter:
        filters.append({"Name": "status", "Values": [status_filter]})

    body = clean({
        "Offset": offset,
        "Limit": limit,
        "Filters": filters or None,
    })
    return call_edgeone_api("DescribeZones", body)


TOOLS = [create_zone, describe_zones]
