/**
 * Shared API contract — mirrors the pydantic models in
 * back/rapidboxes/models.py. Keep the two in sync.
 */

export type Spectrum = "white" | "red" | "green" | "blue";

export interface TropismConfig {
  protocol: "tropism";
  experimentName: string;
  username: string;
  darkPhaseEnabled: boolean;
  darkPhaseHours: number;
  lateralIlluminationHours: number;
  spectra: Spectrum[];
  intervalMinutes: number;
  intensity: number;
}

export type PhotoIlluminationSource = "ir" | "rgbw";

export interface GrowthConfig {
  protocol: "growth";
  experimentName: string;
  username: string;
  dayLengthHours: number;
  experimentLengthDays: number;
  spectra: Spectrum[];
  dayIntensity: number;
  intervalMinutes: number;
}

export type ExperimentConfig = TropismConfig | GrowthConfig;

export type ExperimentState =
  | "idle"
  | "running"
  | "paused"
  | "finishing"
  | "done"
  | "error";

export type ExperimentPhase =
  | "dark"
  | "bending"
  | "baseline"
  | "day"
  | "night";

export interface ExperimentStatus {
  state: ExperimentState;
  phase: ExperimentPhase | null;
  experimentId: string | null;
  experimentName: string | null;
  username: string | null;
  startedAt: string | null;
  elapsedSeconds: number;
  totalSeconds: number;
  phaseElapsedSeconds: number;
  phaseTotalSeconds: number;
  imagesCaptured: number;
  imagesPlanned: number;
  nextCaptureInSeconds: number | null;
  lastImageId: string | null;
  message: string | null;
  config: ExperimentConfig | null;
  dayIndex: number | null;
  totalDays: number | null;
}

export interface StartResponse {
  status: "started" | "busy" | "no_camera";
  experimentId: string | null;
}

export interface HistoryEntry {
  id: string;
  name: string | null;
  username: string | null;
  startedAt: string | null;
  state: string | null;
  imagesCaptured: number;
}

export interface ImageInfo {
  id: string;
  phase: string;
  index: number;
  timestamp: string;
  url: string;
  thumbUrl: string;
}

export interface ImageListResponse {
  experimentId: string | null;
  images: ImageInfo[];
}

export interface SystemInfo {
  hostname: string;
  ip: string;
  version: string;
  simulation: boolean;
  storageRoot: string;
  diskFreeBytes: number;
  diskTotalBytes: number;
  cameraAvailable: boolean;
  // Settings -> General -> SSH Access. Build the full command client-side
  // (`ssh ${sshUser}@${ip}`) -- `ip` above is the one source of truth.
  sshUser: string;
  sshEnabled: boolean;
}

// OTA self-update (Settings -> General -> Update button).
export interface UpdateCheckResult {
  branch: string;
  updateAvailable: boolean;
  currentCommit: string | null;
  remoteCommit: string | null;
  commitsBehind: number;
  commitLog: string[];
  error: string | null;
}

export interface UpdateApplyResult {
  status:
    | "updated"
    | "up_to_date"
    | "error"
    | "experiment_active"
    | "rolled_back"
    | "nothing_to_roll_back_to";
  message: string;
  fromCommit: string | null;
  toCommit: string | null;
  // Set only when status is "updated" or "rolled_back". "failed" means the
  // git move succeeded but the post-move dependency/build step did not --
  // code and installed deps are now mismatched, a worse state than a clean
  // refusal.
  rebuildStatus: "skipped" | "ok" | "failed" | null;
  rebuildMessage: string | null;
}

// One row in the OTA update history (Settings -> General -> Version).
export interface UpdateHistoryEntry {
  commit: string;
  appliedAt: string; // ISO 8601
  trigger: "manual" | "monthly" | "rollback" | "seed";
}

export interface VersionStatus {
  current: UpdateHistoryEntry | null;
  previous: UpdateHistoryEntry | null;
  error: string | null;
}

export interface CameraSettings {
  width: number;
  height: number;
  exposureMicroseconds: number;
  iso: number;
  autofocusEnabled: boolean;
  focusDistance: number;
  grayscale: boolean;
  jpegQuality: number;
  /** Digital zoom 1-5x: center-crop to 1/zoom of the frame, scaled back up to
   *  width x height. Applied to every capture, not just a preview. */
  zoom: number;
}

export interface LedSettings {
  pixelCount: number;
  pixelOrder: string;
  topSegment: [number, number];
  lateralSegment: [number, number];
  spiHz: number;
  /** Fire every Nth pixel within a lit segment (1 = every pixel, 5 = every 5th). */
  stride: number;
}

