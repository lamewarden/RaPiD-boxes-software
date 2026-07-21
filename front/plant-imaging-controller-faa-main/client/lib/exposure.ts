/**
 * Mapping between exposure slider travel and exposure time.
 *
 * The exposure a frame needs follows the light that lights it, so the slider
 * reshapes itself around the selected illumination source rather than offering
 * one range that suits neither:
 *
 *   IR    discrete 0.2 s notches from 0.2–10 s (50 stops). 0.2 s is preferred
 *         over 0.4 s because (10 − 0.2) / 0.2 is an integer, so both ends of
 *         the range land on the grid with a single uniform step size.
 *   RGBW  logarithmic 0.01–0.5s. The useful values bunch at the short end, so
 *         a linear track would cram them into its first fifth. On the log
 *         track 0.01–0.1s takes ~59% of the travel and 0.1–0.5s the rest.
 *
 * The slider always works in position space (0…STEPS) and converts, which also
 * keeps the filled-track gradient in ParameterControl linear in travel.
 */
import { EXPOSURE_PROFILES, type PhotoIlluminationSource } from "@shared/api";

export const EXPOSURE_SLIDER_STEPS = 1000;

/** IR exposure quantum: 0.2 s. Yields 0.2, 0.4, …, 10.0 s (50 notches). */
const IR_STEP_US = 200_000;
const IR_NOTCH_COUNT =
  Math.round((EXPOSURE_PROFILES.ir.max - EXPOSURE_PROFILES.ir.min) / IR_STEP_US) + 1;

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

function irNotchUs(index: number): number {
  const { min, max } = EXPOSURE_PROFILES.ir;
  return clamp(min + index * IR_STEP_US, min, max);
}

function nearestIrNotchIndex(microseconds: number): number {
  const { min, max } = EXPOSURE_PROFILES.ir;
  const us = clamp(microseconds, min, max);
  return clamp(Math.round((us - min) / IR_STEP_US), 0, IR_NOTCH_COUNT - 1);
}

function irPositionToExposure(position: number): number {
  const t = clamp(position, 0, EXPOSURE_SLIDER_STEPS) / EXPOSURE_SLIDER_STEPS;
  const idx = Math.round(t * (IR_NOTCH_COUNT - 1));
  return irNotchUs(idx);
}

function irExposureToPosition(microseconds: number): number {
  const idx = nearestIrNotchIndex(microseconds);
  return Math.round((idx / (IR_NOTCH_COUNT - 1)) * EXPOSURE_SLIDER_STEPS);
}

/** Slider position (0…STEPS) -> exposure in microseconds. */
export function positionToExposure(source: PhotoIlluminationSource, position: number): number {
  if (source === "ir") {
    return irPositionToExposure(position);
  }
  const { min, max, scale } = EXPOSURE_PROFILES[source];
  const t = clamp(position, 0, EXPOSURE_SLIDER_STEPS) / EXPOSURE_SLIDER_STEPS;
  const value = scale === "log" ? min * Math.pow(max / min, t) : min + (max - min) * t;
  return Math.round(clamp(value, min, max));
}

/** Exposure in microseconds -> slider position (0…STEPS). Inverse of the above. */
export function exposureToPosition(
  source: PhotoIlluminationSource,
  microseconds: number,
): number {
  if (source === "ir") {
    return irExposureToPosition(microseconds);
  }
  const { min, max, scale } = EXPOSURE_PROFILES[source];
  const us = clamp(microseconds, min, max);
  const t =
    scale === "log" ? Math.log(us / min) / Math.log(max / min) : (us - min) / (max - min);
  return Math.round(t * EXPOSURE_SLIDER_STEPS);
}

/** Nudge the exposure by one slider step, so +/- feels even across the curve. */
export function stepExposure(
  source: PhotoIlluminationSource,
  microseconds: number,
  steps: number,
): number {
  if (source === "ir" && steps !== 0) {
    const dir = steps > 0 ? 1 : -1;
    return irNotchUs(nearestIrNotchIndex(microseconds) + dir);
  }
  return positionToExposure(source, exposureToPosition(source, microseconds) + steps);
}

/** Short human label: "3.6 s" / "0.2 s" for IR-scale values, "85 ms" for short RGBW. */
export function formatExposure(microseconds: number): string {
  if (microseconds >= 1_000_000) {
    return `${(microseconds / 1_000_000).toFixed(2).replace(/\.?0+$/, "")} s`;
  }
  if (microseconds >= 200_000) {
    return `${(microseconds / 1_000_000).toFixed(1)} s`;
  }
  return `${Math.round(microseconds / 1000)} ms`;
}
