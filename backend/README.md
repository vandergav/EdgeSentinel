# EdgeOne CDN/WAF Engineer — Agent Team

> Part of the [EdgeOne Platform](../README.md) full-stack app. This doc covers
> the agent team specifically (`backend/edgeone_agents/`) — for how it's
> served over HTTP/AG-UI and wired to the frontend, see the top-level README.

A Google ADK multi-agent team that mirrors Tencent Cloud EdgeOne's own API
docs: **one specialist sub-agent per API category**, coordinated by a root
orchestrator that diagnoses issues (traffic/cache/origin/security signals)
and, with your approval, fixes them.

25 categories / 214 API actions are catalogued. **7 actions across 4
categories are wired to the live API today** — everything else is a fully
named, fully described stub that's one function away from going live. That
was the point of this pass: get the *whole* team standing, then fill it in
category by category.

## Architecture

```
edgeone_agents/
├── api_catalog.py     # single source of truth: every category + every API action
├── tool_factory.py     # generates list_operations() + <cat>_not_implemented() per category
├── agent_builder.py     # category -> Agent: real tools first, catalog stubs for the rest
├── agent.py     # root_agent = orchestrator (ADK CLI entry point)
└── real_tools/
    ├── _client.py     # hand-rolled TC3-HMAC-SHA256 signed request client
    ├── site.py     # LIVE: create_zone, describe_zones
    ├── security.py     # LIVE: describe_security_policy, modify_security_policy
    ├── data_analysis.py     # LIVE: describe_overview_l7_data, describe_top_l7_analysis_data
    └── content_management.py     # LIVE: create_purge_task
```

Every sub-agent gets built the same way (`agent_builder.build_category_agent`):

1. Load `real_tools/<category>.py` if it exists → real, network-calling tools.
2. Add `list_<category>_operations()` → lets the LLM see every action in
   that category, live or not (sourced from `api_catalog.py`).
3. For every action in the category *not* covered by a real tool, add one
   shared `<category>_not_implemented(operation, notes)` fallback, so an
   unimplemented request comes back honest instead of hallucinated.

This means all 25 agents exist and know their full job description today;
only their ability to actually *do* most of it is still pending.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then fill in GOOGLE_API_KEY + TENCENTCLOUD_SECRET_ID/KEY
```

## Running

```bash
python main.py          # interactive REPL, manual Runner/SessionService (see main.py)
# or
adk web                 # ADK's own browser UI, run from the repo root
adk run edgeone_agents   # ADK's own CLI
```

Try things like:
- `list the sites on my account` → routes to `site_agent` → `describe_zones`
- `what's the current WAF config for zone-xxxx` → `security_agent` → `describe_security_policy`
- `why is zone-xxxx slow in the last hour` → orchestrator fans out across
  `data_analysis_agent` (traffic + top-N breakdown) and `security_agent`
  (current policy) and synthesizes a root-cause summary
