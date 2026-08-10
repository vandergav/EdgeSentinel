"""
Real, live-API tools for the "Edge Function APIs" category
(see api_catalog.CATALOG["edge_function"]) — serverless compute deployed
at the edge and bound to zones via trigger rules.

Implemented: CreateFunction, DescribeFunctions, CreateFunctionRule.
Everything else (ModifyFunction, DeleteFunction, DescribeFunctionRules,
ModifyFunctionRule, ModifyFunctionRulePriority, DeleteFunctionRules,
DescribeFunctionRuntimeEnvironment, HandleFunctionRuntimeEnvironment,
CreateFunctionReplica, DeleteFunctionReplica, DescribeFunctionReplicas,
ModifyFunctionReplica, DescribeFunctionComponentBindings,
ModifyFunctionComponentBindings) falls back to the catalog stub tools
until someone adds it here.

Sample code references: edge_function_codes/CreateFunction.py,
edge_function_codes/DescribeFunctions.py,
edge_function_codes/CreateFunctionRule.py.

API definitions cross-referenced from api-structurally-mapped-definitions.go:
  - CreateFunctionRequestParams      → ZoneId, Name, Content, Remark
  - DescribeFunctionsRequestParams   → ZoneId, FunctionIds, Filters, Offset, Limit
  - CreateFunctionRuleRequestParams  → ZoneId, FunctionRuleConditions, TriggerType,
                                       FunctionId, RegionMappingSelections,
                                       WeightedSelections, Remark
  - FunctionRuleCondition            → RuleConditions []*RuleCondition
  - RuleCondition                    → Operator, Target, Values, IgnoreCase, Name
"""
from typing import Optional

from ._client import call_edgeone_api, clean, tencent_api


@tencent_api("CreateFunction")
def create_function(
    zone_id: str,
    name: str,
    content: str,
    remark: Optional[str] = None,
) -> dict:
    """Deploys a new edge function (serverless JavaScript) to a zone.

    The function will be available immediately via its auto-assigned preview
    domain once created; it does not serve production traffic until at least
    one trigger rule has been bound to it via create_function_rule.

    Args:
        zone_id (str): The site's ZoneId, e.g. "zone-293e7s5jne1i"
            (obtain from describe_zones).
        name (str): A unique function name. Must contain only lowercase
            letters, digits, and hyphens; must start and end with a letter
            or digit; max 30 characters.
        content (str): The JavaScript source code for the function (the
            runtime uses the Service Worker / Fetch Event API). Maximum
            size is 5 MB. Example minimal handler:
            "addEventListener('fetch', e => { e.respondWith(fetch(e.request)); });"
        remark (str, optional): A human-readable description, max 60 characters.

    Returns:
        dict: {"FunctionId": str, "RequestId": str}
            FunctionId — e.g. "ef-1pakhnuy" — is the stable identifier used
            by create_function_rule and most other edge-function operations.

    Example:
        >>> create_function(
        ...     zone_id="zone-293e7s5jne1i",
        ...     name="ip-redirect",
        ...     content="addEventListener('fetch', e => { e.respondWith(fetch(e.request)); });",
        ...     remark="Redirect specific IPs",
        ... )
        {"FunctionId": "ef-1pakhnuy", "RequestId": "..."}
    """
    body = clean({
        "ZoneId": zone_id,
        "Name": name,
        "Content": content,
        "Remark": remark,
    })
    return call_edgeone_api("CreateFunction", body)


