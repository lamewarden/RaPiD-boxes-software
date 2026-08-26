"""Pydantic models: the source of truth for the API contract.

These are mirrored as TypeScript interfaces in front/shared/api.ts.
"""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Experiment configuration (request body for POST /api/experiments)
# ---------------------------------------------------------------------------

Spectrum = str  # one of: "white" | "red" | "green" | "blue"
VALID_SPECTRA = ("white", "red", "green", "blue")

# Illumination source for any capture taken in darkness (Tropism dark phase,
# Growth baseline, Growth night) — a device setting, not a per-experiment one.
PhotoIlluminationSource = Literal["ir", "rgbw"]

# Fixed photo-flash intensity for RGBW-lit dark/baseline/night captures.
PHOTO_FLASH_INTENSITY = 10

# Exposure is a function of how the frame is lit, so it travels with the
# illumination source rather than being tuned independently: the IR boards need
# a long integration, while the RGBW flash is comparatively bright. Each source
# carries the range the UI offers and the value settings snap to when that
# source is selected. Keep in sync with EXPOSURE_PROFILES in front/shared/api.ts
# (which adds the slider scale — a UI-only concern).
EXPOSURE_PROFILES = {
    # IR UI: discrete 0.2 s notches from 0.2–10 s (see front exposure.ts).
    # Default 1.0 s sits on that grid.
    "ir": {"default": 1_000_000, "min": 200_000, "max": 10_000_000},
    "rgbw": {"default": 100_000, "min": 10_000, "max": 500_000},
}


def exposure_for_source(source: str, current: Optional[int] = None) -> int:
    """The exposure to use for `source`, keeping `current` if it already suits.

    Out-of-range values snap to the source's default, so switching illumination
    can never leave an exposure that blacks out (IR at flash speed) or blows out
    (RGBW at IR speed) every capture.
    """
    profile = EXPOSURE_PROFILES.get(source, EXPOSURE_PROFILES["ir"])
    if current is not None and profile["min"] <= current <= profile["max"]:
        return current
    return profile["default"]


class TropismConfig(BaseModel):
    """The Tropism protocol as exposed by the React UI.

    Maps to the legacy 3-stage protocol:
            dark "apical hook" -> "bending".
    """

    protocol: Literal["tropism"] = "tropism"
    experimentName: str = Field(default="experiment", min_length=1, max_length=80)
    username: str = Field(default="pi", min_length=1, max_length=40)

    # Dark "apical hook" phase: IR-lit captures in darkness.
    darkPhaseEnabled: bool = True
    darkPhaseHours: float = Field(default=90.0, ge=0, le=350)

    # Phototropic "bending" phase: unilateral coloured light between captures.
    lateralIlluminationHours: float = Field(default=20.0, ge=0, le=168)
    spectra: List[Spectrum] = Field(default_factory=lambda: ["white"])

    # Imaging cadence and brightness, shared by both imaging phases.
    intervalMinutes: float = Field(default=20.0, ge=1, le=240)
    intensity: int = Field(default=25, ge=0, le=100)

    # Opt-in issue alerting (mold/anomaly detection during the run), delivered
    # over Telegram -- see telegram_link.py. Requires the requesting user to
    # already have a linked Telegram chat (checked server-side at start, not
    # here: this model has no access to the link store); no address/contact
    # field lives on the config at all, unlike an email-based design, so
    # there is nothing here to leak into the per-experiment XML.
    reportOnIssueEnabled: bool = False

    @model_validator(mode="after")
    def _check(self) -> "TropismConfig":
        bad = [s for s in self.spectra if s not in VALID_SPECTRA]
        if bad:
            raise ValueError(f"invalid spectra {bad}; allowed: {VALID_SPECTRA}")
        dark = self.darkPhaseEnabled and self.darkPhaseHours > 0
        bending = self.lateralIlluminationHours > 0
        if not dark and not bending:
            raise ValueError("at least one imaging phase (dark or bending) must be > 0h")
        if bending and not self.spectra:
            raise ValueError("bending phase needs at least one spectrum colour")
        return self


