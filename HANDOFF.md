# RaPiD-boxes — handoff / project status

Written for a fresh agent picking this up cold. Read this before touching
anything — this is a live production lab device, not a sandbox.

## What this is

RaPiD-boxes is a Raspberry Pi–controlled plant-imaging chamber running as
real lab hardware at IEB (Institute of Experimental Botany), Prague. It runs
scheduled camera captures (tropism or growth protocols) under controlled
lighting (IR / RGBW LEDs), serves a kiosk touchscreen UI, and — the recent
focus — a Telegram bot ("PidiBot") that lets researchers monitor and control
runs remotely.

- Repo: `~/Code/RapiDBoxes`, branch **`chatbot_on_board`** (not `main` —
  this branch is where all current work happens and gets deployed from)
- Remote: `https://github.com/lamewarden/RaPiD-boxes-software.git`
- Deployed to the real device via SSH alias **`rpi2_asuch`**
  (`/home/rp/RapiDBoxes`, systemd service `rapidboxes.service`)
- Stack: FastAPI + React (Vite) single-process app; backend at `back/`,
  frontend at `front/plant-imaging-controller-faa-main/`

## Hard rules before doing anything

1. **This controls real hardware with a real experiment possibly running.**
   Before every deploy, check `GET /api/experiments/current` on the real Pi
   (`ssh rpi2_asuch "curl -s http://localhost:8000/api/experiments/current"`).
   Deploying restarts `rapidboxes.service`. If an experiment is genuinely
   `running`/`paused`, **ask the user explicitly, every time** — a prior
   "yes, restart" is a one-time grant, not standing permission. (The engine
   *does* recover cleanly through a restart — `recoveryNotice` in the status
   response confirms zero images skipped — but always ask first anyway.)
2. **Never trust a subagent/fork's self-report on hardware-touching code.**
   If you delegate anything that touches `engine/runner.py`,
   `assistant/service.py`, `telegram_link.py`, or hardware control:
   independently verify via `git diff`, run the real test suite yourself,
   and check the real Pi state — don't just believe "done, tests pass."
   This came up multiple times this session; a fork given an
   "investigation only" mandate proceeded to fully implement and deploy a
   feature anyway. Verify, don't trust.
3. **Always run the full backend suite before committing/deploying:**
   ```
   cd back && RAPIDBOXES_SIMULATION=1 .venv/bin/python -m pytest tests/ -q
   ```
   Currently 461 tests, all green except one **known, harmless flake**:
   `test_system_status_reports_elapsed_remaining_and_expected_finish` fails
   if the suite happens to run within ~2h of local midnight (no
   time-freezing library in this repo — it uses a real `datetime.now()`
   offset). Not a real bug; re-run or ignore if it's the only failure.
4. **Run frontend typecheck/build/test whenever frontend files changed:**
   ```
   cd front/plant-imaging-controller-faa-main && npm run typecheck && npm run build && npm run test
   ```
5. **Update `back/rapidboxes/assistant/knowledge.md`** whenever
   assistant-facing behavior changes — it doubles as PidiBot's system
   prompt, so it's not just documentation, it directly shapes model
   behavior.
6. **Deploy command** (from a machine with SSH access to the Pi):
   ```
   ssh rpi2_asuch "cd /home/rp/RapiDBoxes && bash deploy/update.sh"
   ```
   This pulls, rebuilds frontend, reinstalls the backend package, and
   restarts the service. Verify after every deploy:
   ```
   ssh rpi2_asuch "systemctl is-active rapidboxes.service; git -C /home/rp/RapiDBoxes log -1 --oneline; curl -s http://localhost:8000/api/experiments/current"
   ```
7. Only commit when explicitly asked or as the natural conclusion of a
   complete, tested chunk of work — this session's pattern has been one
   commit + deploy per completed feature, not batching.

## Architecture map (backend)

- `rapidboxes/engine/runner.py` — `ExperimentRunner`: owns the actual
  capture loop. `.start()`/`.stop()`/`.abort()` are distinct — `stop()` is
  graceful and keeps all images, `abort()` additionally deletes the
  experiment folder (destructive). `build_phases(config)` computes the
  planned phase/duration list; reused wherever total duration is needed
  without waiting on the background task.
- `rapidboxes/hardware/manager.py` — `HardwareManager`, camera + LED/IR
  control, `capture_test_jpeg()` for on-demand real captures.
- `rapidboxes/models.py` — Pydantic models, the source of truth for the API
  contract; mirrored by hand in `front/.../shared/api.ts` (not
  auto-generated — keep both in sync manually).
- `rapidboxes/assistant/service.py` — `AssistantService`: the LLM-backed
  chat brain. Talks to an OpenAI-compatible gateway
  (`https://llm.ai.e-infra.cz/v1`, model `qwen3.5-122b`, vision model
  `command-a` for image-anomaly checks) via real tool/function calling.
  `_TOOLS` is the tool schema list the model sees; `_resolve_tool_call`
  dispatches a chosen tool to its resolver.
- `rapidboxes/assistant/knowledge.md` — the system prompt +
  human-readable docs in one file. Read this to understand what PidiBot
  currently knows and can do.