@tencent_api("DescribeFunctions")
def describe_functions(
    zone_id: str,
    function_ids: Optional[list] = None,
    name_filter: Optional[str] = None,
    remark_filter: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """Lists edge functions deployed to a zone, with optional filtering by ID
    or fuzzy name/description search.

    This is typically the first call in any edge-function workflow when you
    need to look up an existing function's FunctionId before modifying or
    binding it to a new trigger rule.

    Args:
        zone_id (str): The site's ZoneId (from describe_zones). Required.
        function_ids (list[str], optional): Narrow results to these specific
            FunctionIds, e.g. ["ef-1pakhnuy", "ef-2pakw1uk"].
        name_filter (str, optional): Fuzzy match on function name.
            Maps to the Tencent API filter key "name".
        remark_filter (str, optional): Fuzzy match on function description
            (remark). Maps to the Tencent API filter key "remark".
        offset (int): Pagination offset. Default 0.
        limit (int): Max results per page, up to 200. Default 20.

    Returns:
        dict: {
            "TotalCount": int,
            "Functions": [
                {
                    "FunctionId": str,
                    "ZoneId": str,
                    "Name": str,
                    "Remark": str,
                    "Content": str,       # the JS source code
                    "Domain": str,        # preview domain auto-assigned by EdgeOne
                    "CreateTime": str,    # ISO 8601
                    "UpdateTime": str,
                },
                ...
            ],
            "RequestId": str,
        }
    """
    filters = []
    if name_filter:
        filters.append({"Name": "name", "Values": [name_filter]})
    if remark_filter:
        filters.append({"Name": "remark", "Values": [remark_filter]})

    body = clean({
        "ZoneId": zone_id,
        "FunctionIds": function_ids,
        "Filters": filters or None,
        "Offset": offset,
        "Limit": limit,
    })
    return call_edgeone_api("DescribeFunctions", body)


@tencent_api("CreateFunctionRule")
def create_function_rule(
    zone_id: str,
    function_rule_conditions: list,
    function_id: Optional[str] = None,
    trigger_type: str = "direct",
    remark: Optional[str] = None,
    region_mapping_selections: Optional[list] = None,
    weighted_selections: Optional[list] = None,
) -> dict:
    """Creates a trigger rule that binds matching HTTP requests to an edge
    function for execution. A function only intercepts live traffic once it
    has at least one active trigger rule.

    Trigger types:
      - "direct" (default): All matching requests go to a single function
        specified by function_id.
      - "weight": Requests are distributed across multiple functions by
        percentage; provide weighted_selections (weights must sum to 100).
      - "region": Function selection is based on the client's country/region;
        provide region_mapping_selections (must include a "Default" entry).

    Args:
        zone_id (str): The site's ZoneId (from describe_zones). Required.
        function_rule_conditions (list[dict]): One or more condition groups.
            Conditions within a group are AND-ed; groups are OR-ed together.
            Each condition group is a dict with a "RuleConditions" key:
            [
                {
                    "RuleConditions": [
                        {
                            "Operator": "equal",       # "equal"|"notequal"|"exist"|"notexist"
                            "Target": "host",          # see supported targets below
                            "Values": ["www.example.com"],
                            # optional:
                            "IgnoreCase": False,
                            "Name": None,              # required for query_string / request_header targets
                        }
                    ]
                }
            ]
            Supported Target values:
              "filename", "extension", "host", "full_url", "url",
              "client_country", "query_string", "request_header",
              "client_ip", "request_protocol", "request_method"
        function_id (str, optional): The FunctionId to execute when
            trigger_type is "direct" (required) or omitted (default "direct").
            Obtain from create_function or describe_functions.
        trigger_type (str): How the target function is selected. One of
            "direct" (default), "weight", or "region".
        remark (str, optional): Rule description, max 60 characters.
        region_mapping_selections (list[dict], optional): Required when
            trigger_type="region". Each entry:
            {"FunctionId": str, "Regions": [str]}. Must include one entry
            with Regions=["Default"] to handle unmatched regions.
        weighted_selections (list[dict], optional): Required when
            trigger_type="weight". Each entry:
            {"FunctionId": str, "Weight": int}. All weights must sum to 100.

    Returns:
        dict: {"RuleId": str, "RequestId": str}
            RuleId — e.g. "rule-vnqup0uc" — identifies this trigger rule for
            future modify/delete operations.

    Example — bind all requests for "www.example.com" to function "ef-1pakhnuy":
        >>> create_function_rule(
        ...     zone_id="zone-293e7s5jne1i",
        ...     function_rule_conditions=[
        ...         {
        ...             "RuleConditions": [
        ...                 {"Operator": "equal", "Target": "host",
        ...                  "Values": ["www.example.com"]}
        ...             ]
        ...         }
        ...     ],
        ...     function_id="ef-1pakhnuy",
        ...     trigger_type="direct",
        ...     remark="Route host to IP-redirect function",
        ... )
        {"RuleId": "rule-vnqup0uc", "RequestId": "..."}
    """
    body = clean({
        "ZoneId": zone_id,
        "FunctionRuleConditions": function_rule_conditions,
        "TriggerType": trigger_type,
        "FunctionId": function_id,
        "RegionMappingSelections": region_mapping_selections,
        "WeightedSelections": weighted_selections,
        "Remark": remark,
    })
    return call_edgeone_api("CreateFunctionRule", body)


TOOLS = [create_function, describe_functions, create_function_rule]
