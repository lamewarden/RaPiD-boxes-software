/** Thin typed client for the RaPiD-boxes FastAPI backend (same origin). */
import type {
  AssistantChatResponse,
  AssistantMessage,
  CameraSettings,
  DeviceSettings,
  ExperimentConfig,
  ExperimentStatus,
  FreeSpaceResponse,
  HistoryEntry,
  ImageListResponse,
  PlantMaskStatus,
  RemoteSyncCheckResult,
  RemoteSyncStatus,
  RemoteSyncUpdate,
  SavedExperimentConfig,
  StartResponse,
  SystemInfo,
  UpdateApplyResult,
  UpdateCheckResult,
  UserSummary,
  VersionStatus,
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
  /** URL for the zipped experiment folder (images + metadata + config XML).
   * No fetch needed -- an <a href> download or window.location navigation
   * lets the browser handle the actual save. */
  experimentDownloadUrl: (id: string) => `/api/experiments/${id}/download`,
  /** URL for one zip of every experiment belonging to `username`, each under
   *  its own folder inside the archive. Same "just let the browser save it"
   *  pattern as experimentDownloadUrl. */
  experimentDownloadAllUrl: (username: string) =>
    `/api/experiments/download-all?username=${encodeURIComponent(username)}`,
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
  /** Usernames this device has seen -- from experiment history and saved
   *  "Mine" settings -- for the user-select picker. */
  users: () => jsonFetch<UserSummary[]>("/api/users"),
  saveSettings: (s: DeviceSettings) =>
    jsonFetch<DeviceSettings>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
  /** This user's saved baseline ("Mine") -- camera + illumination as one
   *  bundle -- or null if they've never saved one. */
  myDefaults: (username: string) =>
    jsonFetch<DeviceSettings | null>(`/api/settings/mine?username=${encodeURIComponent(username)}`),
  saveMyDefaults: (username: string, settings: DeviceSettings) =>
    jsonFetch<DeviceSettings>("/api/settings/mine", {
      method: "PUT",
      body: JSON.stringify({ username, settings }),
    }),
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
  /** Live-view assist light: RGBW fill (10,10,10,10) or off. IR is Settings test-photo only. */
  setLiveBacklight: (mode: "off" | "white") =>
    jsonFetch<{ mode: "off" | "white" }>("/api/preview/backlight", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  /** 409s while an experiment is running/paused/finishing; 503 if the
   *  assistant API isn't reachable. `history` is the client's own copy so a
   *  page refresh doesn't lose context (the server keeps one too, for
   *  archiving -- see AssistantService). */
  assistantChat: (message: string, history: AssistantMessage[], username?: string) =>
    jsonFetch<AssistantChatResponse>("/api/assistant/chat", {
      method: "POST",
      body: JSON.stringify({ message, history, username }),
    }),
};

/** Resolve the WebSocket URL for live status against the current origin. */
export function statusWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/ws`;
}