class GrowthConfig(BaseModel):
    """The Growth (day/night photoperiod) protocol.

    One baseline photo -> repeating top-down day/night cycle for
    `experimentLengthDays` days. Always uses the top LED segment; never the
    lateral/side segment (that's reserved for Tropism's unilateral bending).
    """

    protocol: Literal["growth"] = "growth"
    experimentName: str = Field(default="experiment", min_length=1, max_length=80)
    username: str = Field(default="pi", min_length=1, max_length=40)

    # Day/night photoperiod cycle.
    dayLengthHours: int = Field(default=16, ge=0, le=24)
    experimentLengthDays: int = Field(default=14, ge=1, le=30)
    spectra: List[Spectrum] = Field(default_factory=lambda: ["white"])
    dayIntensity: int = Field(default=25, ge=0, le=100)

    # Imaging cadence, uniform across day and night.
    intervalMinutes: float = Field(default=30.0, ge=1, le=240)

    # Opt-in issue alerting, delivered over Telegram -- see TropismConfig for
    # why this isn't mirrored into SavedExperimentConfig.
    reportOnIssueEnabled: bool = False

    @model_validator(mode="after")
    def _check(self) -> "GrowthConfig":
        bad = [s for s in self.spectra if s not in VALID_SPECTRA]
        if bad:
            raise ValueError(f"invalid spectra {bad}; allowed: {VALID_SPECTRA}")
        if not self.spectra:
            raise ValueError("day phase needs at least one spectrum colour")
        return self


ExperimentConfig = Annotated[Union[TropismConfig, GrowthConfig], Field(discriminator="protocol")]


# ---------------------------------------------------------------------------
# Experiment runtime status (GET /api/experiments/current and WS payload)
# ---------------------------------------------------------------------------


class ExperimentState(str, Enum):
    idle = "idle"
    running = "running"
    paused = "paused"
    finishing = "finishing"
    done = "done"
    error = "error"


class ExperimentPhase(str, Enum):
    dark = "dark"
    bending = "bending"
    baseline = "baseline"
    day = "day"
    night = "night"


class StorageNotice(BaseModel):
    """Retention-policy reminder shown on the experiment progress screen."""

    kind: Literal["expiring", "info"]
    message: str
    experiments: List[dict] = Field(default_factory=list)


class RecoveryNotice(BaseModel):
    """Shown once after the app resumes a run that was interrupted by a
    crash, power loss, or reboot -- see ExperimentRunner.recover()."""

    kind: Literal["recovered"] = "recovered"
    message: str
    offlineSeconds: float
    imagesSkipped: int


class PhaseInfo(BaseModel):
    """One entry in ExperimentStatus.phases -- the full planned sequence for
    this run, computed once at start (see engine.runner.build_phases) so the
    UI can show "previous/current/next phase" and a phase-by-phase
    breakdown without duplicating the scheduling logic client-side."""

    name: ExperimentPhase
    durationSeconds: float
    capture: bool
    dayIndex: Optional[int] = None
    imagesPlanned: int


class ExperimentStatus(BaseModel):
    state: ExperimentState = ExperimentState.idle
    phase: Optional[ExperimentPhase] = None
    experimentId: Optional[str] = None
    experimentName: Optional[str] = None
    username: Optional[str] = None
    startedAt: Optional[datetime] = None
    elapsedSeconds: float = 0.0
    totalSeconds: float = 0.0
    phaseElapsedSeconds: float = 0.0
    phaseTotalSeconds: float = 0.0
    imagesCaptured: int = 0
    imagesPlanned: int = 0
    nextCaptureInSeconds: Optional[float] = None
    lastImageId: Optional[str] = None
    message: Optional[str] = None
    config: Optional[ExperimentConfig] = None
    dayIndex: Optional[int] = None
    totalDays: Optional[int] = None
    storageNotice: Optional[StorageNotice] = None
    recoveryNotice: Optional[RecoveryNotice] = None
    # Stamped on every metadata write; recover() diffs this against wall-clock
    # time on the next boot to work out how long the box was off.
    updatedAt: Optional[datetime] = None
    # The full planned phase sequence, and where we are in it -- None while on
    # the growth baseline (a one-off capture, not part of `phases`) or once
    # the run is no longer active.
    phases: List[PhaseInfo] = Field(default_factory=list)
    currentPhaseIndex: Optional[int] = None
    # Real bytes written so far (summed at each capture) vs. the same rough
    # worst-case estimate used for the pre-flight low-space check at start.
    bytesUsed: int = 0
    estimatedTotalBytes: Optional[int] = None
    # Set by MoldWatchService (assistant/mold_watch.py) once a confirmed
    # anomaly (>= MOLD_CONFIRM_THRESHOLD frames) is found mid-run for a user
    # who opted into reportOnIssueEnabled. Lives on live status (broadcast
    # over the WS, written through the normal metadata heartbeat) rather than
    # a separate out-of-band metadata.json patch, so there is no risk of a
    # race against the runner's own periodic full-status metadata writes.
    issueDetected: bool = False
    issueDetail: Optional[str] = None


