"""
Central catalog of every Tencent Cloud EdgeOne API, grouped into the same
25 categories used in the official docs (https://edgeone.ai/document/50454).

This is the single source of truth the rest of the package builds from:
  - agent_builder.py creates exactly one sub-agent per CategorySpec here.
  - tool_factory.py uses each CategorySpec's `apis` tuple to generate
    honest "not implemented yet" tools for whatever a category's
    real_tools/<key>.py module doesn't cover.

To add a brand new Tencent API action to the team's knowledge, add it to
the relevant category below. To make it actually callable, implement it in
edgeone_agents/real_tools/<category key>.py — see that package's README
note in agent_builder.py for the wiring convention.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiSpec:
    name: str        # Tencent API action name, e.g. "CreateZone"
    feature: str     # one-line description, straight from the docs
    rps_limit: int   # max requests/second Tencent allows for this action


@dataclass(frozen=True)
class CategorySpec:
    key: str             # python-safe identifier, e.g. "site"
    display_name: str    # e.g. "Site APIs" (matches the docs' section title)
    description: str     # used as the sub-agent's `description`
    apis: tuple[ApiSpec, ...]

    @property
    def api_names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.apis)


CATALOG: dict[str, CategorySpec] = {

    "site": CategorySpec(
        key="site",
        display_name="Site APIs",
        description=(
            "Creates, verifies, configures, and lists EdgeOne sites (zones) — "
            "the root resource every other category operates on."
        ),
        apis=(
            ApiSpec("CreateZone", "Creates a site", 20),
            ApiSpec("DescribeIdentifications", "Queries the verification information of a site.", 20),
            ApiSpec("ModifyZone", "Modifies a site.", 20),
            ApiSpec("DeleteZone", "Deletes a site", 20),
            ApiSpec("ModifyZoneStatus", "Changes the site status", 20),
            ApiSpec("CheckCnameStatus", "Validate the CNAME configuration status", 20),
            ApiSpec("IdentifyZone", "Verifies site ownership", 20),
            ApiSpec("DescribeZones", "Queries the list of sites", 20),
            ApiSpec("ExportZoneConfig", "Exports site configuration", 20),
            ApiSpec("ImportZoneConfig", "Imports site configuration", 20),
            ApiSpec("DescribeZoneConfigImportResult", "Queries site configuration import results", 20),
        ),
    ),

    "accelerated_domain": CategorySpec(
        key="accelerated_domain",
        display_name="Accelerated Domain Name Management APIs",
        description="Connects, lists, and manages the domains served through a site, plus shared CNAMEs.",
        apis=(
            ApiSpec("CreateAccelerationDomain", "Connects a domain to EdgeOne", 20),
            ApiSpec("DescribeAccelerationDomains", "Queries accelerated domain names", 20),
            ApiSpec("ModifyAccelerationDomain", "Modifies an accelerated domain name", 20),
            ApiSpec("ModifyAccelerationDomainStatuses", "Batch modifies the status of accelerated domains.", 20),
            ApiSpec("DeleteAccelerationDomains", "Batch remove accelerated domain names", 20),
            ApiSpec("CreateSharedCNAME", "Creates a shared CNAME.", 20),
            ApiSpec("DescribeSharedCNAME", "Query the shared CNAME list", 20),
            ApiSpec("ModifySharedCNAME", "Modify a shared CNAME", 20),
            ApiSpec("BindSharedCNAME", "Binds with a shared CNAME", 20),
            ApiSpec("DeleteSharedCNAME", "Deletes a shared CNAME", 20),
        ),
    ),

    "site_acceleration": CategorySpec(
        key="site_acceleration",
        display_name="Site Acceleration Configuration APIs",
        description="Layer-7 acceleration rules and settings — caching, redirects, and routing behavior for a site.",
        apis=(
            ApiSpec("CreateL7AccRules", "Create layer-7 acceleration rules", 20),
            ApiSpec("DescribeL7AccRules", "Query layer-7 acceleration rules", 20),
            ApiSpec("ModifyL7AccRule", "Modify layer-7 acceleration rules", 20),
            ApiSpec("DeleteL7AccRules", "Delete layer-7 acceleration rules", 20),
            ApiSpec("DescribeL7AccSetting", "Query global configurations of layer-7 acceleration", 20),
            ApiSpec("ModifyL7AccSetting", "Modify global configurations of layer-7 acceleration", 20),
            ApiSpec("ModifyL7AccRulePriority", "Modify the rule priority of layer-7 acceleration", 20),
        ),
    ),

    "edge_function": CategorySpec(
        key="edge_function",
        display_name="Edge Function APIs",
        description="Deploys and manages edge compute (serverless functions) and their trigger rules.",
        apis=(
            ApiSpec("CreateFunction", "Creates an edge function", 5),
            ApiSpec("DescribeFunctions", "Lists edge functions deployed to a zone", 20),
            ApiSpec("ModifyFunction", "Modifies an edge function", 20),
            ApiSpec("DeleteFunction", "Deletes an edge function", 20),
            ApiSpec("CreateFunctionRule", "Creates a trigger rule for an edge function", 20),
            ApiSpec("DescribeFunctionRules", "Queries a trigger rule for an edge function", 20),
            ApiSpec("ModifyFunctionRule", "Modifies a trigger rule for an edge function", 20),
            ApiSpec("ModifyFunctionRulePriority", "Modifies the priority of trigger rules for an edge function", 20),
            ApiSpec("DeleteFunctionRules", "Deletes a trigger rule for an edge function", 20),
            ApiSpec("DescribeFunctionRuntimeEnvironment", "Queries the runtime environment of an edge function", 20),
            ApiSpec("HandleFunctionRuntimeEnvironment", "Operates the runtime environment of an edge function", 20),
            ApiSpec("CreateFunctionReplica", "Creates an edge function replica", 20),
            ApiSpec("DeleteFunctionReplica", "Delete an edge function replica", 20),
            ApiSpec("DescribeFunctionReplicas", "Queries the list of edge function replicas", 20),
            ApiSpec("ModifyFunctionReplica", "Edit an edge function replica", 20),
            ApiSpec("DescribeFunctionComponentBindings", "Query the bound list of a function component", 20),
            ApiSpec("ModifyFunctionComponentBindings", "Modify a function component binding", 20),
        ),
    ),

    "alias_domain": CategorySpec(
        key="alias_domain",
        display_name="Alias Domain APIs",
        description="Manages alias domains that mirror an existing accelerated domain's configuration.",
        apis=(
            ApiSpec("CreateAliasDomain", "Creates an alias domain name.", 20),
            ApiSpec("DescribeAliasDomains", "Queries the information of alias domain names.", 20),
            ApiSpec("ModifyAliasDomain", "Modifies an alias domain name.", 20),
            ApiSpec("ModifyAliasDomainStatus", "Modifies the status of an alias domain name.", 20),
            ApiSpec("DeleteAliasDomain", "Deletes an alias domain name.", 20),
        ),
    ),

    "security": CategorySpec(
        key="security",
        display_name="Security Configuration APIs",
        description=(
            "WAF, bot management, rate limiting, custom rules, DDoS protection, and IP groups — "
            "the core CDN/WAF security surface."
        ),
        apis=(
            ApiSpec("CreateSecurityIPGroup", "Creates a security IP group", 20),
            ApiSpec("DescribeSecurityIPGroup", "Queries security IP groups", 20),
            ApiSpec("ModifySecurityIPGroup", "Modifies a security IP group", 20),
            ApiSpec("DeleteSecurityIPGroup", "Deletes a security IP group", 20),
            ApiSpec("DescribeSecurityTemplateBindings", "Queries bindings of a policy template", 20),
            ApiSpec("BindSecurityTemplateToEntity", "Binds/Unbinds a domain name to/from a security policy template", 20),
            ApiSpec("DescribeSecurityPolicy", "Query security protection configuration details", 20),
            ApiSpec("ModifySecurityPolicy", "Modifies the web and bot security configurations", 20),
            ApiSpec("DescribeSecurityIPGroupInfo", "Queries security IP groups (deprecated)", 20),
            ApiSpec("DescribeSecurityIPGroupContent", "Queries the IP list in an IP group with paging", 20),
            ApiSpec("CreateSecurityJSInjectionRule", "Creates a JavaScript injection rule", 20),
            ApiSpec("DescribeSecurityJSInjectionRule", "Query JavaScript injection rules", 20),
            ApiSpec("ModifySecurityJSInjectionRule", "Modify JavaScript injection rules", 20),
            ApiSpec("DeleteSecurityJSInjectionRule", "Delete a JavaScript injection rule", 20),
            ApiSpec("CreateSecurityClientAttester", "Creates client authentication options", 20),
            ApiSpec("DescribeSecurityClientAttester", "Queries client authentication options", 20),
            ApiSpec("ModifySecurityClientAttester", "Modify client authentication options", 20),
            ApiSpec("DeleteSecurityClientAttester", "Delete a client authentication option", 20),
            ApiSpec("CreateWebSecurityTemplate", "Creates a security policy configuration template", 20),
            ApiSpec("DeleteWebSecurityTemplate", "Delete a security policy configuration template", 20),
            ApiSpec("DescribeWebSecurityTemplates", "Query security policy configuration template list", 20),
            ApiSpec("ModifyWebSecurityTemplate", "Modify a security policy configuration template", 20),
            ApiSpec("DescribeDDoSProtection", "Query the exclusive DDoS protection info of a site", 20),
            ApiSpec("DescribeWebSecurityTemplate", "Query security policy configuration template detail", 20),
            ApiSpec("ModifyDDoSProtection", "Modify site exclusive Anti-DDoS protection", 20),
        ),
    ),

    "l4_proxy": CategorySpec(
        key="l4_proxy",
        display_name="Layer 4 Application Proxy APIs",
        description="TCP/UDP (Layer 4) proxy instances and forwarding rules for non-HTTP traffic.",
        apis=(
            ApiSpec("CreateL4Proxy", "Creates Layer 4 proxy instances", 20),
            ApiSpec("ModifyL4Proxy", "Modifies a Layer 4 proxy instance", 20),
            ApiSpec("ModifyL4ProxyStatus", "Modifies Layer 4 proxy instance status", 20),
            ApiSpec("DescribeL4Proxy", "Queries a Layer 4 proxy instance list", 20),
            ApiSpec("DeleteL4Proxy", "Deletes a Layer 4 proxy instance", 20),
            ApiSpec("CreateL4ProxyRules", "Creates Layer 4 proxy forwarding rules", 20),
            ApiSpec("ModifyL4ProxyRules", "Modifies Layer 4 proxy forwarding rules", 20),
            ApiSpec("ModifyL4ProxyRulesStatus", "Modifies Layer 4 proxy forwarding rule status", 20),
            ApiSpec("DescribeL4ProxyRules", "Queries a Layer 4 proxy forwarding rule list", 20),
            ApiSpec("DeleteL4ProxyRules", "Deletes Layer 4 proxy forwarding rules", 20),
        ),
    ),

    "content_management": CategorySpec(
        key="content_management",
        display_name="Content Management APIs",
        description="Cache purge and prefetch tasks, plus content management quotas.",
        apis=(
            ApiSpec("CreatePurgeTask", "This API is used to create a cache purging task.", 20),
            ApiSpec("DescribePurgeTasks", "Querying the cache purging history", 20),
            ApiSpec("CreatePrefetchTask", "This API is used to create a pre-warming task.", 20),
            ApiSpec("DescribePrefetchTasks", "This API is used to query the pre-warming task status.", 20),
            ApiSpec("DescribeContentQuota", "This API is used to query content management quotas.", 20),
            ApiSpec("DescribePrefetchOriginLimit", "Query preheating origin speed limit restrictions", 20),
            ApiSpec("ModifyPrefetchOriginLimit", "Configure preheating origin speed limit", 20),
        ),
    ),

    "data_analysis": CategorySpec(
        key="data_analysis",
        display_name="Data Analysis APIs",
        description="Traffic, cache, origin-pull, and DDoS analytics — the read side an engineer diagnoses issues with.",
        apis=(
            ApiSpec("DescribeDDoSAttackData", "Queries the time-series data of DDoS attacks.", 100),
            ApiSpec("DescribeDDoSAttackEvent", "Queries DDoS attack events.", 100),
            ApiSpec("DescribeDDoSAttackTopData", "Queries the top-ranked DDoS attack data.", 100),
            ApiSpec("DescribeOverviewL7Data", "Query monitoring traffic time sequence data (to be discarded)", 100),
            ApiSpec("DescribeTimingL7OriginPullData", "Query origin time series data", 20),
            ApiSpec("DescribeTimingL4Data", "Queries the L4 traffic data over time", 100),
            ApiSpec("DescribeTimingL7AnalysisData", "Queries the traffic analysis data over time", 30),
            ApiSpec("DescribeTopL7AnalysisData", "Queries the traffic analysis data", 30),
            ApiSpec("DescribeTimingL7CacheData", "Queries the time series data of the cache analysis (to be deprecated)", 100),
            ApiSpec("DescribeTopL7CacheData", "Queries the top N data of the cache analysis (to be deprecated)", 100),
        ),
    ),

    "log_service": CategorySpec(
        key="log_service",
        display_name="Log Service APIs",
        description="Raw log downloads and real-time log delivery (e.g. to CLS) for deep investigation and continuous monitoring.",
        apis=(
            ApiSpec("DownloadL7Logs", "Downloads L7 logs.", 100),
            ApiSpec("DownloadL4Logs", "Downloads L4 logs", 100),
            ApiSpec("CreateCLSIndex", "Creates a CLS index", 20),
            ApiSpec("CreateRealtimeLogDeliveryTask", "Creates a real-time log delivery task", 20),
            ApiSpec("ModifyRealtimeLogDeliveryTask", "Modifies a real-time log delivery task", 20),
            ApiSpec("DeleteRealtimeLogDeliveryTask", "Deletes a real-time log delivery task", 20),
            ApiSpec("DescribeRealtimeLogDeliveryTasks", "Queries the list of real-time log delivery tasks", 20),
        ),
    ),

    "billing": CategorySpec(
        key="billing",
        display_name="Billing APIs",
        description="Plan purchase, upgrade, renewal, and billing-data queries.",
        apis=(
            ApiSpec("CreatePlan", "Creates plan", 20),
            ApiSpec("DescribePlans", "Query package information list", 20),
            ApiSpec("UpgradePlan", "Upgrades plan", 20),
            ApiSpec("RenewPlan", "Renews plan", 20),
            ApiSpec("ModifyPlan", "Modifies plan setting", 20),
            ApiSpec("IncreasePlanQuota", "Purchases additional plan quota", 20),
            ApiSpec("DestroyPlan", "Terminates plan", 20),
            ApiSpec("CreatePlanForZone", "Purchase a plan for a new site", 20),
            ApiSpec("BindZoneToPlan", "Binds a site to a plan.", 20),
            ApiSpec("DescribeBillingData", "Queries billing data", 50),
            ApiSpec("DescribeAvailablePlans", "Queries plan options available for purchase", 20),
        ),
    ),

    "certificate": CategorySpec(
        key="certificate",
        display_name="Certificate APIs",
        description="TLS certificate configuration, including Tencent's free-certificate flow.",
        apis=(
            ApiSpec("DescribeDefaultCertificates", "Queries the list of default certificates", 20),
            ApiSpec("ModifyHostsCertificate", "Configures the certificate", 20),
            ApiSpec("ApplyFreeCertificate", "Applies for a free certificate", 20),
            ApiSpec("CheckFreeCertificateVerification", "Check free certificate application result", 20),
        ),
    ),

    "origin_protection": CategorySpec(
        key="origin_protection",
        display_name="Origin Protection APIs",
        description="Origin ACLs that restrict origin access to EdgeOne's own IP ranges.",
        apis=(
            ApiSpec("EnableOriginACL", "Enable origin protection", 20),
            ApiSpec("ModifyOriginACL", "Modify Origin Protection Instances", 20),
            ApiSpec("DescribeOriginACL", "Query Origin Protection Detail", 20),
            ApiSpec("ConfirmOriginACLUpdate", "Confirm Origin ACLs Update", 20),
            ApiSpec("DisableOriginACL", "Disable Origin Protection", 20),
        ),
    ),

    "load_balancing": CategorySpec(
        key="load_balancing",
        display_name="Load Balancing APIs",
        description="Origin groups and load balancers, including origin health status.",
        apis=(
            ApiSpec("CreateOriginGroup", "Creates an origin group", 20),
            ApiSpec("ModifyOriginGroup", "Modifies an origin group", 20),
            ApiSpec("DeleteOriginGroup", "Deletes an origin group", 20),
            ApiSpec("DescribeOriginGroup", "Obtains a list of origin groups.", 20),
            ApiSpec("CreateLoadBalancer", "Creates a LoadBalancer", 20),
            ApiSpec("ModifyLoadBalancer", "Modifies a LoadBalancer", 20),
            ApiSpec("DeleteLoadBalancer", "Deletes a LoadBalancer", 20),
            ApiSpec("DescribeLoadBalancerList", "Queries the LoadBalancer list", 20),
            ApiSpec("DescribeOriginGroupHealthStatus", "Queries the health status of origin server groups under a LoadBalancer", 20),
        ),
    ),

    "diagnostic_tool": CategorySpec(
        key="diagnostic_tool",
        display_name="Diagnostic Tool APIs",
        description="Small standalone diagnostics, e.g. resolving an IP to region/ASN.",
        apis=(
            ApiSpec("DescribeIPRegion", "Queries IP location information", 20),
        ),
    ),

    "custom_response_page": CategorySpec(
        key="custom_response_page",
        display_name="Custom Response Page APIs",
        description="Custom error/response pages served for specific status codes.",
        apis=(
            ApiSpec("CreateCustomizeErrorPage", "Creates a custom response page", 20),
            ApiSpec("DescribeCustomErrorPages", "Queries the custom response page list", 20),
            ApiSpec("ModifyCustomErrorPage", "Modifies a custom response page", 20),
            ApiSpec("DeleteCustomErrorPage", "Deletes a custom response page", 20),
        ),
    ),

    "version_management": CategorySpec(
        key="version_management",
        display_name="Version Management APIs",
        description="Configuration-group versioning: create, deploy, and roll back a batch of changes safely.",
        apis=(
            ApiSpec("CreateConfigGroupVersion", "Create Configuration Group Version", 20),
            ApiSpec("DeployConfigGroupVersion", "Release Configuration Group Version", 20),
            ApiSpec("DescribeConfigGroupVersionDetail", "Query Configuration Group Version Details", 20),
            ApiSpec("DescribeConfigGroupVersions", "Query Configuration Group Version List", 20),
            ApiSpec("DescribeDeployHistory", "Query Version Release History", 20),
            ApiSpec("DescribeEnvironments", "Query Environment Information", 20),
            ApiSpec("ModifyZoneWorkMode", "Modify site working mode", 20),
        ),
    ),

    "api_security": CategorySpec(
        key="api_security",
        display_name="API Security APIs",
        description="API asset discovery and management — resources and services exposed through a site.",
        apis=(
            ApiSpec("CreateSecurityAPIResource", "Creates an API resource", 20),
            ApiSpec("DescribeSecurityAPIResource", "Query API resources", 20),
            ApiSpec("ModifySecurityAPIResource", "Modify an API resource", 20),
            ApiSpec("DeleteSecurityAPIResource", "Delete an API resource", 20),
            ApiSpec("CreateSecurityAPIService", "Creates an API service", 20),
            ApiSpec("DescribeSecurityAPIService", "Query API service", 20),
            ApiSpec("ModifySecurityAPIService", "Modify an API service", 20),
            ApiSpec("DeleteSecurityAPIService", "Delete an API service", 20),
        ),
    ),

    "dns_record": CategorySpec(
        key="dns_record",
        display_name="DNS Record APIs",
        description="DNS record management for sites using DNSPod-hosted or NS-connected domains.",
        apis=(
            ApiSpec("CreateDnsRecord", "Creates a dns record", 20),
            ApiSpec("DescribeDnsRecords", "Query dns record list", 20),
            ApiSpec("ModifyDnsRecords", "Batch modify dns records", 20),
            ApiSpec("ModifyDnsRecordsStatus", "Batch modify dns record status", 20),
            ApiSpec("DeleteDnsRecords", "Batch deletes dns records", 20),
        ),
    ),

    "content_identifier": CategorySpec(
        key="content_identifier",
        display_name="Content Identifier APIs",
        description="Named content identifiers used to group resources for cache/config targeting.",
        apis=(
            ApiSpec("CreateContentIdentifier", "Create content identifiers", 20),
            ApiSpec("DescribeContentIdentifiers", "Batch query content identifiers", 20),
            ApiSpec("ModifyContentIdentifier", "Modify content identifier", 20),
            ApiSpec("DeleteContentIdentifier", "Delete content identifiers", 20),
        ),
    ),

    "legacy": CategorySpec(
        key="legacy",
        display_name="Legacy APIs",
        description="Pre-rule-engine APIs (old-style rules, zone settings, application proxies) kept for backward compatibility.",
        apis=(
            ApiSpec("DescribeOriginProtection", "Query origin protection info (legacy)", 20),
            ApiSpec("CreateRule", "Creates a rule in the rule engine (old)", 20),
            ApiSpec("DescribeRules", "Query the rules in the rule engine (old)", 20),
            ApiSpec("ModifyRule", "Modify a rule in the rule engine (old)", 20),
            ApiSpec("DeleteRules", "Batch delete rules from the rule engine (old)", 20),
            ApiSpec("DescribeZoneSetting", "Query zone configuration (old)", 20),
            ApiSpec("ModifyZoneSetting", "Modify site configuration (old)", 20),
            ApiSpec("DescribeHostsSetting", "Query detailed configurations of the domain name (old)", 20),
            ApiSpec("DescribeRulesSetting", "Query the setting parameters of the rule engine (old)", 20),
            ApiSpec("CreateApplicationProxy", "Creates an application proxy (legacy)", 20),
            ApiSpec("DescribeApplicationProxies", "Queries the application proxy list (legacy)", 20),
            ApiSpec("ModifyApplicationProxy", "Modifies an application proxy (legacy)", 20),
            ApiSpec("ModifyApplicationProxyStatus", "Modifies the application proxy status (legacy)", 20),
            ApiSpec("DeleteApplicationProxy", "Deletes an application proxy (legacy)", 20),
            ApiSpec("CreateApplicationProxyRule", "Creates an application proxy rule (legacy)", 20),
            ApiSpec("ModifyApplicationProxyRule", "Modifies an application proxy rule (legacy)", 20),
            ApiSpec("ModifyApplicationProxyRuleStatus", "Modifies the status of an application proxy rule (legacy)", 20),
            ApiSpec("DeleteApplicationProxyRule", "Deletes an application proxy rule (legacy)", 20),
        ),
    ),

    "ownership": CategorySpec(
        key="ownership",
        display_name="Ownership APIs",
        description="Domain/site ownership verification.",
        apis=(
            ApiSpec("VerifyOwnership", "Verifies ownership", 20),
        ),
    ),

    "image_video": CategorySpec(
        key="image_video",
        display_name="Image and Video Processing APIs",
        description="Just-in-time transcoding templates for image and video processing.",
        apis=(
            ApiSpec("CreateJustInTimeTranscodeTemplate", "Create a just-in-time transcoding template", 20),
            ApiSpec("DescribeJustInTimeTranscodeTemplates", "Retrieve the instant transcoding template list", 20),
            ApiSpec("DeleteJustInTimeTranscodeTemplates", "Delete a just in time transcoding template", 20),
        ),
    ),

    "multipath_gateway": CategorySpec(
        key="multipath_gateway",
        display_name="Multi-Channel Security Gateway APIs",
        description="Multi-channel secure acceleration gateways (SCDN) — a distinct product line for private line / multi-path access.",
        apis=(
            ApiSpec("ConfirmMultiPathGatewayOriginACL", "Confirm multi-channel SCDN gateway origin IP range update", 20),
            ApiSpec("DescribeMultiPathGatewayOriginACL", "Query multi-channel SCDN gateway origin server protection detail", 20),
            ApiSpec("CreateMultiPathGateway", "Create a multi-channel security acceleration gateway", 20),
            ApiSpec("DescribeMultiPathGateways", "Query multi-channel security acceleration gateway list", 20),
            ApiSpec("DescribeMultiPathGateway", "Query multi-channel security acceleration gateway details", 20),
            ApiSpec("ModifyMultiPathGateway", "Modify the multi-channel security acceleration gateway information", 20),
            ApiSpec("ModifyMultiPathGatewayStatus", "Modify the multi-channel security acceleration gateway status", 20),
            ApiSpec("DeleteMultiPathGateway", "Delete a multi-channel security acceleration gateway", 20),
            ApiSpec("DescribeMultiPathGatewayRegions", "Query the list of available regions for the multi-channel security acceleration gateway", 20),
            ApiSpec("CreateMultiPathGatewaySecretKey", "Create a multi-channel security acceleration gateway key", 20),
            ApiSpec("DescribeMultiPathGatewaySecretKey", "Query the access key for the multi-channel security acceleration gateway", 20),
            ApiSpec("ModifyMultiPathGatewaySecretKey", "Modify a multi-channel security acceleration gateway integration key", 20),
            ApiSpec("RefreshMultiPathGatewaySecretKey", "Refresh the access key for the multi-channel security acceleration gateway", 20),
            ApiSpec("CreateMultiPathGatewayLine", "Create a multi-channel security acceleration gateway route", 20),
            ApiSpec("DescribeMultiPathGatewayLine", "Query multi-channel security acceleration gateway lines details", 20),
            ApiSpec("ModifyMultiPathGatewayLine", "Modify multi-channel security acceleration gateway line information", 20),
            ApiSpec("DeleteMultiPathGatewayLine", "Delete a multi-channel security acceleration gateway line", 20),
        ),
    ),

    "kv_storage": CategorySpec(
        key="kv_storage",
        display_name="KV Storage APIs",
        description="EdgeKV namespaces and key-value data used by edge functions.",
        apis=(
            ApiSpec("CreateEdgeKVNamespace", "Create a KV namespace", 20),
            ApiSpec("DeleteEdgeKVNamespace", "Delete a KV namespace", 20),
            ApiSpec("DescribeEdgeKVNamespaces", "Query a KV namespace", 20),
            ApiSpec("EdgeKVDelete", "Delete KV Data", 20),
            ApiSpec("EdgeKVGet", "Query KV data", 20),
            ApiSpec("EdgeKVList", "Query KV name list", 20),
            ApiSpec("EdgeKVPut", "Write KV data", 20),
            ApiSpec("ModifyEdgeKVNamespace", "Modify a KV namespace", 20),
        ),
    ),
}


def total_api_count() -> int:
    return sum(len(c.apis) for c in CATALOG.values())
