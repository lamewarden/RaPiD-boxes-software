import { useEffect, useRef, useState } from "react";
import type { ExperimentStatus } from "@shared/api";
import { statusWsUrl } from "@/lib/api";

/**
 * Subscribes to the backend WebSocket and returns the latest ExperimentStatus.
 * Auto-reconnects so a backend restart simply resumes the live feed.
 */
export function useExperimentStatus(): { status: ExperimentStatus | null; connected: boolean } {
  const [status, setStatus] = useState<ExperimentStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;

    const connect = () => {
      const ws = new WebSocket(statusWsUrl());
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        try {
          setStatus(JSON.parse(ev.data) as ExperimentStatus);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  return { status, connected };
}