# ---------------------------------------------------------------------------
# Device settings (GET/PUT /api/settings) — user-editable hardware defaults
# ---------------------------------------------------------------------------


class CameraSettings(BaseModel):
    width: int = Field(default=2304, ge=320, le=4608)   # v3 sensor half-res default
    height: int = Field(default=1296, ge=240, le=2592)
    exposureMicroseconds: int = Field(default=100_000, ge=100, le=10_000_000)
    iso: int = Field(default=100, ge=50, le=1600)
    autofocusEnabled: bool = False
    # LensPosition is in diopters (1/metres): 10.0 focuses at 1/10 m = 10 cm.
    focusDistance: float = Field(default=10.0, ge=0.0, le=32.0)
    grayscale: bool = True
    # Digital zoom: center-crop to 1/zoom of the frame, then scale back up to
    # width x height, so every image stays the configured size regardless of
    # framing. Applied to every capture -- experiment images and test photos
    # alike -- not just a preview convenience.
    zoom: float = Field(default=1.0, ge=1.0, le=5.0)


# AWB and per-shot settle time used to be user-tunable, but the sensor is
# accurate enough at a fixed white balance that tuning them never actually
# helped, and the manual settle slider is now unneeded now that settle is
# derived from exposure (see settle_seconds_for below). Fixed here instead of
# in CameraSettings so there is nothing left to (mis)configure.
AWB_RED_GAIN = 2.0
AWB_BLUE_GAIN = 1.0

SETTLE_SECONDS_MIN = 0.15
SETTLE_SECONDS_MAX = 2.0


def settle_seconds_for(exposure_microseconds: int) -> float:
    """Delay before a capture, so a just-changed exposure has settled.

    Scales with exposure, bounded at both ends: a flash-speed RGBW shot
    (10-500ms) settles almost immediately at the floor, while a multi-second
    IR exposure gets proportionally more margin for the sensor pipeline to
    flush the previous frame, capped so it never adds more than 2s.
    """
    return min(SETTLE_SECONDS_MAX, max(SETTLE_SECONDS_MIN, exposure_microseconds / 1_000_000))


class LedSettings(BaseModel):
    pixelCount: int = Field(default=70, ge=1, le=600)
    pixelOrder: str = "GRBW"
    # Inclusive-exclusive [start, end) segments on the strip.
    topSegment: Tuple[int, int] = (22, 64)
    lateralSegment: Tuple[int, int] = (0, 21)
    spiHz: int = Field(default=6_400_000, ge=2_000_000, le=10_000_000)
    # Fire every Nth pixel within a lit segment (1 = every pixel, 5 = every 5th).
    # Counted from the start of each segment; skipped pixels are driven off.
    stride: int = Field(default=1, ge=1, le=5)


class IrSettings(BaseModel):
    # BCM pins for the two IR boards (legacy: 26 & 23).
    pins: List[int] = Field(default_factory=lambda: [26, 23])


