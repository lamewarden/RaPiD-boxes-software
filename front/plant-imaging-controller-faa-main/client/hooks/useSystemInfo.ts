import { useEffect, useState } from "react";
import type { SystemInfo } from "@shared/api";
import { api } from "@/lib/api";

/**
 * Polls /api/system so the UI can react to things like camera availability
 * without needing a dedicated WebSocket channel for it. Also exposes a
 * setter so callers (e.g. a "recheck camera" button press) can apply a
 * fresher result immediately instead of waiting for the next poll tick.
 */
export function useSystemInfo(pollMs = 5000): [SystemInfo | null, (info: SystemInfo) => void] {
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

  return [info, setInfo];
}
