/** Thin typed client for the RaPiD-boxes FastAPI backend (same origin). */
import type {
  DeviceSettings,
  ExperimentStatus,
  ImageListResponse,
  StartResponse,
  SystemInfo,
  TropismConfig,
} from "@shared/api";

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  startExperiment: (config: TropismConfig) =>
    jsonFetch<StartResponse>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  currentStatus: () => jsonFetch<ExperimentStatus>("/api/experiments/current"),
  pause: () => jsonFetch<ExperimentStatus>("/api/experiments/current/pause", { method: "POST" }),
  resume: () => jsonFetch<ExperimentStatus>("/api/experiments/current/resume", { method: "POST" }),
  stop: () => jsonFetch<ExperimentStatus>("/api/experiments/current/stop", { method: "POST" }),
  history: () => jsonFetch<unknown[]>("/api/experiments/history"),
  images: (experimentId?: string) =>
    jsonFetch<ImageListResponse>(experimentId ? `/api/images/${experimentId}` : "/api/images"),
  settings: () => jsonFetch<DeviceSettings>("/api/settings"),
  saveSettings: (s: DeviceSettings) =>
    jsonFetch<DeviceSettings>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
  system: () => jsonFetch<SystemInfo>("/api/system"),
  recheckCamera: () => jsonFetch<SystemInfo>("/api/system/recheck-camera", { method: "POST" }),
};

/** Resolve the WebSocket URL for live status against the current origin. */
export function statusWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/ws`;
}
