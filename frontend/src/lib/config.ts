/**
 * Central place for frontend runtime config. Everything reads from
 * import.meta.env (Vite) so there's exactly one spot to change if the
 * copilot-runtime bridge's URL or the agent id ever changes.
 */
export const COPILOT_RUNTIME_URL: string =
  import.meta.env.VITE_COPILOT_RUNTIME_URL ?? "http://localhost:3001/api/copilotkit";

/** The protocol bridge also exposes a local, read-only demo trace stream. */
export const COPILOT_RUNTIME_ORIGIN: string = COPILOT_RUNTIME_URL.replace(/\/api\/copilotkit\/?$/, "");

export const AGENT_ID: string = import.meta.env.VITE_AGENT_ID ?? "edgeone_agent";

export const BACKEND_API_URL: string = import.meta.env.VITE_BACKEND_API_URL ?? "http://localhost:8000";