export interface IrSettings {
  /** BCM pins driving the IR boards. */
  pins: number[];
}

export interface ExposureProfile {
  /** Exposure (µs) the settings snap to when this source is selected. */
  default: number;
  min: number;
  max: number;
  /**
   * How slider travel maps to exposure. RGBW is logarithmic because the useful
   * values bunch at the short end — this gives 0.01–0.1s most of the track and
   * compresses 0.2–0.5s. IR is discrete 0.2 s notches from 0.2–10 s (see
   * client/lib/exposure.ts); `scale: "linear"` here means equal notch spacing.
   */
  scale: "linear" | "log";
}

/**
 * Exposure travels with the illumination source: IR needs a long integration,
 * the RGBW flash is bright. Mirrors EXPOSURE_PROFILES in
 * back/rapidboxes/models.py, which enforces default/min/max server-side.
 */
export const EXPOSURE_PROFILES: Record<PhotoIlluminationSource, ExposureProfile> = {
  // Default 3.6 s sits on the 0.2 s IR notch grid (3.5 s would fall between notches).
  ir: { default: 3_600_000, min: 200_000, max: 10_000_000, scale: "linear" },
  rgbw: { default: 100_000, min: 10_000, max: 500_000, scale: "log" },
};

export interface DeviceSettings {
  camera: CameraSettings;
  leds: LedSettings;
  ir: IrSettings;
  /** Illumination source for dark/baseline/night captures — applies to every
   * imaging mode and every next experiment, not a per-experiment choice. */
  photoIlluminationSource: PhotoIlluminationSource;
}

// ---------------------------------------------------------------------------
// Remote CIFS/SMB sync (Settings -> General -> Remote Sync).
// Mirrors RemoteSyncStatus / RemoteSyncUpdate in back/rapidboxes/models.py.
//
// SECURITY: there is deliberately no `password` field on RemoteSyncStatus. The
// backend never returns it, and the UI must never try to display or infer it —
// including its length. `passwordSet` is all there is.
// ---------------------------------------------------------------------------

/** Pre-filled default, from the institutional share the legacy script mounted. */
export const DEFAULT_REMOTE_SERVER = "//ds.asuch.cas.cz/ueb/lhr";

export interface RemoteSyncStatus {
  enabled: boolean;
  /** //host/share[/path] — strictly validated server-side before it can reach mount. */
  server: string;
  /** The CIFS/SMB account used to mount the share. */
  username: string;
  /** Whether a password is held in the backend's memory. Never the password itself. */
  passwordSet: boolean;
  mounted: boolean;
  /**
   * Switched on, but the in-memory password is gone — i.e. the backend has
   * restarted (reboot, power blip, or the monthly OTA update). Sync can do
   * nothing at all in this state, so the UI must say so loudly rather than
   * showing an "on" toggle that silently copies nothing.
   */
  credentialsRequired: boolean;
  /** Destination subfolder on the share; sync stops if the researcher changes. */
  researcher: string;
  remotePath: string | null;
  /** Images captured but not yet copied (queued + failed-and-awaiting-retry). */
  pendingCount: number;
  lastSyncAt: string | null; // ISO 8601
  lastResult: "ok" | "error" | null;
  lastError: string | null;
  bulkInProgress: boolean;
  bulkMessage: string | null;
  /** True on a dev laptop: no real CIFS mount is attempted. */
  simulation: boolean;
}

/** PUT body. Every field optional so the UI can patch one thing at a time.
 *  `password` is write-only — it is never returned by any endpoint. */
export interface RemoteSyncUpdate {
  enabled?: boolean;
  server?: string;
  username?: string;
  password?: string;
  researcher?: string;
}

export interface RemoteSyncCheckResult {
  ok: boolean;
  message: string;
  status: RemoteSyncStatus;
}

/** The saved/loaded per-experiment <name>.xml: phases + light + illumination + camera, no identity fields. */
export interface SavedExperimentConfig {
  protocol: "tropism" | "growth";
  darkPhaseEnabled: boolean;
  darkPhaseHours: number;
  lateralIlluminationHours: number;
  spectra: Spectrum[];
  intervalMinutes: number;
  intensity: number;
  dayLengthHours: number;
  experimentLengthDays: number;
  dayIntensity: number;
  photoIlluminationSource: PhotoIlluminationSource;
  leds: LedSettings;
  ir: IrSettings;
  camera: CameraSettings;
}
