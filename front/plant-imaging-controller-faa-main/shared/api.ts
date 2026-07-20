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
}

export interface CameraSettings {
  width: number;
  height: number;
  exposureMicroseconds: number;
  iso: number;
  autofocusEnabled: boolean;
  focusDistance: number;
  grayscale: boolean;
  awbRedGain: number;
  awbBlueGain: number;
  jpegQuality: number;
  settleSeconds: number;
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
   * compresses 0.2–0.5s. IR is a plain linear 1–10s sweep.
   */
  scale: "linear" | "log";
}

/**
 * Exposure travels with the illumination source: IR needs a long integration,
 * the RGBW flash is bright. Mirrors EXPOSURE_PROFILES in
 * back/rapidboxes/models.py, which enforces default/min/max server-side.
 */
export const EXPOSURE_PROFILES: Record<PhotoIlluminationSource, ExposureProfile> = {
  ir: { default: 3_500_000, min: 1_000_000, max: 10_000_000, scale: "linear" },
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
