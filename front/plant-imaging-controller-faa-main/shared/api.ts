/**
 * Shared API contract — mirrors the pydantic models in
 * back/rapidboxes/models.py. Keep the two in sync.
 */

export type Spectrum = "white" | "red" | "green" | "blue";

export interface TropismConfig {
  experimentName: string;
  username: string;
  preIlluminationEnabled?: boolean;
  preIlluminationHours?: number;
  darkPhaseEnabled: boolean;
  darkPhaseHours: number;
  lateralIlluminationHours: number;
  spectra: Spectrum[];
  intervalMinutes: number;
  intensity: number;
}

export type ExperimentState =
  | "idle"
  | "running"
  | "paused"
  | "finishing"
  | "done"
  | "error";

export type ExperimentPhase = "pre_illumination" | "dark" | "bending";

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
  config: TropismConfig | null;
}

export interface StartResponse {
  status: "started" | "busy" | "no_camera";
  experimentId: string | null;
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
  diskFreeBytes: number;
  diskTotalBytes: number;
  cameraAvailable: boolean;
}

export interface CameraSettings {
  width: number;
  height: number;
  exposureMicroseconds: number;
  iso: number;
  grayscale: boolean;
  awbRedGain: number;
  awbBlueGain: number;
  jpegQuality: number;
  settleSeconds: number;
}

export interface DeviceSettings {
  camera: CameraSettings;
  leds: {
    pixelCount: number;
    pixelOrder: string;
    topSegment: [number, number];
    lateralSegment: [number, number];
    spiHz: number;
  };
  ir: { pins: number[] };
}
