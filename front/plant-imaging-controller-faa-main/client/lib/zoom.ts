/**
 * Zoom slider: continuous 1x-5x with magnetic snap at each integer.
 *
 * The underlying <input type=range> reports fine-grained raw values (step
 * ZOOM_STEP); applyZoomStickiness rounds anything within ZOOM_STICKY_RADIUS of
 * an integer to that integer, and otherwise rounds to one decimal place. The
 * result: dragging near 3x locks the thumb to exactly 3x, but there's room to
 * land on 3.2x or 4.5x once you pull far enough from the nearest integer.
 */
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 5;
export const ZOOM_STEP = 0.02;
const ZOOM_STICKY_RADIUS = 0.12;

export function applyZoomStickiness(raw: number): number {
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, raw));
  const nearestInt = Math.round(clamped);
  if (Math.abs(clamped - nearestInt) <= ZOOM_STICKY_RADIUS) {
    return nearestInt;
  }
  return Math.round(clamped * 10) / 10;
}

/** "1x" for a whole number, "3.2x" otherwise. */
export function formatZoom(zoom: number): string {
  return Number.isInteger(zoom) ? `${zoom}x` : `${zoom.toFixed(1)}x`;
}
