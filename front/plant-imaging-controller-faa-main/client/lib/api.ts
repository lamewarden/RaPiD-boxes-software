/** Thin typed client for the RaPiD-boxes FastAPI backend (same origin). */
import type {
  CameraSettings,
  DeviceSettings,
  ExperimentConfig,
  ExperimentStatus,
  FreeSpaceResponse,
  HistoryEntry,
  ImageListResponse,
  PlantMaskStatus,
  SavedExperimentConfig,
  StartResponse,
  SystemInfo,
} from "@shared/api";

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item: { msg?: string; loc?: unknown[] }) => {
          const loc = Array.isArray(item.loc)
            ? item.loc.filter((p) => p !== "body").join(".")
            : "";
          const msg = item.msg ?? JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join("; ");
    }
    if (detail != null) return JSON.stringify(detail);
  } catch {
    /* ignore */
  }
  return res.statusText;
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`${res.status}: ${await errorDetail(res)}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  startExperiment: (config: ExperimentConfig) =>
    jsonFetch<StartResponse>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  /** Deletes the given experiment ids -- backend re-checks ownership against
   *  `username` server-side, so only the requesting user's own folders are
   *  ever removed. Used to resolve a "low_space" startExperiment response. */
  freeSpace: (username: string, experimentIds: string[]) =>
    jsonFetch<FreeSpaceResponse>("/api/experiments/free-space", {
      method: "POST",
      body: JSON.stringify({ username, experimentIds }),
    }),
  currentStatus: () => jsonFetch<ExperimentStatus>("/api/experiments/current"),
  pause: () => jsonFetch<ExperimentStatus>("/api/experiments/current/pause", { method: "POST" }),
  resume: () => jsonFetch<ExperimentStatus>("/api/experiments/current/resume", { method: "POST" }),
  stop: () => jsonFetch<ExperimentStatus>("/api/experiments/current/stop", { method: "POST" }),
  abort: () => jsonFetch<ExperimentStatus>("/api/experiments/current/abort", { method: "POST" }),
  history: () => jsonFetch<HistoryEntry[]>("/api/experiments/history"),
  experimentConfig: (id: string) =>
    jsonFetch<SavedExperimentConfig>(`/api/experiments/${id}/config`),
  images: (experimentId?: string) =>
    jsonFetch<ImageListResponse>(experimentId ? `/api/images/${experimentId}` : "/api/images"),
  /** Growth heatmap JPEG URL; `v` cache-busts when the frame set grows. */
  growthUrl: (experimentId: string, imageCount: number) =>
    `/api/images/${encodeURIComponent(experimentId)}/growth?v=${imageCount}`,
  startPlantMask: (experimentId: string) =>
    jsonFetch<PlantMaskStatus>(`/api/images/${encodeURIComponent(experimentId)}/plant-mask/start`, {
      method: "POST",
    }),
  plantMaskStatus: (experimentId: string) =>
    jsonFetch<PlantMaskStatus>(`/api/images/${encodeURIComponent(experimentId)}/plant-mask/status`),
  settings: () => jsonFetch<DeviceSettings>("/api/settings"),
  saveSettings: (s: DeviceSettings) =>
    jsonFetch<DeviceSettings>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
  health: () => jsonFetch<{ ok: boolean; version: string }>("/api/health"),
  system: () => jsonFetch<SystemInfo>("/api/system"),
  recheckCamera: () => jsonFetch<SystemInfo>("/api/system/recheck-camera", { method: "POST" }),
  closeKiosk: () => jsonFetch<{ status: string; kioskPids: number[] }>("/api/system/close-kiosk", { method: "POST" }),
  restartService: () => jsonFetch<{ status: string }>("/api/system/restart-service", { method: "POST" }),
  testPhotoWithSettings: async (settings: CameraSettings): Promise<Blob> => {
    const res = await fetch("/api/preview/test-photo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    if (!res.ok) {
      throw new Error(`${res.status}: ${await errorDetail(res)}`);
    }
    return res.blob();
  },
  /** Live-view assist light: RGBW fill (10,10,10,10) or off. IR is Settings test-photo only. */
  setLiveBacklight: (mode: "off" | "white") =>
    jsonFetch<{ mode: "off" | "white" }>("/api/preview/backlight", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
};

/** Resolve the WebSocket URL for live status against the current origin. */
export function statusWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/ws`;
}
