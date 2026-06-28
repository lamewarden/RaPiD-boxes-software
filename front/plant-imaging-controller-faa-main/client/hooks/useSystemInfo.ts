import { useEffect, useState } from "react";
import type { SystemInfo } from "@shared/api";
import { api } from "@/lib/api";

/**
 * Polls /api/system so the UI can react to things like camera availability
 * without needing a dedicated WebSocket channel for it.
 */
export function useSystemInfo(pollMs = 5000): SystemInfo | null {
  const [info, setInfo] = useState<SystemInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const result = await api.system();
        if (!cancelled) setInfo(result);
      } catch {
        /* keep last known value; backend may be restarting */
      }
    };
    poll();
    const id = setInterval(poll, pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pollMs]);

  return info;
}
