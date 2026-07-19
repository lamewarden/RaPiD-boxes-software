# RaPiD-boxes — Forward Plan

Backlog of larger features to build after the current illumination-settings
refactor. Not yet started; ordered roughly by how self-contained each one is.

## 1. Disk-space guard / auto-cleanup

A Growth run (14 days × 30 min interval, full-res) can fill the SD card. Today
`GeneralSettingsMenu` just shows a "Low" disk badge under 2 GB free — nothing
stops a run from starting or continuing once space runs out.

- Before `POST /api/experiments`: estimate bytes needed (planned image count ×
  last-known average JPEG size) and refuse to start (`no_space` status,
  mirroring today's `no_camera`) if it won't fit with headroom.
- During a run: `ExperimentRunner` checks free space each capture cycle;
  auto-pauses (not stops — an operator may free space and resume) and
  broadcasts a status message when free space drops under a threshold.
- Optional: a "delete oldest experiment(s)" action from `GeneralSettingsMenu`'s
  disk-space card, guarded by a confirmation (this is a destructive action).
- Threshold should be a device setting, not hardcoded (`LOW_DISK_BYTES` today
  is a frontend-only const).

## 2. Experiment scheduling

Let an operator queue a run instead of only "start now".

- New `scheduledStartAt: datetime | null` on the start request; `ExperimentRunner`
  (or a small new `Scheduler` beside it) holds a pending config and starts it
  when the clock/`PausableClock`-equivalent reaches the target time.
- UI: a "Start at..." option next to the existing "Start Experiment" button on
  both program screens, using the existing `OnScreenKeyboard`-style modal
  pattern for time entry.
- Consider: chaining — start Config B automatically when Config A's run
  reaches `done`, for back-to-back protocols without an operator present.

## 3. Remote monitoring dashboard

The backend already binds `0.0.0.0:8000`, so anything on the same network can
reach the API. There's no page other than the kiosk SPA itself.

- Lightest version: a read-only route (e.g. `/status` or a separate small
  bundle) showing current `ExperimentStatus` (via the existing `/api/ws`
  feed), the latest captured image, and a link into `/api/images` — no touch
  controls, so it's safe to leave reachable.
- Reuses existing endpoints entirely; no new backend surface except maybe a
  trimmed static page. Could literally be the existing Progress screens
  rendered read-only if kiosk-only actions (pause/resume/stop) are hidden
  behind a `readOnly` prop.
- Out of scope for v1: auth. Fine on a trusted local network; revisit if this
  ever needs to be reachable beyond that.

---
*Not yet scheduled: live plant-motion analysis (frame-diff / tip-tracking) —
larger, separate effort, discussed but deliberately left out of this plan.*