class DeviceSettings(BaseModel):
    camera: CameraSettings = Field(default_factory=CameraSettings)
    leds: LedSettings = Field(default_factory=LedSettings)
    ir: IrSettings = Field(default_factory=IrSettings)
    # Illumination source for dark/baseline/night captures, shared by both
    # protocols. Persisted like camera/leds/ir; applies to every next run.
    photoIlluminationSource: PhotoIlluminationSource = "ir"

    @model_validator(mode="after")
    def _couple_exposure_to_source(self) -> "DeviceSettings":
        """Keep the exposure in step with the illumination source.

        Exposure and light source are one decision, not two: IR needs seconds,
        the RGBW flash needs milliseconds. Enforcing it here means every route
        into the settings — the API, the settings file, a session reset — lands
        on a usable pairing, instead of each having to remember the rule.
        """
        wanted = exposure_for_source(
            self.photoIlluminationSource, self.camera.exposureMicroseconds
        )
        if wanted != self.camera.exposureMicroseconds:
            self.camera = self.camera.model_copy(update={"exposureMicroseconds": wanted})
        return self


class UserDefaultsUpdate(BaseModel):
    """PUT /api/settings/mine body: save the current camera + illumination
    settings as this user's personal baseline (Settings -> "Save Mine",
    shared across the Camera and Illumination tabs).

    Distinct from DeviceSettings' own field defaults (the fixed system
    default, which nothing in the app can overwrite) and from the active
    session's settings (the camera half is always reset to the system
    default on process start, see
    settings_store.load_device_settings_for_new_session) -- this one is
    keyed by username and persists across restarts until the user next saves
    over it."""

    username: str = Field(min_length=1, max_length=40)
    settings: DeviceSettings


class UserSummary(BaseModel):
    """One entry in GET /api/users -- a username this device has seen, with
    how many experiments are attributed to it. Usernames are matched
    case-insensitively (see api/users.py), so this count folds together any
    historical "Ivan"/"IVAN"/"ivan" variants under one lower-cased identity."""

    username: str
    experimentCount: int
    bytesUsed: int


# ---------------------------------------------------------------------------
# Saved/loaded experiment config (the per-experiment <name>.xml) — phases,
# light and camera, deliberately excluding identity fields (name/username).
# ---------------------------------------------------------------------------


class SavedExperimentConfig(BaseModel):
    protocol: Literal["tropism", "growth"] = "tropism"
    darkPhaseEnabled: bool = True
    darkPhaseHours: float = Field(default=90.0, ge=0, le=350)
    lateralIlluminationHours: float = Field(default=20.0, ge=0, le=168)
    spectra: List[Spectrum] = Field(default_factory=lambda: ["white"])
    intervalMinutes: float = Field(default=20.0, ge=1, le=240)
    intensity: int = Field(default=25, ge=0, le=100)
    dayLengthHours: int = Field(default=16, ge=0, le=24)
    experimentLengthDays: int = Field(default=14, ge=1, le=30)
    dayIntensity: int = Field(default=25, ge=0, le=100)
    # Whether issue alerting was on for this run -- replayed on Import (see
    # TropismProgram.tsx/GrowthProgram.tsx). There is no contact field to
    # mirror here at all: delivery is Telegram, resolved server-side from a
    # per-user link, never a per-experiment address (see TropismConfig).
    reportOnIssueEnabled: bool = False
    # A full snapshot of DeviceSettings as it stood when this run started: a
    # historical record of how these images were taken, and the payload Import
    # replays into the live device settings so a past run can be reproduced
    # exactly. Every field of DeviceSettings must be mirrored here -- see
    # test_saved_config_covers_every_device_setting, which fails if one is
    # added to DeviceSettings and not carried through to here and the XML.
    photoIlluminationSource: PhotoIlluminationSource = "ir"
    leds: LedSettings = Field(default_factory=LedSettings)
    ir: IrSettings = Field(default_factory=IrSettings)
    camera: CameraSettings = Field(default_factory=CameraSettings)


# ---------------------------------------------------------------------------
# QA assistant (local chat, see rapidboxes/assistant/service.py)
# ---------------------------------------------------------------------------


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Echoed back by the client so a page refresh doesn't lose context; the
    # server also keeps its own copy for archiving, see AssistantService.
    history: List[AssistantMessage] = Field(default_factory=list)
    # Bounded like every other username field in this file (and every username
    # Query in api/). This one had no constraint at all, and it is the one that
    # reaches the assistant's system prompt (see AssistantService.chat), so an
    # unbounded value here was an unbounded prompt-injection surface.
    #
    # Note what this does NOT fix: over HTTP this value is simply asserted by
    # the caller, so the "always scoped to whoever is chatting" promise in the
    # assistant tool docstrings is a UX affordance, not a security boundary.
    # Nothing in this API is authenticated; closing that needs auth, not a
    # length cap.
    username: Optional[str] = Field(default=None, max_length=40)


