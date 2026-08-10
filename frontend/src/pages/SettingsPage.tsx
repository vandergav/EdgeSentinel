import { PlaceholderPanel } from "../components/PlaceholderPanel";

export function SettingsPage() {
  return (
    <div className="page">
      <header className="page__header">
        <h1>Settings</h1>
        <p className="page__subtitle">
          Connection and configuration options land here in a later phase — see
          backend/app/routers/settings.py for the matching API stub.
        </p>
      </header>

      <div className="panel-grid">
        <PlaceholderPanel
          title="Tencent Cloud credentials"
          description="Manage the SecretId/SecretKey the agent team's tools use, from the UI instead of hand-editing .env."
        />
        <PlaceholderPanel
          title="Model selection"
          description="Choose which Gemini, Claude, or GPT model powers each category agent."
        />
      </div>
    </div>
  );
}
