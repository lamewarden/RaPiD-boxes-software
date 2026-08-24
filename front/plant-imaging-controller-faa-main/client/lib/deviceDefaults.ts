import {
  EXPOSURE_PROFILES,
  type CameraSettings,
  type DeviceSettings,
  type LedSettings,
  type PhotoIlluminationSource,
} from "@shared/api";

/**
 * The fixed "system default" device settings. Mirrors the pydantic field
 * defaults in back/rapidboxes/models.py (CameraSettings / LedSettings /
 * IrSettings / DeviceSettings) — keep the two in sync. Nothing in the app
 * can overwrite this; it's what the Settings menu's "Default" button resets
 * to, and what the backend resets the active session's camera half to at
 * every process start (see settings_store.load_device_settings_for_new_session).
 *
 * Distinct from a user's own saved baseline ("Mine", saved/restored
 * via api.saveMyDefaults / api.myDefaults) — that one is optional, per
 * username, covers camera + illumination as one bundle, and persists across
 * restarts.
 */
export const DEFAULT_SOURCE: PhotoIlluminationSource = "ir";

export const DEFAULT_CAMERA: CameraSettings = {
  width: 2304,
  height: 1296,
  exposureMicroseconds: EXPOSURE_PROFILES[DEFAULT_SOURCE].default,
  iso: 100,
  autofocusEnabled: false,
  // LensPosition is in diopters (1/metres): 10.0 focuses at 1/10 m = 10 cm.
  focusDistance: 10.0,
  grayscale: true,
  zoom: 1.0,
};

export const DEFAULT_LEDS: LedSettings = {
  pixelCount: 70,
  pixelOrder: "GRBW",
  topSegment: [22, 64],
  lateralSegment: [0, 21],
  spiHz: 6_400_000,
  stride: 1,
};

export const DEFAULT_IR_PINS: [number, number] = [26, 23];

export const DEFAULT_DEVICE_SETTINGS: DeviceSettings = {
  camera: DEFAULT_CAMERA,
  leds: DEFAULT_LEDS,
  ir: { pins: DEFAULT_IR_PINS },
  photoIlluminationSource: DEFAULT_SOURCE,
};