- `what can the billing agent do` → `billing_agent` → `list_billing_operations`
  (fully catalogued, zero live calls yet — it'll say so)

## What's live vs. catalog-only

| Category | Live tools | Catalog-only |
|---|---|---|
| `site_agent` | CreateZone, DescribeZones | 9 more |
| `security_agent` | DescribeSecurityPolicy, ModifySecurityPolicy | 23 more |
| `data_analysis_agent` | DescribeOverviewL7Data, DescribeTopL7AnalysisData | 8 more |
| `content_management_agent` | CreatePurgeTask | 6 more |
| all other 21 agents | — | fully catalogued, 0 live |

Run `python -c "from edgeone_agents.api_catalog import total_api_count; print(total_api_count())"`
any time — it's always accurate, since every agent is generated from that
one file.

## Adding the next real API

Say you want `DescribePurgeTasks` (checking a purge task's status) live in
`content_management`:

1. Open `edgeone_agents/real_tools/content_management.py`.
2. Add a typed function with a Google-style docstring:
   ```python
   @tencent_api("DescribePurgeTasks")
   def describe_purge_tasks(zone_id: str, task_id: str | None = None) -> dict:
       """...Args/Returns..."""
       body = clean({"ZoneId": zone_id, "TaskId": task_id})
       return call_edgeone_api("DescribePurgeTasks", body)
   ```
3. Add it to that file's `TOOLS` list.
4. Nothing else changes — `agent_builder.py` will automatically stop
   generating a stub for `DescribePurgeTasks` and use your real function
   instead, next time the app starts.

Same pattern for a brand-new category that has zero real tools yet: create
`real_tools/<category_key>.py`, export `TOOLS = [...]`, done — the builder
already generates that agent from `api_catalog.py`, it just picks up the
real tools the moment the module exists.

## Using OpenAI through LiteLLM

Gemini is the default. The model resolver can instead construct ADK's
`LiteLlm` wrapper for the full Chat team and the isolated Incident Response
Team. Install the backend requirements, then set the following in `.env`:

```env
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
EDGEONE_LLM_PROVIDER=openai
EDGEONE_AGENT_MODEL=gpt-5-mini
EDGEONE_ORCHESTRATOR_MODEL=gpt-5-mini
INCIDENT_RESPONSE_MODEL=gpt-5-mini
```

The resolver accepts either `gpt-5-mini` or LiteLLM's explicit
`openai/gpt-5-mini` spelling. Restart the backend after changing model
configuration: agents are built when the process imports `edgeone_agents`.
If a copied `.env` still contains a `gemini-*` model name when the provider
is switched to OpenAI, the resolver safely substitutes `gpt-5-mini`.

## Why agents don't hallucinate the date anymore

LLMs have no innate sense of "now" — left alone, an agent asked for
"traffic in the last 30 minutes" will happily invent a plausible-but-wrong
`StartTime`/`EndTime` (this shipped with exactly that bug: a relative-time
query came back anchored to a fabricated date months in the past). Fixed
with three layers, cheapest/most-automatic first:

1. **`edgeone_agents/callbacks.py: inject_current_time`** — a
   `before_model_callback`, wired into every agent (including the
   orchestrator) in `agent_builder.py`/`agent.py`. It stamps the real UTC
   time into `llm_request.config.system_instruction` on *every single* LLM
   call, automatically, with zero reliance on the model remembering to ask.
2. **`edgeone_agents/real_tools/_time.py: get_current_time`** — a plain
   tool, also added to every agent, for when an agent wants to explicitly
   anchor relative language like "since yesterday" to a real date.
3. **`lookback_minutes` on the two data_analysis tools** — the belt-and-
   suspenders fix for the common case: `describe_overview_l7_data` and
   `describe_top_l7_analysis_data` now take a plain integer
   (`lookback_minutes=30`) and compute the actual `StartTime`/`EndTime` in
   Python from `datetime.now(timezone.utc)`. For "last N minutes/hours"
   requests — the majority case — the model never touches a date string at
   all, so it can't hallucinate one. Explicit `start_time`/`end_time` are
   still there for a specific historical window the user names.

All three were verified against a mocked HTTP layer and the real system
clock before shipping (every `system_instruction` shape the callback might
encounter, plus `lookback_minutes=30` resolving to an actual 30-minute
window anchored to the real current date, not a fabricated one).

## Known caveats — read before pointing this at production

- **`real_tools/_client.py`'s signing was verified structurally** (canonical
  request → string-to-sign → derived key → signature, cross-checked against
  Tencent's published EdgeOne signature doc and header format) **but not
  against a real live call** — this sandbox has no network access. Test
  `describe_zones` first; if you get `AuthFailure.SignatureFailure`, it's
  almost certainly a header/casing mismatch to diff against Tencent's API
  Explorer's "signature" debug output.
- The uploaded `api-structurally-mapped-definitions.go` reference is
  Tencent's **mainland** SDK (product 1552, Chinese comments), while
  `.env.example` defaults to the **international** endpoint (product 1145,
  which your `1145_47119_en.md`/`.pdf` docs are from). Field names are
  near-identical between the two, but if something 400s, check the field
  against whichever docs match your actual account/console region.
- `modify_security_policy` replaces `CustomRules`/`RateLimitingRules`
  wholesale for the target entity — always `describe_security_policy`
  first and merge, or you'll silently drop existing rules. This is called
  out in its docstring, but it's the one real tool here with teeth, so
  it's worth repeating.
- No retry/backoff, no pagination helpers, no `DescribePurgeTasks`-style
  "wait for the async task to finish" polling yet — this is a base
  structure, not a hardened client.