class ExperimentProposal(BaseModel):
    """A concrete, ready-to-review config resolved from one specific past
    experiment's own saved settings -- never invented by the model. The
    caller (web UI or CLI) must show this to a human and get an explicit
    confirmation before it is ever used to start anything; the assistant
    itself never calls the start API."""

    experimentId: str
    protocol: Literal["tropism", "growth"]
    sourceUsername: str
    summary: str
    config: SavedExperimentConfig


class AssistantImageRef(BaseModel):
    """One specific, real capture resolved by the show_image tool -- never
    invented by the model, always a real file that already exists (same
    never-invent-just-point-at-real-data principle as ExperimentProposal).
    The frontend opens this directly via `url`/`thumbUrl`, the same routes
    Gallery already uses."""

    experimentId: str
    imageId: str
    url: str
    thumbUrl: str
    caption: str


class AssistantDownloadRef(BaseModel):
    """One specific, real experiment folder resolved by the
    download_experiment tool, packaged as a zip -- never invented, always
    real data (same principle as AssistantImageRef/ExperimentProposal).
    `url` is the existing GET /api/experiments/{id}/download endpoint (same
    one Gallery -> Folders' own download button uses); the web UI just
    links to it, while Telegram delivery builds and uploads the zip
    directly (see telegram_link.py) since Telegram can't fetch a URL back
    from this not-internet-reachable device.

    `imageIds=None` means the whole experiment (the original, still-default
    behavior); a list means only those specific images were requested (e.g.
    "just the first three") -- `url` already has them baked in as a query
    string so the web link and the Telegram delivery path both zip the same
    subset without re-resolving "first three" a second time."""

    experimentId: str
    url: str
    filename: str
    sizeBytes: int
    imageIds: Optional[List[str]] = None


class AssistantLiveImageRef(BaseModel):
    """A snapshot/screenshot captured live for this one reply -- unlike
    AssistantImageRef, which always points at a real file that already
    existed before the tool call, this is a fresh capture made *during* the
    call with nothing persisted to disk (no experiment, no file, no URL to
    point at). Sent inline as base64 in the response itself instead:
    telegram_link.py decodes it straight back to bytes and uploads it via
    the same _send_photo_bytes a slash command uses; the web chat renders
    it as a data: URI <img>, no extra server round-trip needed."""

    mimeType: str
    base64Data: str
    caption: str


class AssistantChatResponse(BaseModel):
    reply: str
    proposal: Optional[ExperimentProposal] = None
    # Mutually exclusive in practice -- one tool call resolves to at most
    # one of these four extra payloads, never more than one.
    image: Optional[AssistantImageRef] = None
    download: Optional[AssistantDownloadRef] = None
    liveImage: Optional[AssistantLiveImageRef] = None
    # Set when the model recognized real intent to start or stop an
    # experiment right now (start_experiment/stop_experiment tools), not
    # just a question about one. `reply` above is still a complete, safe
    # answer on its own (a review-only proposal, or "use the Stop button")
    # for any consumer that ignores this field -- telegram_link.py is the
    # only one that acts on it, handing off to the same real step-by-step
    # confirmation flow /launch or /stop already use, never starting or
    # stopping anything by itself just because the model asked.
    # start_launch_exact is the stronger form -- "same as my last one",
    # not just "start it" -- and skips straight to a full summary of that
    # past run's own real values with nothing re-asked, still ending on
    # the same required "yes". Only set when a real past run was actually
    # matched; with no match it degrades to the ordinary start_launch
    # field-by-field wizard instead (see prefill_experiment's own
    # description for why there's nothing to "repeat exactly" otherwise).
    chatAction: Optional[Literal["start_launch", "start_launch_exact", "stop"]] = None


# ---------------------------------------------------------------------------
# Telegram issue-alert linking (Settings -> General -> Telegram Alerts).
# See rapidboxes/telegram_link.py.
# ---------------------------------------------------------------------------


