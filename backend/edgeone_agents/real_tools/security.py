"""
Real, live-API tools for the "Security Configuration APIs" category
(see api_catalog.CATALOG["security"]) — the core WAF/bot/rate-limit surface.

Implemented: DescribeSecurityPolicy, ModifySecurityPolicy.
Everything else (IP groups, JS injection rules, client attestation, security
templates, DDoS protection toggles, ...) falls back to the catalog stub
tools until someone adds it here.
"""
from typing import Optional

from ._client import call_edgeone_api, clean, tencent_api


@tencent_api("DescribeWebSecurityTemplates")
def describe_web_security_templates(zone_ids: list[str]) -> dict:
    """List Web Security policy templates for one or more EdgeOne zones."""
    return call_edgeone_api("DescribeWebSecurityTemplates", {"ZoneIds": zone_ids})


@tencent_api("DescribeSecurityTemplateBindings")
def describe_security_template_bindings(zone_id: str, template_ids: list[str]) -> dict:
    """Read policy-template bindings for a zone. This call never changes policy."""
    return call_edgeone_api("DescribeSecurityTemplateBindings", {"ZoneId": zone_id, "TemplateId": template_ids})


@tencent_api("DescribeWebSecurityTemplate")
def describe_web_security_template(zone_id: str, template_id: str) -> dict:
    """Read one Web Security template's effective modern policy configuration."""
    return call_edgeone_api("DescribeWebSecurityTemplate", {"ZoneId": zone_id, "TemplateId": template_id})


@tencent_api("DescribeSecurityPolicy")
def describe_security_policy(
    zone_id: str,
    entity: str = "ZoneDefaultPolicy",
    host: Optional[str] = None,
    template_id: Optional[str] = None,
) -> dict:
    """Reads the current WAF/bot/rate-limit configuration for a zone, host, or
    policy template. Always call this before modify_security_policy so you
    know what's already in place and don't clobber unrelated rules.

    Args:
        zone_id (str): The site's ZoneId, e.g. "zone-2noz78a8ev6k" (from describe_zones).
        entity (str): Which level of policy to read. One of:
            "ZoneDefaultPolicy" (the site-wide default, default value),
            "Host" (a specific domain's policy — requires `host`),
            "Template" (a reusable policy template — requires `template_id`).
        host (str, optional): Required when entity="Host", e.g. "www.example.com".
        template_id (str, optional): Required when entity="Template".

    Returns:
        dict: The full SecurityPolicy object — CustomRules, ManagedRules,
        RateLimitingRules, BotManagement, ExceptionRules, HttpDDoSProtection, etc.
    """
    body = clean({
        "ZoneId": zone_id,
        "Entity": entity,
        "Host": host,
        "TemplateId": template_id,
    })
    return call_edgeone_api("DescribeSecurityPolicy", body)


@tencent_api("ModifySecurityPolicy")
def modify_security_policy(
    zone_id: str,
    entity: str = "ZoneDefaultPolicy",
    host: Optional[str] = None,
    template_id: Optional[str] = None,
    custom_rules: Optional[list] = None,
    rate_limiting_rules: Optional[list] = None,
    legacy_acl_rules: Optional[list] = None,
    legacy_acl_switch: Optional[str] = None,
) -> dict:
    """Applies WAF custom rules and/or rate-limiting rules. This is a real,
    live change to the site's security posture — the orchestrator must have
    the user's explicit go-ahead before calling this.

    IMPORTANT: this replaces the given rule set(s) wholesale for the target
    entity, it does not append. Call describe_security_policy first, merge
    your new rule into the existing `CustomRules`/`RateLimitingRules` list
    yourself, and pass the full merged list back — otherwise you will
    silently delete the other rules.

    Args:
        zone_id (str): The site's ZoneId.
        entity (str): "ZoneDefaultPolicy" (default), "Host", or "Template" — same
            meaning as in describe_security_policy.
        host (str, optional): Required when entity="Host".
        template_id (str, optional): Required when entity="Template".
        custom_rules (list, optional): Full replacement for SecurityPolicy.CustomRules.Rules.
            Each item: {"Id": str, "Name": str, "Condition": str, "Enabled": "on"|"off",
            "Action": {"Name": "Deny"|"Allow"|"Monitor"|"Redirect"|...}, "Priority": int}.
            `Condition` uses EdgeOne's rule-engine expression syntax, e.g. to block
            an ASN: "${http.request.ip.asn} in ['132203']" with Action.Name="Deny".
        rate_limiting_rules (list, optional): Full replacement for
            SecurityPolicy.RateLimitingRules.Rules — each item defines a
            threshold (requests per period) and an action once it's exceeded.
        legacy_acl_rules (list, optional): Full replacement for the
            legacy SecurityConfig.AclConfig.AclUserRules list. Use
            only for accounts where the modern CustomRules quota is unavailable.
        legacy_acl_switch (str, optional): Legacy ACL module switch, "on" or
        "off". Required when legacy_acl_rules is supplied.

    Returns:
        dict: The API response envelope (empty on success besides RequestId).
    """
    security_policy = clean({
        "CustomRules": {"Rules": custom_rules} if custom_rules is not None else None,
        "RateLimitingRules": {"Rules": rate_limiting_rules} if rate_limiting_rules is not None else None,
    })

    body = clean({
        "ZoneId": zone_id,
        "Entity": entity,
        "Host": host,
        "TemplateId": template_id,
        # Required by ModifySecurityPolicy even when the modern
        # SecurityPolicy field is the only configuration being changed.
        "SecurityConfig": (
            {"AclConfig": {"Switch": legacy_acl_switch, "AclUserRules": legacy_acl_rules}}
            if legacy_acl_rules is not None
            else {}
        ),
        "SecurityPolicy": security_policy or None,
    })
    return call_edgeone_api("ModifySecurityPolicy", body)


TOOLS = [
    describe_security_policy,
    modify_security_policy,
    describe_web_security_templates,
    describe_security_template_bindings,
    describe_web_security_template,
]
