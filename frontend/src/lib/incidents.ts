import { BACKEND_API_URL } from "./config";

export interface IncidentSummary {
  id: string;
  title: string;
  namespace: string | null;
  policy_id: string | null;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  source_resolved_at: string | null;
  created_at: string;
  updated_at: string;
  remediation_executed: boolean;
  latest_event_id: number;
  last_activity_at: string;
  is_unread: boolean;
  investigation_queued: boolean;
  proactive_assessment_queued?: boolean;
}

export interface IncidentDeleteResult {
  deleted_ids: string[];
  missing_ids: string[];
}

export interface ProactiveMode {
  enabled: boolean;
  available: boolean;
  updated_at: string | null;
}

export interface IncidentTimelineEvent {
  id: number;
  type: string;
  summary: string;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface Recommendation {
  id: string;
  version: number;
  status: "draft" | "approved" | "stale" | "executed";
  action_type: string;
  target: { zone_id: string; host: string | null; client_ip: string };
  proposed_rule: Record<string, unknown>;
}

export interface ProactiveWorkflow {
  is_active: boolean;
  recommendation_id: string | null;
  stages: Array<{
    id: "evidence" | "qualification" | "agent_team" | "execution" | "verification";
    label: string;
    status: "not_started" | "waiting" | "queued" | "running" | "completed" | "cancelled" | "failed" | "pending";
  }>;
}

export interface IncidentDetail extends IncidentSummary {
  source_alarm: {
    metric_display_name: string | null;
    current_value: string | null;
    threshold_value: string | null;
    unit: string | null;
    dimensions: Record<string, unknown>;
  } | null;
  timeline: IncidentTimelineEvent[];
  job: { id: number; job_type: string; status: string; created_at: string; available_at?: string | null } | null;
  evidence_recheck_waiting: boolean;
  next_evidence_recheck_at: string | null;
  proactive_assessment_queued: boolean;
  proactive_agent_assessment_queued: boolean;
  proactive_workflow: ProactiveWorkflow;
  recommendations: Recommendation[];
}

export interface IncidentAttention {
  unread_count: number;
  items: IncidentSummary[];
}

const VIEWER_ID_KEY = "edgeone-platform.incident-viewer-id";

function incidentViewerId(): string {
  const existing = window.localStorage.getItem(VIEWER_ID_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID().replaceAll("-", "");
  window.localStorage.setItem(VIEWER_ID_KEY, created);
  return created;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-Incident-Viewer-ID", incidentViewerId());
  const response = await fetch(`${BACKEND_API_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function listIncidents(): Promise<IncidentSummary[]> {
  const response = await request<{ items: IncidentSummary[] }>("/api/incidents");
  return response.items;
}

export function getIncidentAttention(): Promise<IncidentAttention> {
  return request<IncidentAttention>("/api/incidents/attention");
}

export function markIncidentRead(incidentId: string, throughEventId: number): Promise<unknown> {
  return request(`/api/incidents/${encodeURIComponent(incidentId)}/read`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ through_event_id: throughEventId }),
  });
}

export function markAllIncidentsRead(): Promise<unknown> {
  return request("/api/incidents/read-all", { method: "POST" });
}

export function getIncident(incidentId: string): Promise<IncidentDetail> {
  return request<IncidentDetail>(`/api/incidents/${encodeURIComponent(incidentId)}`);
}

export function deleteIncident(incidentId: string): Promise<IncidentDeleteResult> {
  return request<IncidentDeleteResult>(`/api/incidents/${encodeURIComponent(incidentId)}`, {
    method: "DELETE",
  });
}

export function getProactiveMode(): Promise<ProactiveMode> {
  return request<ProactiveMode>("/api/incidents/proactive-mode");
}

export function setProactiveMode(enabled: boolean): Promise<ProactiveMode> {
  return request<ProactiveMode>("/api/incidents/proactive-mode", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export function createBlockIpRecommendation(
  incidentId: string,
  clientIp: string,
  strategy: "modern" | "legacy_acl" = "modern"
): Promise<Recommendation> {
  return request<Recommendation>(`/api/incidents/${encodeURIComponent(incidentId)}/recommendations/block-ip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_ip: clientIp, strategy }),
  });
}

export function createRateLimitRecommendation(incidentId: string, clientIp: string): Promise<Recommendation> {
  return request<Recommendation>(`/api/incidents/${encodeURIComponent(incidentId)}/recommendations/rate-limit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_ip: clientIp }),
  });
}

export function approveRecommendation(recommendationId: string): Promise<Recommendation> {
  return request<Recommendation>(`/api/incidents/recommendations/${encodeURIComponent(recommendationId)}/approve`, {
    method: "POST",
  });
}

export function runInvestigation(incidentId: string): Promise<unknown> {
  return request(`/api/incidents/jobs/run-once?incident_id=${encodeURIComponent(incidentId)}`, {
    method: "POST",
  });
}

export function executeRecommendation(recommendationId: string): Promise<unknown> {
  return request(`/api/incidents/recommendations/${encodeURIComponent(recommendationId)}/execute`, {
    method: "POST",
  });
}

export function preflightRecommendation(recommendationId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(`/api/incidents/recommendations/${encodeURIComponent(recommendationId)}/preflight`);
}
