import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  approveRecommendation,
  createBlockIpRecommendation,
  deleteIncident,
  executeRecommendation,
  getIncident,
  getProactiveMode,
  listIncidents,
  markIncidentRead,
  runInvestigation,
  setProactiveMode,
  type IncidentDetail,
  type IncidentSummary,
} from "../lib/incidents";
import { useIncidentAttention } from "../lib/incidentAttention";

function formatTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function statusLabel(status: string, remediationExecuted = false): string {
  if (status === "source_resolved") return remediationExecuted ? "mitigated · TCOP resolved" : "resolved by TCOP";
  if (remediationExecuted) return "mitigated · awaiting TCOP recovery";
  if (status === "recovery_unmatched") return "unmatched recovery";
  return status.replaceAll("_", " ");
}

function humanize(value: string | null | undefined): string {
  if (!value) return "Incident";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function incidentHeadline(metric: string | null | undefined): string {
  if (metric === "host_requests") return "Host Requests Spike";
  return humanize(metric);
}

export function IncidentsPage() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [selected, setSelected] = useState<IncidentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [proactiveEnabled, setProactiveEnabled] = useState(false);
  const [proactiveAvailable, setProactiveAvailable] = useState(false);
  const [proactiveBusy, setProactiveBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [searchParams] = useSearchParams();
  const { refreshAttention } = useIncidentAttention();
  const previousActivityIds = useRef(new Map<string, number>());
  const receivedInitialList = useRef(false);
  const pendingQueryRead = useRef(searchParams.get("incident"));
  const [highlightedIncidentIds, setHighlightedIncidentIds] = useState<string[]>([]);

  const refreshIncidents = useCallback(async (preferredIncidentId?: string) => {
    setError(null);
    try {
      const items = await listIncidents();
      const previous = previousActivityIds.current;
      const changed = receivedInitialList.current
        ? items.filter((item) => previous.get(item.id) !== item.latest_event_id).map((item) => item.id)
        : [];
      previousActivityIds.current = new Map(items.map((item) => [item.id, item.latest_event_id]));
      receivedInitialList.current = true;
      if (changed.length > 0) {
        setHighlightedIncidentIds(changed);
        window.setTimeout(() => setHighlightedIncidentIds((current) => current.filter((id) => !changed.includes(id))), 2200);
      }
      setIncidents(items);
      const selectedId = preferredIncidentId ?? searchParams.get("incident") ?? selected?.id;
      const next = items.find((item) => item.id === selectedId) ?? items[0];
      const detail = next ? await getIncident(next.id) : null;
      setSelected(detail);
      if (detail && pendingQueryRead.current === detail.id) {
        pendingQueryRead.current = null;
        await markIncidentRead(detail.id, detail.latest_event_id);
        setIncidents((current) => current.map((item) => item.id === detail.id ? { ...item, is_unread: false } : item));
        void refreshAttention();
      }
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load incidents");
    } finally {
      setLoading(false);
    }
  }, [searchParams, selected?.id]);

  useEffect(() => {
    void refreshIncidents();
  }, [refreshIncidents]);

  useEffect(() => {
    void getProactiveMode()
      .then((mode) => {
        setProactiveEnabled(mode.enabled);
        setProactiveAvailable(mode.available);
      })
      .catch((requestError: unknown) =>
        setError(requestError instanceof Error ? requestError.message : "Unable to load proactive mode")
      );
  }, []);

  useEffect(() => {
    if (!selected) return;
    // Keep the selected case file current without requiring a page refresh.
    // Active automation benefits from a faster cadence; delayed evidence checks
    // and completed/manual cases use lighter polling.
    const interval = selected.proactive_workflow?.is_active
      ? 2500
      : selected.evidence_recheck_waiting
        ? 60_000
        : 15_000;
    const poll = window.setInterval(() => void refreshIncidents(selected.id), interval);
    return () => window.clearInterval(poll);
  }, [refreshIncidents, selected, selected?.evidence_recheck_waiting, selected?.proactive_workflow?.is_active]);

  const toggleProactiveMode = async () => {
    const nextEnabled = !proactiveEnabled;
    if (nextEnabled && !window.confirm("Enable Proactive Agent Team demo mode? This does not yet execute incident actions automatically.")) {
      return;
    }
    setProactiveBusy(true);
    setError(null);
    try {
      const mode = await setProactiveMode(nextEnabled);
      setProactiveEnabled(mode.enabled);
      setProactiveAvailable(mode.available);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Unable to update proactive mode");
    } finally {
      setProactiveBusy(false);
    }
  };

  const selectIncident = (incident: IncidentSummary) => {
    setError(null);
    void getIncident(incident.id)
      .then(async (detail) => {
        setSelected(detail);
        await markIncidentRead(detail.id, detail.latest_event_id);
        setIncidents((current) => current.map((item) => item.id === detail.id ? { ...item, is_unread: false } : item));
        void refreshAttention();
      })
      .catch((requestError: unknown) =>
        setError(requestError instanceof Error ? requestError.message : "Unable to load incident")
      );
  };

  const deleteTicket = async (incidentId: string) => {
    setDeleting(true);
    setError(null);
    try {
      await deleteIncident(incidentId);
      await refreshIncidents(selected?.id === incidentId ? undefined : selected?.id);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Unable to delete incident tickets");
    } finally {
      setDeleting(false);
    }
  };
  const incidentItem = (incident: IncidentSummary, isNewest: boolean) => (
    <div className={"incident-list__row" + (isNewest ? " incident-list__row--newest" : "") + (highlightedIncidentIds.includes(incident.id) ? " incident-list__row--updated" : "") + (incident.is_unread ? " incident-list__row--unread" : "") + (selected?.id === incident.id ? " incident-list__row--selected" : "")} key={incident.id}>
      <button
        type="button"
        className={"incident-list__item" + (selected?.id === incident.id ? " incident-list__item--active" : "")}
        onClick={() => selectIncident(incident)}
      >
        <span className="incident-list__status">{statusLabel(incident.status, incident.remediation_executed)}</span>
        <strong>{incident.title}</strong>
        <span>{incident.namespace ?? "Unknown namespace"}</span>
        <small>{formatTime(incident.last_activity_at)}</small>
      </button>
    </div>
  );

  return (
    <div className="page incidents-page">
      <header className="page__header">
        <div className="incidents-heading">
          <div>
            <h1>Incidents</h1>
            <p className="page__subtitle">
              Track active security incidents, review automated evidence and
              manually approve scoped blocks, or let the Proactive Agent Team autonomously assess and remediate threats.
            </p>
          </div>
          <div className="proactive-mode">
            <div>
              <strong>Proactive Agent Team</strong>
              <small>{proactiveAvailable ? (proactiveEnabled ? "Demo automation is active for qualified incidents." : "Enable to process qualified demo incidents automatically.") : "Disabled by the server demo kill switch."}</small>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={proactiveEnabled}
              className={"proactive-mode__toggle" + (proactiveEnabled ? " proactive-mode__toggle--enabled" : "")}
              disabled={proactiveBusy || !proactiveAvailable}
              onClick={() => void toggleProactiveMode()}
            >
              <span />
              <span className="sr-only">Toggle Proactive Agent Team mode</span>
            </button>
          </div>
        </div>
      </header>

      {error && <p className="incidents-error">{error}</p>}
      {loading ? (
        <p className="page__subtitle">Loading incidents…</p>
      ) : (
        <div className="incidents-layout">
          <section className="incident-list" aria-label="Incident case files">
            {incidents.length === 0 ? (
              <p className="page__subtitle">No TCOP callbacks received yet.</p>
            ) : (
              incidents.map((incident, index) => incidentItem(incident, index === 0))
            )}
          </section>

          <section className="incident-case" aria-live="polite">
            {selected ? (
              <IncidentCase
                incident={selected}
                onRefresh={refreshIncidents}
                onDelete={() => deleteTicket(selected.id)}
                deleting={deleting}
                proactiveModeEnabled={proactiveEnabled}
              />
            ) : <p className="page__subtitle">Select an incident.</p>}
          </section>
        </div>
      )}
    </div>
  );
}

function IncidentCase({
  incident,
  onRefresh,
  onDelete,
  deleting,
  proactiveModeEnabled,
}: {
  incident: IncidentDetail;
  onRefresh: (incidentId: string) => Promise<void>;
  onDelete: () => Promise<void>;
  deleting: boolean;
  proactiveModeEnabled: boolean;
}) {
  const alarm = incident.source_alarm;
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const isResolved = ["source_resolved", "recovery_unmatched"].includes(incident.status);
  // Only cases created while the mode was active are owned by proactive
  // automation. Older/manual cases remain actionable so enabling the switch
  // later cannot strand their queued work.
  const proactiveCase = incident.timeline.some((event) => event.type.startsWith("proactive."));
  const proactiveControlsOwnCase = proactiveModeEnabled && proactiveCase;
  const investigation = incident.timeline.find((event) => event.type === "investigation.completed");
  const agentAssessment = incident.timeline.find((event) => event.type === "proactive.agent_assessment.completed");
  const agentTrace = Array.isArray(agentAssessment?.payload.agent_trace) ? agentAssessment.payload.agent_trace : [];
  const evidence = Array.isArray(investigation?.payload.evidence) ? investigation.payload.evidence : [];
  const ipEvidence = evidence.find(
    (item): item is { source: string; candidates: Array<{ ip: string; requests: number | null }> } =>
      typeof item === "object" && item !== null && (item as { source?: unknown }).source === "top_client_ips" &&
      Array.isArray((item as { candidates?: unknown }).candidates)
  );
  const assessment = (
    agentAssessment && typeof agentAssessment.payload.assessment === "object" && agentAssessment.payload.assessment !== null
      ? agentAssessment.payload.assessment as Record<string, unknown>
      : null
  );
  const candidates = ipEvidence?.candidates ?? [];
  const leadingCandidate = candidates[0];
  const domain = typeof alarm?.dimensions.domain === "string" ? alarm.dimensions.domain : null;
  const [showAuditTrail, setShowAuditTrail] = useState(false);
  // Preserve the underlying audit log exactly as stored, while presenting the
  // newest operational events first. Older entries remain available on demand.
  const timelineNewestFirst = [...incident.timeline].sort((left, right) => (
    new Date(right.created_at).valueOf() - new Date(left.created_at).valueOf()
  ));
  const recentTimeline = timelineNewestFirst.slice(0, 5);
  const olderTimeline = timelineNewestFirst.slice(5);

  const createDraft = async (clientIp: string) => {
    setBusy(true);
    setActionError(null);
    try {
      await createBlockIpRecommendation(incident.id, clientIp);
      await onRefresh(incident.id);
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : "Unable to create recommendation");
    } finally {
      setBusy(false);
    }
  };

  const approve = async (recommendationId: string) => {
    setBusy(true);
    setActionError(null);
    try {
      await approveRecommendation(recommendationId);
      await onRefresh(incident.id);
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : "Unable to approve recommendation");
    } finally {
      setBusy(false);
    }
  };

  const runEvidenceCollection = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await runInvestigation(incident.id);
      await onRefresh(incident.id);
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : "Unable to run investigation");
    } finally {
      setBusy(false);
    }
  };

  const execute = async (recommendationId: string, clientIp: string, actionType: string) => {
    const message = actionType === "rate_limit_client_ip"
      ? `Apply a live rate limit for ${clientIp} (100 requests / 2 minutes, deny for 20 minutes)? This changes EdgeOne security policy.`
      : `Apply a live site-level Deny rule for ${clientIp}? This changes EdgeOne security policy.`;
    if (!window.confirm(message)) return;
    setBusy(true);
    setActionError(null);
    try {
      await executeRecommendation(recommendationId);
      await onRefresh(incident.id);
    } catch (error: unknown) {
      setActionError(error instanceof Error ? error.message : "Unable to execute recommendation");
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    await onDelete();
    setConfirmingDelete(false);
  };

  return (
    <>
      <div className="incident-case__header">
        <div>
          <span className="incident-list__status">{statusLabel(incident.status, incident.remediation_executed)}</span>
          <h2>{incidentHeadline(alarm?.metric_display_name ?? incident.title)}</h2>
          <p>{incident.id}</p>
        </div>
        <div className="incident-case__meta">
          <span>First seen {formatTime(incident.first_seen_at)}</span>
          <span>
            {incident.evidence_recheck_waiting
              ? `Waiting for EdgeOne client-IP data until ${formatTime(incident.next_evidence_recheck_at)}`
              : incident.investigation_queued ? "Investigation queued" : "No pending investigation"}
          </span>
          {!proactiveControlsOwnCase && !isResolved && incident.investigation_queued && (
            <button type="button" disabled={busy} onClick={() => void runEvidenceCollection()}>
              Run investigation
            </button>
          )}
          {!proactiveControlsOwnCase && !isResolved && incident.proactive_assessment_queued && (
            <button type="button" disabled={busy} onClick={() => void runEvidenceCollection()}>
              Run proactive dry run
            </button>
          )}
          {!proactiveControlsOwnCase && !isResolved && incident.proactive_agent_assessment_queued && (
            <button type="button" disabled={busy} onClick={() => void runEvidenceCollection()}>
              Run agent assessment
            </button>
          )}
        </div>
        <button
          type="button"
          className="incident-delete-icon"
          aria-label="Delete incident ticket"
          title="Delete incident ticket"
          disabled={deleting}
          onClick={() => setConfirmingDelete(true)}
        >
          <TrashIcon />
        </button>
      </div>

      {confirmingDelete && (
        <div className="incident-confirmation" role="presentation" onMouseDown={() => setConfirmingDelete(false)}>
          <div className="incident-confirmation__dialog" role="dialog" aria-modal="true" aria-labelledby="delete-incident-title" onMouseDown={(event) => event.stopPropagation()}>
            <h3 id="delete-incident-title">Delete incident ticket?</h3>
            <p>This removes the case file and its workflow data. Raw TCOP callback audit records are retained.</p>
            <div>
              <button type="button" onClick={() => setConfirmingDelete(false)}>Cancel</button>
              <button type="button" className="incident-confirmation__delete" disabled={deleting} onClick={() => void confirmDelete()}>
                {deleting ? "Deleting…" : "Delete ticket"}
              </button>
            </div>
          </div>
        </div>
      )}

      {isResolved && (
        <p className="page__subtitle">
          {incident.status === "source_resolved"
            ? "Resolved by TCOP. Pending proactive work was cancelled and remediation is disabled."
            : "Recovery notification received without a matching active incident. This is audit-only."}
        </p>
      )}

      {proactiveControlsOwnCase && !isResolved && (
        <p className="proactive-case-notice">
          Proactive Agent Team owns this case. Evidence, assessment, recommendation, and demo remediation progress automatically; turn off Proactive Agent Team to intervene manually.
        </p>
      )}

      <section className="incident-command-card" aria-label="Incident command summary">
        <div>
          <span className="incident-command-card__eyebrow">Incident command</span>
          <h3>{assessment ? humanize(String(assessment.classification ?? "Incident assessment")) : incidentHeadline(alarm?.metric_display_name ?? incident.title)}</h3>
          <p>
            {leadingCandidate
              ? `${leadingCandidate.ip} generated ${leadingCandidate.requests ?? "an unknown number of"} requests${domain ? ` against ${domain}` : ""}.`
              : "Evidence collection is determining whether a public client IP dominates this incident."}
          </p>
        </div>
        <dl>
          <div><dt>Status</dt><dd>{statusLabel(incident.status, incident.remediation_executed)}</dd></div>
          <div><dt>Confidence</dt><dd>{assessment?.confidence ? `${String(assessment.confidence)} confidence` : "Pending assessment"}</dd></div>
          <div><dt>Scope</dt><dd>{leadingCandidate ? "Single client IP" : "Evidence pending"}</dd></div>
          <div><dt>Action</dt><dd>{assessment?.recommended_action ? humanize(String(assessment.recommended_action)) : "Not decided"}</dd></div>
          <div><dt>Alarm</dt><dd>{alarm?.current_value ?? "Not supplied"} / {alarm?.threshold_value ?? "—"}</dd></div>
          <div><dt>Target</dt><dd>{domain ?? String(alarm?.dimensions.zoneid ?? "Not supplied")}</dd></div>
        </dl>
        <details className="incident-command-card__details">
          <summary>Show alarm and target details</summary>
          <dl>
            <div><dt>Metric</dt><dd>{alarm?.metric_display_name ?? "Not supplied"}</dd></div>
            <div><dt>Namespace</dt><dd>{incident.namespace ?? "Not supplied"}</dd></div>
            {Object.entries(alarm?.dimensions ?? {}).map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>
            ))}
          </dl>
        </details>
      </section>

      <div className="incident-detail-layout">
      <div className="incident-detail-layout__main">
      {assessment && (
      <section className="incident-response-card" aria-label="AI response">
        <div className="incident-decision-grid">
          <article className="incident-card incident-assessment-card">
            <span className="incident-command-card__eyebrow">AI incident assessment</span>
            <h3>{humanize(String(assessment.classification ?? "Assessment complete"))}</h3>
            <p>{String(assessment.rationale ?? "The Agent Team assessed the available incident evidence.")}</p>
            <dl>
              <div><dt>Recommended action</dt><dd>{humanize(String(assessment.recommended_action ?? "Not decided"))}</dd></div>
              <div><dt>Candidate IP</dt><dd>{String(assessment.candidate_ip ?? leadingCandidate?.ip ?? "Not available")}</dd></div>
            </dl>
          </article>
          <article className="incident-card incident-agent-card">
            <span className="incident-command-card__eyebrow">Agent activity</span>
            <h3>Response team</h3>
            <div className="incident-agent-card__steps">
              {incident.proactive_workflow.stages.slice(0, 4).map((stage) => (
                <div key={stage.id}>
                  <span className={`proactive-workflow__indicator proactive-workflow__indicator--${stage.status}`} />
                  <span>{stage.label}</span>
                  <small>{stage.status.replaceAll("_", " ")}</small>
                </div>
              ))}
            </div>
            {agentTrace.length > 0 && (
              <p className="agent-trace">Participants: {agentTrace.map((entry) => String((entry as Record<string, unknown>).agent ?? "unknown").replaceAll("_", " ")).join(" → ")}</p>
            )}
          </article>
        </div>
      </section>
      )}

      <article className="incident-card incident-card--timeline incident-remediation-card" aria-label="AI remediation">
        <div className="incident-remediation-card__heading">
          <div>
            <span className="incident-command-card__eyebrow">AI remediation</span>
            <h3>Recommended action</h3>
          </div>
          {Boolean(assessment?.recommended_action) && <small>{humanize(String(assessment?.recommended_action))}</small>}
        </div>
        {actionError && <p className="incidents-error">{actionError}</p>}
        {incident.recommendations.length === 0 ? (
          <div className="remediation-empty-state">
            <p className="page__subtitle">
              {leadingCandidate
                ? "The Agent Team has enough context to prepare a targeted mitigation."
                : "Waiting for the Agent Team to complete its assessment and propose an action."}
            </p>
            {leadingCandidate && !isResolved && !proactiveControlsOwnCase && (
              <button type="button" disabled={busy} onClick={() => void createDraft(leadingCandidate.ip)}>
                Create recommended block
              </button>
            )}
          </div>
        ) : (
          incident.recommendations.map((recommendation) => (
            <div className="recommendation recommendation--action" key={recommendation.id}>
              <strong>{humanize(recommendation.action_type)}</strong>
              <small>Version {recommendation.version} · {recommendation.status}</small>
              <span>{ruleSummary(recommendation.proposed_rule)}</span>
              {recommendation.status === "executed" && (
                <div className="remediation-confirmation" role="status">
                  <span aria-hidden="true">✓</span>
                  <div>
                    <strong>Action applied to EdgeOne</strong>
                    <p>The approved rule is active for this incident’s recommended scope.</p>
                  </div>
                </div>
              )}
              {recommendation.action_type === "rate_limit_client_ip" && (
                <small>100 requests / 2 minutes · deny for 20 minutes</small>
              )}
              {recommendation.status === "stale" && (
                <small>Create a new draft to capture the current policy.</small>
              )}
              {!proactiveControlsOwnCase && !isResolved && recommendation.status === "draft" && (
                <button type="button" disabled={busy} onClick={() => void approve(recommendation.id)}>
                  Approve block rule
                </button>
              )}
              {!proactiveControlsOwnCase && !isResolved && recommendation.status === "approved" && (
                <>
                  <button
                    type="button"
                    className="recommendation__execute"
                    disabled={busy}
                    onClick={() => void execute(recommendation.id, recommendation.target.client_ip, recommendation.action_type)}
                  >
                    Block IP
                  </button>
                </>
              )}
            </div>
          ))
        )}
      </article>
      </div>

      <aside className="incident-story-panel" aria-label="Incident story">
        <div className="incident-story-panel__header">
          <div>
            <span className="incident-command-card__eyebrow">Case history</span>
            <h3>Incident story</h3>
          </div>
          <small>Live updates</small>
        </div>
        <div className="incident-story-panel__events">
          {recentTimeline.map((event) => (
            <div className="incident-timeline__item" key={event.id}>
              <span>{formatTime(event.created_at)}</span>
              <strong>{event.summary}</strong>
            </div>
          ))}
          {olderTimeline.length > 0 && (
            <button type="button" className="incident-audit-toggle" onClick={() => setShowAuditTrail((current) => !current)}>
              {showAuditTrail ? "Hide older events" : `Show ${olderTimeline.length} older events`}
            </button>
          )}
          {showAuditTrail && olderTimeline.map((event) => (
            <div className="incident-timeline__item incident-timeline__item--audit" key={event.id}>
              <span>{formatTime(event.created_at)}</span>
              <strong>{event.summary}</strong>
            </div>
          ))}
        </div>
      </aside>
      </div>
    </>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M4 7h16M10 11v6m4-6v6M9 7l1-2h4l1 2m-9 0 1 13h10l1-13" />
    </svg>
  );
}

function ruleSummary(rule: Record<string, unknown>): string {
  if (typeof rule.Condition === "string") return rule.Condition;
  const conditions = rule.AclConditions ?? rule.Conditions;
  if (Array.isArray(conditions) && conditions[0] && typeof conditions[0] === "object") {
    const condition = conditions[0] as Record<string, unknown>;
    const matchValue = Array.isArray(condition.MatchValue) ? condition.MatchValue.join(", ") : condition.MatchContent;
    return `${String(condition.MatchFrom ?? condition.MatchKey ?? "field")} ${String(condition.Operator ?? condition.MatchAction ?? "matches")} ${String(matchValue ?? "")}`;
  }
  return "Proposed security rule";
}