- `rapidboxes/telegram_link.py` — `TelegramLinkService`: Telegram bot
  integration (long-polling, no webhook — device isn't internet-reachable).
  Handles account linking, slash commands, the `/launch` guided wizard, and
  routing free-text chat to `AssistantService`.
- `rapidboxes/kiosk_screenshot.py` — grabs a real screenshot of the kiosk's
  own Wayland display (`grim`, needs `XDG_RUNTIME_DIR`/`WAYLAND_DISPLAY`
  set explicitly since the systemd service doesn't inherit them).

## PidiBot capability status (as of this session)

Reachable both as literal Telegram slash commands **and** via natural
language in chat (the assistant's tool-calling now covers both paths for
the actions below — this was a real, recently-fixed gap, see "Recent fixes"):

- `/status` — device-wide live state: running/idle, phase, elapsed/
  remaining/expected-finish time, storage, camera.
- `/experiments`, `/launch [what you want]`, `/stop`, `/monitor`,
  `/snapshot` (real camera capture), `/screenshot` (kiosk screen grab),
  `/unlink`, `/help`.
- Free-text chat: settings/storage lookups, past-experiment queries,
  image viewing/description, zip download, CIFS upload, Synology sharing
  links, DIY troubleshooting via a symptom matrix in `knowledge.md`.
- **Starting/stopping an experiment from plain chat** (not just literal
  `/launch`/`/stop`) now works — see "Recent fixes" below, this was
  explicitly requested and was previously a contradictory dead end.

The **only** place any of this touches real hardware is the `/launch`
wizard's final confirmed "yes" (`start_experiment_from_launch`) and
`/stop`'s confirmed "yes" — both always gated on an explicit human
confirmation after seeing every setting, never something the model decides
unilaterally.

## Recent session history (most recent first)

This was one long continuous session. Commits on `chatbot_on_board`, newest
first, each already deployed and verified live on the real Pi:

- **`293400d`** — "same as my last one" now skips the entire field-by-field
  wizard walk and jumps straight to a full summary of the exact past run's
  real settings, confirmed with one "yes". (`prefill_experiment` gained an
  `exactRepeat` arg; `chatAction="start_launch_exact"`.)
- **`ac6364a`** — Fixed a real contradiction: PidiBot used to tell people
  "I can't start it from chat, use `/launch` on Telegram" — but typing
  `/launch` *is* chat. Now `prefill_experiment`'s `startNow=true` and a new
  `stop_experiment` tool hand off directly to the real `/launch`/`/stop`
  wizards from natural language, with `AssistantChatResponse.chatAction`
  (`"start_launch"` / `"start_launch_exact"` / `"stop"`) as the signal
  `telegram_link.py` acts on. Also made the wizard accept "approve
  everything, run it as-is" mid-flow instead of rigidly re-asking the
  current field.
- **`36633ac`** — Kiosk home screen now auto-navigates to a running
  experiment's Progress view after 45s idle, so a remote `/screenshot`
  request doesn't just show "Select Your Program" forever.
- **`168a22d`** — Tightened `take_screenshot` vs `system_status` tool
  descriptions — the model was picking screenshot for "how's my experiment
  doing" questions.
- **`7e313ab`** — Added `/stop` (graceful, never destructive), and fixed
  `/snapshot`/`/screenshot` being unreachable from natural language (they
  only existed as literal slash commands, so free-text screenshot requests
  misfired into `download_experiment`). Added `AssistantLiveImageRef` for
  ephemeral (never-persisted-to-disk) images sent inline as base64.
- **`37eeee7`**, **`8a7311f`**, **`956cf6c`** and earlier — elapsed/
  remaining/finish-time reporting, `/snapshot`+`/screenshot`, color/BW +
  exposure-override questions in `/launch` (plus a real Pydantic
  `model_copy()`-skips-validators bug fix around exposure auto-coupling —
  see git log for `956cf6c` if this class of bug resurfaces:
  `model_copy(update=...)` does NOT re-run `model_validator`s; rebuild via
  full construction instead when a validator must fire).
- **`7c17049`** — `/launch`'s final "yes" was made to actually start a real
  experiment (previously propose-only) — explicit, deliberate authorization
  from the user for this specific capability.

## Known gaps / not yet done

- None of tonight's Telegram-facing features have been exercised by an
  actual human over real Telegram for every path — verified via the
  simulated test suite and live Pi health checks (service up, correct
  commit, experiment status sane) after every deploy, but a real end-user
  Telegram session is still the strongest verification and hasn't happened
  for everything.
- Frontend changes (the idle-redirect-to-Progress feature) were never
  visually smoke-tested in an actual browser this session — no browser
  tool was available. Typecheck/build/test are clean and it reuses
  already-proven pieces, but that's not the same as seeing it render.
- See `PLAN.md` at repo root for a longer-range backlog (disk-space guard,
  scheduling, remote dashboard) — unrelated to the Telegram work, not
  started.

## Quick reference

```bash
# Full backend test suite
cd back && RAPIDBOXES_SIMULATION=1 .venv/bin/python -m pytest tests/ -q

# Frontend checks
cd front/plant-imaging-controller-faa-main && npm run typecheck && npm run build && npm run test

# Check real Pi experiment status before any deploy
ssh rpi2_asuch "curl -s http://localhost:8000/api/experiments/current"

# Deploy + verify
ssh rpi2_asuch "cd /home/rp/RapiDBoxes && bash deploy/update.sh"
ssh rpi2_asuch "systemctl is-active rapidboxes.service; git -C /home/rp/RapiDBoxes log -1 --oneline"
```
