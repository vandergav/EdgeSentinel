import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { getIncidentAttention, markAllIncidentsRead, type IncidentAttention } from "./incidents";

interface IncidentAttentionContextValue {
  attention: IncidentAttention | null;
  refreshAttention: () => Promise<void>;
  markAllRead: () => Promise<void>;
}

const IncidentAttentionContext = createContext<IncidentAttentionContextValue | null>(null);

export function IncidentAttentionProvider({ children }: { children: React.ReactNode }) {
  const [attention, setAttention] = useState<IncidentAttention | null>(null);

  const refreshAttention = useCallback(async () => {
    setAttention(await getIncidentAttention());
  }, []);

  const markAllRead = useCallback(async () => {
    await markAllIncidentsRead();
    await refreshAttention();
  }, [refreshAttention]);

  useEffect(() => {
    void refreshAttention().catch(() => undefined);
    const poll = window.setInterval(() => {
      if (document.visibilityState === "visible") void refreshAttention().catch(() => undefined);
    }, 15_000);
    return () => window.clearInterval(poll);
  }, [refreshAttention]);

  const value = useMemo(() => ({ attention, refreshAttention, markAllRead }), [attention, markAllRead, refreshAttention]);
  return <IncidentAttentionContext.Provider value={value}>{children}</IncidentAttentionContext.Provider>;
}

export function useIncidentAttention(): IncidentAttentionContextValue {
  const value = useContext(IncidentAttentionContext);
  if (!value) throw new Error("useIncidentAttention must be used inside IncidentAttentionProvider");
  return value;
}