class TelegramStatusResponse(BaseModel):
    # False if no admin has set a bot token/username yet -- the whole
    # feature is simply unavailable, distinct from "configured but this
    # user hasn't linked".
    configured: bool
    linked: bool
    botUsername: Optional[str] = None


class TelegramLinkCodeResponse(BaseModel):
    code: str
    botUsername: str


# ---------------------------------------------------------------------------
# Misc API payloads
# ---------------------------------------------------------------------------


class ImageInfo(BaseModel):
    id: str
    phase: str
    index: int
    timestamp: datetime
    url: str
    thumbUrl: str


class SystemInfo(BaseModel):
    hostname: str
    ip: str
    version: str
    simulation: bool
    storageRoot: str
    diskFreeBytes: int
    diskTotalBytes: int
    cameraAvailable: bool = True
    # Settings -> General -> SSH Access. sshUser is the account `rapidboxes.
    # service` runs as (User=@USER@ in deploy/rapidboxes.service) -- the same
    # account SSH would log into. sshEnabled reflects whether the openssh
    # server is actually reachable right now (see api/system.py).
    sshUser: str = ""
    sshEnabled: bool = False


# ---------------------------------------------------------------------------
# OTA self-update (Settings -> General -> Update button, and the monthly
# rapidboxes-update.timer). See rapidboxes/updater.py for the git plumbing.
# ---------------------------------------------------------------------------


class UpdateCheckResult(BaseModel):
    branch: str
    updateAvailable: bool
    currentCommit: Optional[str] = None
    remoteCommit: Optional[str] = None
    commitsBehind: int = 0
    # Short "<hash> <subject>" lines, newest first, capped (see updater.py).
    commitLog: List[str] = []
    error: Optional[str] = None


class UpdateApplyResult(BaseModel):
    # "updated" | "up_to_date" | "error" | "experiment_active"
    # | "rolled_back" | "nothing_to_roll_back_to" (rollback only)
    status: str
    message: str
    fromCommit: Optional[str] = None
    toCommit: Optional[str] = None
    # Set only when status in ("updated", "rolled_back"): whether the
    # post-move dependency / frontend-build step ran, and how it went.
    # "failed" means the git move succeeded but the running process is now on
    # mismatched code/deps -- a worse state than a clean refusal, so callers
    # must NOT treat this as a green light to restart (see updater.py).
    rebuildStatus: Optional[str] = None  # None | "skipped" | "ok" | "failed"
    rebuildMessage: Optional[str] = None


class UpdateHistoryEntry(BaseModel):
    """One row in update_history.json: a commit that became HEAD, and why."""

    commit: str  # short hash (8 chars), consistent with fromCommit/toCommit above
    appliedAt: datetime
    trigger: str  # "manual" | "monthly" | "rollback" | "seed"


class VersionStatus(BaseModel):
    """What Settings -> General -> Version shows: current + (if any) previous."""

    current: Optional[UpdateHistoryEntry] = None
    previous: Optional[UpdateHistoryEntry] = None
    error: Optional[str] = None


class StorageSuggestion(BaseModel):
    """Oldest-first folders (of the requesting user only) whose deletion would
    free enough room for a new experiment that doesn't currently fit."""

    experimentIds: List[str]
    count: int
    freedBytes: int


class StartResponse(BaseModel):
    status: str  # "started" | "busy" | "no_camera" | "low_space"
    experimentId: Optional[str] = None
    # Populated only when status == "low_space".
    estimatedBytes: Optional[int] = None
    availableBytes: Optional[int] = None
    suggestion: Optional[StorageSuggestion] = None


class FreeSpaceRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    experimentIds: List[str]


class FreeSpaceResponse(BaseModel):
    deletedIds: List[str]
    freedBytes: int
    availableBytes: int


# ---------------------------------------------------------------------------
# Remote CIFS/SMB sync (Settings -> General -> Remote Sync).
# See rapidboxes/remote_sync.py for the mount + copy plumbing.
#
# SECURITY: there is deliberately NO password field on any model below that is
# persisted or returned by the API. The password is accepted only on
# RemoteSyncUpdate (write-only, PUT body) and lives in process memory for the
# lifetime of the process -- never in settings files, never in a response,
# never in a process argument list. `passwordSet` is the only thing the UI
# learns about it.
# ---------------------------------------------------------------------------

