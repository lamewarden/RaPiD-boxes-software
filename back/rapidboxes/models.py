"""Pydantic models: the source of truth for the API contract.

These are mirrored as TypeScript interfaces in front/shared/api.ts.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Experiment configuration (request body for POST /api/experiments)
# ---------------------------------------------------------------------------

Spectrum = str  # one of: "white" | "red" | "green" | "blue"
VALID_SPECTRA = ("white", "red", "green", "blue")


class TropismConfig(BaseModel):
    """The Tropism protocol as exposed by the React UI.

    Maps to the legacy 3-stage protocol:
      pre-illumination (optional white soak) -> dark "apical hook" -> "bending".
    """

    experimentName: str = Field(default="experiment", min_length=1, max_length=80)
    username: str = Field(default="pi", min_length=1, max_length=40)

    # Optional white-light soak before the dark phase (legacy "6h pre-treatment").
    preIlluminationEnabled: bool = False
    preIlluminationHours: float = Field(default=6.0, ge=0, le=48)

    # Dark "apical hook" phase: IR-lit captures in darkness.
    darkPhaseEnabled: bool = True
    darkPhaseHours: float = Field(default=90.0, ge=0, le=350)

    # Phototropic "bending" phase: unilateral coloured light between captures.
    lateralIlluminationHours: float = Field(default=20.0, ge=0, le=168)
    spectra: List[Spectrum] = Field(default_factory=lambda: ["white"])

    # Imaging cadence and brightness, shared by both imaging phases.
    intervalMinutes: float = Field(default=20.0, ge=1, le=240)
    intensity: int = Field(default=25, ge=0, le=100)

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
    pre_illumination = "pre_illumination"
    dark = "dark"
    bending = "bending"


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
    config: Optional[TropismConfig] = None


# ---------------------------------------------------------------------------
# Device settings (GET/PUT /api/settings) — user-editable hardware defaults
# ---------------------------------------------------------------------------


class CameraSettings(BaseModel):
    width: int = Field(default=2304, ge=320, le=4608)   # v3 sensor half-res default
    height: int = Field(default=1296, ge=240, le=2592)
    exposureMicroseconds: int = Field(default=100_000, ge=100, le=10_000_000)
    iso: int = Field(default=100, ge=50, le=1600)
    grayscale: bool = True
    awbRedGain: float = Field(default=2.0, ge=0.0, le=8.0)
    awbBlueGain: float = Field(default=1.0, ge=0.0, le=8.0)
    jpegQuality: int = Field(default=92, ge=40, le=100)
    settleSeconds: float = Field(default=1.0, ge=0, le=30)


class LedSettings(BaseModel):
    pixelCount: int = Field(default=70, ge=1, le=600)
    pixelOrder: str = "GRBW"
    # Inclusive-exclusive [start, end) segments on the strip.
    topSegment: Tuple[int, int] = (22, 64)
    lateralSegment: Tuple[int, int] = (0, 21)
    spiHz: int = Field(default=6_400_000, ge=2_000_000, le=10_000_000)


class IrSettings(BaseModel):
    # BCM pins for the two IR boards (legacy: 26 & 23).
    pins: List[int] = Field(default_factory=lambda: [26, 23])


class DeviceSettings(BaseModel):
    camera: CameraSettings = Field(default_factory=CameraSettings)
    leds: LedSettings = Field(default_factory=LedSettings)
    ir: IrSettings = Field(default_factory=IrSettings)


# ---------------------------------------------------------------------------
# Saved/loaded experiment config (the per-experiment <name>.xml) — phases,
# light and camera, deliberately excluding identity fields (name/username).
# ---------------------------------------------------------------------------


class SavedExperimentConfig(BaseModel):
    preIlluminationEnabled: bool = False
    preIlluminationHours: float = Field(default=6.0, ge=0, le=48)
    darkPhaseEnabled: bool = True
    darkPhaseHours: float = Field(default=90.0, ge=0, le=350)
    lateralIlluminationHours: float = Field(default=20.0, ge=0, le=168)
    spectra: List[Spectrum] = Field(default_factory=lambda: ["white"])
    intervalMinutes: float = Field(default=20.0, ge=1, le=240)
    intensity: int = Field(default=25, ge=0, le=100)
    camera: CameraSettings = Field(default_factory=CameraSettings)


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
    diskFreeBytes: int
    diskTotalBytes: int
    cameraAvailable: bool = True


class StartResponse(BaseModel):
    status: str  # "started" | "busy" | "no_camera"
    experimentId: Optional[str] = None
