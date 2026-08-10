import { CopilotKit } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";

import { AppShell } from "./layout/AppShell";
import { AGENT_ID, COPILOT_RUNTIME_URL } from "./lib/config";
import { IncidentAttentionProvider } from "./lib/incidentAttention";
import { DashboardPage } from "./pages/DashboardPage";
import { IncidentsPage } from "./pages/IncidentsPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  return (
    <CopilotKit runtimeUrl={COPILOT_RUNTIME_URL} agent={AGENT_ID} useSingleEndpoint>
      <IncidentAttentionProvider>
        <Router>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/chat" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/incidents" element={<IncidentsPage />} />
              <Route path="/chat" element={null} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </Router>
      </IncidentAttentionProvider>
    </CopilotKit>
  );
}