# Pre-filled default, from the institutional share the legacy script mounted.
DEFAULT_REMOTE_SERVER = "//ds.asuch.cas.cz/ueb/lhr"

# Strict allowlist for the //host/share[/path] string. This value is passed to
# a *sudo* mount command, so anything outside this pattern is rejected outright
# rather than escaped: no spaces, no commas, no leading "-" (which mount would
# read as an option), no shell metacharacters, no relative segments.
REMOTE_SERVER_PATTERN = r"^//[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+$"
_REMOTE_SERVER_RE = re.compile(REMOTE_SERVER_PATTERN)
REMOTE_SERVER_MAX_LEN = 255

# The CIFS account name also reaches the (root) mount, inside the credentials
# file rather than on the command line -- but a newline there would let a
# crafted username inject extra credential directives, so it is constrained too.
REMOTE_USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@\\-]{0,63}$"
_REMOTE_USERNAME_RE = re.compile(REMOTE_USERNAME_PATTERN)


def validate_remote_server(server: str) -> str:
    """Return `server` if it is a safe //host/share[/path], else raise ValueError."""
    value = (server or "").strip()
    if not value:
        raise ValueError("server/share path is required")
    if len(value) > REMOTE_SERVER_MAX_LEN:
        raise ValueError(f"server/share path is too long (max {REMOTE_SERVER_MAX_LEN} characters)")
    if not _REMOTE_SERVER_RE.match(value):
        raise ValueError(
            "server/share path must look like //host/share or //host/share/folder "
            "(letters, digits, dots, hyphens and underscores only)"
        )
    if any(segment == ".." for segment in value.split("/")):
        raise ValueError("server/share path must not contain '..' segments")
    return value


def validate_remote_username(username: str) -> str:
    """Return `username` if it is a safe CIFS account name, else raise ValueError."""
    value = (username or "").strip()
    if not value:
        raise ValueError("username is required")
    if not _REMOTE_USERNAME_RE.match(value):
        raise ValueError(
            "username may only contain letters, digits, dots, underscores, "
            "hyphens, '@' and '\\'"
        )
    return value


class RemoteSyncSettings(BaseModel):
    """The persisted (non-secret) half of the remote-sync configuration.

    Written to its own JSON file rather than into DeviceSettings: it is not a
    property of how an image was taken, so it must not travel into the
    per-experiment config XML — and keeping it separate means the password can
    never be swept into a settings snapshot by accident.
    """

    enabled: bool = False
    server: str = DEFAULT_REMOTE_SERVER
    # The CIFS/SMB account used to mount the share.
    username: str = ""
    # The researcher whose experiments sync (the destination subfolder on the
    # share). Captured when sync is switched on; sync stops if it changes.
    researcher: str = ""

    @model_validator(mode="after")
    def _check(self) -> "RemoteSyncSettings":
        if self.server:
            validate_remote_server(self.server)
        if self.username:
            validate_remote_username(self.username)
        return self


class RemoteSyncUpdate(BaseModel):
    """PUT /api/settings/remote-sync body — the only model carrying a password.

    Every field is optional so the UI can patch one thing at a time (e.g. flip
    the toggle without resending credentials). `password` is WRITE-ONLY: it is
    never echoed back by any endpoint and never written to disk.
    """

    enabled: Optional[bool] = None
    server: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, max_length=256)
    researcher: Optional[str] = None


