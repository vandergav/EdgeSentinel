/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_COPILOT_RUNTIME_URL: string;
  readonly VITE_AGENT_ID: string;
  readonly VITE_BACKEND_API_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
