/**
 * Mapping between exposure slider travel and exposure time.
 *
 * The exposure a frame needs follows the light that lights it, so the slider
 * reshapes itself around the selected illumination source rather than offering
 * one range that suits neither:
 *
 *   IR    linear 1–10s. A plain sweep; anywhere in the range is plausible.
 *   RGBW  logarithmic 0.01–0.5s. The useful values bunch at the short end, so
 *         a linear track would cram them into its first fifth. On the log
 *         track 0.01–0.1s takes ~59% of the travel and 0.1–0.5s the rest.
 *
 * The slider always works in position space (0…STEPS) and converts, which also
 * keeps the filled-track gradient in ParameterControl linear in travel.
 */
import { EXPOSURE_PROFILES, type PhotoIlluminationSource } from "@shared/api";

export const EXPOSURE_SLIDER_STEPS = 1000;

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

/** Slider position (0…STEPS) -> exposure in microseconds. */
export function positionToExposure(source: PhotoIlluminationSource, position: number): number {
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
  return positionToExposure(source, exposureToPosition(source, microseconds) + steps);
}

/** Short human label: "3.5 s" for the IR range, "85 ms" for the RGBW range. */
export function formatExposure(microseconds: number): string {
  return microseconds >= 1_000_000
    ? `${(microseconds / 1_000_000).toFixed(2).replace(/\.?0+$/, "")} s`
    : `${Math.round(microseconds / 1000)} ms`;
}