class RemoteSyncStatus(BaseModel):
    """GET /api/settings/remote-sync — everything the UI shows. No password."""

    enabled: bool = False
    server: str = DEFAULT_REMOTE_SERVER
    username: str = ""
    # Whether a password is held in memory. Never the password, never its length.
    passwordSet: bool = False
    mounted: bool = False
    # The loud state: sync is switched on but the in-memory password is gone
    # (fresh process after a restart/reboot/OTA update), so nothing can sync
    # until a human re-enters it.
    credentialsRequired: bool = False
    # Destination subfolder on the share = the researcher this sync is armed for.
    researcher: str = ""
    remotePath: Optional[str] = None
    # Images captured but not yet copied (queued + failed-and-awaiting-retry).
    pendingCount: int = 0
    lastSyncAt: Optional[datetime] = None
    lastResult: Optional[str] = None  # "ok" | "error"
    lastError: Optional[str] = None
    # Progress/outcome text for the last "Sync entire folder now" run.
    bulkInProgress: bool = False
    bulkMessage: Optional[str] = None
    # True on a dev laptop (RAPIDBOXES_SIMULATION=1): no real CIFS mount is
    # attempted; the share is emulated by a local directory.
    simulation: bool = False


# ---------------------------------------------------------------------------
# Synology DSM sharing links (Settings -> General -> Sharing Links), used by
# the assistant's upload_experiment_to_remote tool to hand back a real,
# clickable, internet-reachable URL instead of just a local network path.
# A DIFFERENT server/account than Remote Sync's CIFS share -- see
# rapidboxes/dsm_sharing.py's module docstring for why this exists
# separately rather than reusing the CIFS credentials. Same password
# handling as Remote Sync: session-only, never persisted, never returned.
# ---------------------------------------------------------------------------

DSM_HOST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$"
_DSM_HOST_RE = re.compile(DSM_HOST_PATTERN)

# A DSM-internal filesystem path (e.g. "/volume1/ueb-if"), NOT a CIFS UNC
# path -- File Station's own API addresses folders this way, and Synology's
# volume numbering can't be derived from the share name, so this is entered
# by whoever set up the NAS, same as the CIFS server string.
DSM_SHARE_ROOT_PATTERN = r"^/[A-Za-z0-9][A-Za-z0-9._/-]{0,253}$"
_DSM_SHARE_ROOT_RE = re.compile(DSM_SHARE_ROOT_PATTERN)


def validate_dsm_host(host: str) -> str:
    value = (host or "").strip()
    if not value:
        raise ValueError("host is required")
    if not _DSM_HOST_RE.match(value):
        raise ValueError("host must be a plain hostname (letters, digits, dots, hyphens)")
    return value


def validate_dsm_share_root(path: str) -> str:
    value = (path or "").strip()
    if not value:
        raise ValueError("share root is required")
    if not _DSM_SHARE_ROOT_RE.match(value):
        raise ValueError("share root must be an absolute DSM path, e.g. /volume1/ueb-if")
    if any(segment == ".." for segment in value.split("/")):
        raise ValueError("share root must not contain '..' segments")
    return value


class DsmSharingSettings(BaseModel):
    """The persisted (non-secret) half of the DSM-sharing configuration."""

    enabled: bool = False
    host: str = ""
    port: int = 5001
    username: str = ""
    # DSM-internal path whose <share_root>/<username>/<experiment_id>
    # subfolder is where Remote Sync's CIFS copies actually land on this
    # NAS's own filesystem (see the module docstring in dsm_sharing.py).
    shareRoot: str = ""

    @model_validator(mode="after")
    def _check(self) -> "DsmSharingSettings":
        if self.host:
            validate_dsm_host(self.host)
        if self.shareRoot:
            validate_dsm_share_root(self.shareRoot)
        return self


class DsmSharingUpdate(BaseModel):
    """PUT /api/settings/dsm-sharing body. `password` is write-only, same
    precedent as RemoteSyncUpdate: never echoed back, never persisted."""

    enabled: Optional[bool] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = Field(default=None, max_length=256)
    shareRoot: Optional[str] = None


class DsmSharingStatus(BaseModel):
    """GET /api/settings/dsm-sharing — everything the UI shows. No password."""

    enabled: bool = False
    host: str = ""
    port: int = 5001
    username: str = ""
    shareRoot: str = ""
    passwordSet: bool = False
    # Switched on, but the in-memory password is gone (a restart) -- nothing
    # can generate a link until a human re-enters it. Same state/meaning as
    # RemoteSyncStatus.credentialsRequired.
    credentialsRequired: bool = False
    lastResult: Optional[str] = None  # "ok" | "error"
    lastError: Optional[str] = None
