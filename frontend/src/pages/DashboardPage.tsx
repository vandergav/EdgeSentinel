import { Link } from "react-router-dom";

import { PlaceholderPanel } from "../components/PlaceholderPanel";
import { useIncidentAttention } from "../lib/incidentAttention";

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function incidentStatusLabel(status: string, remediationExecuted: boolean): string {
  if (status === "source_resolved") return remediationExecuted ? "mitigated · TCOP resolved" : "resolved by TCOP";
  if (remediationExecuted) return "mitigated · awaiting TCOP recovery";
  return status.replaceAll("_", " ");
}

export function DashboardPage() {
  const { attention, markAllRead } = useIncidentAttention();
  return (
    <div className="page">
      <header className="page__header">
        <h1>Dashboard</h1>
        <p className="page__subtitle">
          Recent TCOP case activity is shown below. Live traffic metrics and broader
          EdgeOne monitoring remain the next dashboard phase.
        </p>
      </header>

      <div className="panel-grid">
        <PlaceholderPanel
          title="Live metrics"
          description="Traffic, cache hit ratio, and origin health, polled from the data_analysis agent's own tools."
        />
        <section className="dashboard-incidents" aria-label="Recent incidents">
          <div className="dashboard-incidents__header">
            <div>
              <span className="placeholder-panel__badge">Live case files</span>
              <h2>Incidents</h2>
            </div>
            <div className="dashboard-incidents__actions">
              {(attention?.unread_count ?? 0) > 0 && <span className="incident-unread-badge" aria-label={`${attention?.unread_count} unread incident updates`}>{attention?.unread_count}</span>}
              {(attention?.unread_count ?? 0) > 0 && <button type="button" onClick={() => void markAllRead()}>Mark all read</button>}
            </div>
          </div>
          {!attention ? (
            <p className="placeholder-panel__description">Loading recent incident activity…</p>
          ) : attention.items.length === 0 ? (
            <p className="placeholder-panel__description">No TCOP incident callbacks received yet.</p>
          ) : (
            <div className="dashboard-incidents__items">
              {attention.items.map((incident) => (
                <Link className={"dashboard-incidents__item" + (incident.is_unread ? " dashboard-incidents__item--unread" : "")} key={incident.id} to={`/incidents?incident=${encodeURIComponent(incident.id)}`}>
                  <span className="dashboard-incidents__marker" aria-hidden="true" />
                  <span>
                    <strong>{incident.title}</strong>
                    <small>{incidentStatusLabel(incident.status, incident.remediation_executed)} · {formatTime(incident.last_activity_at)}</small>
                  </span>
                </Link>
              ))}
            </div>
          )}
          <Link className="dashboard-incidents__all" to="/incidents">View all incidents →</Link>
        </section>
        <PlaceholderPanel
          title="Agent activity timeline"
          description="A running log of what the agent team has diagnosed, recommended, and changed, and when."
        />
      </div>
    </div>
  );
}
