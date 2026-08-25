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
  /** Opt-in mid-run issue alerting (mold/anomaly detection), delivered over
   *  Telegram -- see back/rapidboxes/telegram_link.py. Requires the
   *  requesting user to already have a linked Telegram chat (checked
   *  server-side at start); no contact field lives on this config at all. */
  reportOnIssueEnabled: boolean;
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
  reportOnIssueEnabled: boolean;
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

export interface StorageNotice {
  kind: "expiring" | "info";
  message: string;
  experiments: Array<{ id: string; name: string | null; daysRemaining: number }>;
}

/** Shown once after the app resumes a run interrupted by a crash, power
 *  loss, or reboot -- see back/rapidboxes/engine/runner.py's recover(). */
export interface RecoveryNotice {
  kind: "recovered";
  message: string;
  offlineSeconds: number;
  imagesSkipped: number;
}

/** One entry in ExperimentStatus.phases -- the full planned sequence for this
 *  run, computed once at start. See back/rapidboxes/engine/runner.py
 *  phase_infos_for(). */
export interface PhaseInfo {
  name: ExperimentPhase;
  durationSeconds: number;
  capture: boolean;
  dayIndex: number | null;
  imagesPlanned: number;
}

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
  storageNotice: StorageNotice | null;
  recoveryNotice: RecoveryNotice | null;
  phases: PhaseInfo[];
  /** Index into `phases`; null on the growth baseline (a one-off capture,
   *  not part of the phase list) or once the run is no longer active. */
  currentPhaseIndex: number | null;
  bytesUsed: number;
  estimatedTotalBytes: number | null;
  /** Set once MoldWatchService confirms a mid-run anomaly for a user who
   *  opted into reportOnIssueEnabled -- see mark_issue_detected(). */
  issueDetected: boolean;
  issueDetail: string | null;
}

export interface StorageSuggestion {
  experimentIds: string[];
  count: number;
  freedBytes: number;
}

export interface StartResponse {
  status: "started" | "busy" | "no_camera" | "low_space";
  experimentId: string | null;
  estimatedBytes?: number | null;
  availableBytes?: number | null;
  suggestion?: StorageSuggestion | null;
}

export interface FreeSpaceResponse {
  deletedIds: string[];
  freedBytes: number;
  availableBytes: number;
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

export interface PlantMaskStatus {
  status: "idle" | "running" | "done" | "error";
  current: number;
  total: number;
  message: string;
  percent?: number;
  error?: string;
  maskUrl?: string;
  overlayUrl?: string;
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
  // Default 1.0 s sits on the 0.2 s IR notch grid.
  ir: { default: 1_000_000, min: 200_000, max: 10_000_000, scale: "linear" },
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

/** PUT /api/settings/mine body -- save the current camera + illumination
 *  settings as this user's personal baseline (shared across the Camera and
 *  Illumination tabs). See back/rapidboxes/user_defaults.py. */
export interface UserDefaultsUpdate {
  username: string;
  settings: DeviceSettings;
}

/** One entry in GET /api/users -- a username this device has seen, with how
 *  many experiments are attributed to it. Matched case-insensitively, so
 *  "Ivan"/"IVAN"/"ivan" fold into one lower-cased entry. */
export interface UserSummary {
  username: string;
  experimentCount: number;
  bytesUsed: number;
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

// ---------------------------------------------------------------------------
// Synology DSM sharing links (Settings -> General -> Sharing Links).
// Mirrors DsmSharingStatus / DsmSharingUpdate in back/rapidboxes/models.py.
// A DIFFERENT NAS/account than Remote Sync above -- see
// back/rapidboxes/dsm_sharing.py's module docstring for why. Same
// session-only password precedent: never returned by any endpoint.
// ---------------------------------------------------------------------------

export interface DsmSharingStatus {
  enabled: boolean;
  host: string;
  port: number;
  username: string;
  /** DSM-internal path, e.g. "/volume1/ueb-if" -- NOT a CIFS UNC path. */
  shareRoot: string;
  passwordSet: boolean;
  credentialsRequired: boolean;
  lastResult: "ok" | "error" | null;
  lastError: string | null;
}

export interface DsmSharingUpdate {
  enabled?: boolean;
  host?: string;
  port?: number;
  username?: string;
  password?: string;
  shareRoot?: string;
}

export interface DsmSharingCheckResult {
  ok: boolean;
  message: string;
  status: DsmSharingStatus;
}

// --- Telegram issue-alert linking (see rapidboxes/telegram_link.py) -------

export interface TelegramStatus {
  /** False if no admin has set a bot token/username yet -- the whole
   *  feature is unavailable, distinct from "configured but not linked". */
  configured: boolean;
  linked: boolean;
  botUsername: string | null;
}

export interface TelegramLinkCode {
  code: string;
  botUsername: string;
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
  /** Whether issue alerting was on for this run -- replayed on Import,
   *  unlike the Telegram link itself, which this snapshot never carries
   *  (see back/rapidboxes/models.py's TropismConfig docstring). */
  reportOnIssueEnabled: boolean;
}

// --- QA chat assistant (see rapidboxes/assistant/service.py) --------------

export interface AssistantMessage {
  role: "user" | "assistant";
  content: string;
  /** Frontend-only: attached to a stored message so a shown image/download
   *  link survives chat history persistence (see lib/assistantHistory.ts)
   *  and a reload, not just the live response that produced it. Harmless if
   *  echoed back in `history` -- the backend only ever reads `.content`. */
  image?: AssistantImageRef | null;
  download?: AssistantDownloadRef | null;
}

/** One real, already-captured image resolved by the show_image tool --
 *  never invented, always a file that already exists (see
 *  ExperimentProposal for the same never-invent principle). */
export interface AssistantImageRef {
  experimentId: string;
  imageId: string;
  url: string;
  thumbUrl: string;
  caption: string;
}

/** One real experiment folder -- or a specific range/count of its images --
 *  resolved by the download_experiment tool, packaged as a zip -- never
 *  invented (see AssistantImageRef). `url` is the same GET
 *  /api/experiments/{id}/download endpoint Gallery's own download button
 *  uses, with an `?images=` query string already baked in when imageIds is
 *  set. `imageIds` is null for "the whole experiment" (the default). */
export interface AssistantDownloadRef {
  experimentId: string;
  url: string;
  filename: string;
  sizeBytes: number;
  imageIds: string[] | null;
}

/** A concrete, ready-to-review config resolved from one specific past
 *  experiment's own saved settings -- never invented by the model. Only ever
 *  used to pre-fill the setup screen (same mechanism as Import); a human
 *  still has to press the real Start button. */
export interface ExperimentProposal {
  experimentId: string;
  protocol: "tropism" | "growth";
  sourceUsername: string;
  summary: string;
  config: SavedExperimentConfig;
}

export interface AssistantChatResponse {
  reply: string;
  proposal: ExperimentProposal | null;
  image: AssistantImageRef | null;
  download: AssistantDownloadRef | null;
}

/** AI-generated end-of-run summary (rapidboxes/assistant/summary.py),
 *  written asynchronously once an experiment finishes. Mold detection uses
 *  a >=3-frame confirmation rule -- see moldFrameCount vs framesChecked. */
export interface ExperimentAiSummary {
  generatedAt: string;
  ranSmoothly: boolean;
  textSummary: string;
  moldDetected: boolean;
  moldFrameCount: number;
  imageCheckSummary: string | null;
  framesChecked: number;
}
