/** Thin typed client for the RaPiD-boxes FastAPI backend (same origin). */
import type {
  CameraSettings,
  DeviceSettings,
  ExperimentConfig,
  ExperimentStatus,
  HistoryEntry,
  ImageListResponse,
  RemoteSyncCheckResult,
  RemoteSyncStatus,
  RemoteSyncUpdate,
  SavedExperimentConfig,
  StartResponse,
  SystemInfo,
  UpdateApplyResult,
  UpdateCheckResult,
  VersionStatus,
} from "@shared/api";

async function errorDetail(res: Response): Promise<string> {
  let detail = res.statusText;
  try {
    detail = (await res.json()).detail ?? detail;
  } catch {
    /* ignore */
  }
  return detail;
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
  currentStatus: () => jsonFetch<ExperimentStatus>("/api/experiments/current"),
  pause: () => jsonFetch<ExperimentStatus>("/api/experiments/current/pause", { method: "POST" }),
  resume: () => jsonFetch<ExperimentStatus>("/api/experiments/current/resume", { method: "POST" }),
  stop: () => jsonFetch<ExperimentStatus>("/api/experiments/current/stop", { method: "POST" }),
  abort: () => jsonFetch<ExperimentStatus>("/api/experiments/current/abort", { method: "POST" }),
  history: () => jsonFetch<HistoryEntry[]>("/api/experiments/history"),
  experimentConfig: (id: string) =>
    jsonFetch<SavedExperimentConfig>(`/api/experiments/${id}/config`),
  /** URL for the zipped experiment folder (images + metadata + config XML).
   * No fetch needed -- an <a href> download or window.location navigation
   * lets the browser handle the actual save. */
  experimentDownloadUrl: (id: string) => `/api/experiments/${id}/download`,
  images: (experimentId?: string) =>
    jsonFetch<ImageListResponse>(experimentId ? `/api/images/${experimentId}` : "/api/images"),
  settings: () => jsonFetch<DeviceSettings>("/api/settings"),
  saveSettings: (s: DeviceSettings) =>
    jsonFetch<DeviceSettings>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
  remoteSync: () => jsonFetch<RemoteSyncStatus>("/api/settings/remote-sync"),
  /** Patch the remote-sync config. `password` is write-only and never comes back. */
  saveRemoteSync: (update: RemoteSyncUpdate) =>
    jsonFetch<RemoteSyncStatus>("/api/settings/remote-sync", {
      method: "PUT",
      body: JSON.stringify(update),
    }),
  checkRemoteSync: () =>
    jsonFetch<RemoteSyncCheckResult>("/api/settings/remote-sync/check", { method: "POST" }),
  syncAllRemote: (researcher: string) =>
    jsonFetch<RemoteSyncStatus>("/api/settings/remote-sync/sync-all", {
      method: "POST",
      body: JSON.stringify({ researcher }),
    }),
  health: () => jsonFetch<{ ok: boolean; version: string }>("/api/health"),
  system: () => jsonFetch<SystemInfo>("/api/system"),
  recheckCamera: () => jsonFetch<SystemInfo>("/api/system/recheck-camera", { method: "POST" }),
  closeKiosk: () => jsonFetch<{ status: string; kioskPids: number[] }>("/api/system/close-kiosk", { method: "POST" }),
  restartService: () => jsonFetch<{ status: string }>("/api/system/restart-service", { method: "POST" }),
  checkForUpdate: () => jsonFetch<UpdateCheckResult>("/api/system/update/check"),
  applyUpdate: () => jsonFetch<UpdateApplyResult>("/api/system/update/apply", { method: "POST" }),
  versionStatus: () => jsonFetch<VersionStatus>("/api/system/update/version"),
  rollbackUpdate: () => jsonFetch<UpdateApplyResult>("/api/system/update/rollback", { method: "POST" }),
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
  /** Live-view assist light: RGBW fill (10,10,10,10), IR boards high, or off. */
  setLiveBacklight: (mode: "off" | "white" | "ir") =>
    jsonFetch<{ mode: "off" | "white" | "ir" }>("/api/preview/backlight", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
};

/** Resolve the WebSocket URL for live status against the current origin. */
export function statusWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/ws`;
}
