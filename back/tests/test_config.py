import pytest
from pydantic import ValidationError

from rapidboxes.models import TropismConfig


def test_valid_minimal_config():
    c = TropismConfig(darkPhaseEnabled=True, darkPhaseHours=10, lateralIlluminationHours=0)
    assert c.darkPhaseHours == 10


def test_rejects_no_imaging_phase():
    with pytest.raises(ValidationError):
        TropismConfig(darkPhaseEnabled=False, darkPhaseHours=0, lateralIlluminationHours=0)


def test_rejects_unknown_spectrum():
    with pytest.raises(ValidationError):
        TropismConfig(lateralIlluminationHours=5, spectra=["ultraviolet"])


def test_rejects_out_of_range_intensity():
    with pytest.raises(ValidationError):
        TropismConfig(lateralIlluminationHours=5, spectra=["red"], intensity=150)


def test_bending_requires_a_colour():
    with pytest.raises(ValidationError):
        TropismConfig(darkPhaseEnabled=False, lateralIlluminationHours=5, spectra=[])


def test_exposure_is_coupled_to_illumination_source():
    """Exposure and light source are one decision: IR needs seconds, the RGBW
    flash milliseconds. DeviceSettings must never hold an incoherent pair."""
    from rapidboxes.models import EXPOSURE_PROFILES, CameraSettings, DeviceSettings

    ir_default = EXPOSURE_PROFILES["ir"]["default"]
    rgbw_default = EXPOSURE_PROFILES["rgbw"]["default"]

    # Defaults are coherent out of the box.
    assert DeviceSettings().camera.exposureMicroseconds == ir_default
    assert DeviceSettings(photoIlluminationSource="rgbw").camera.exposureMicroseconds == rgbw_default

    # An exposure that suits the other source is snapped to this one's default.
    blown = DeviceSettings(
        photoIlluminationSource="rgbw",
        camera=CameraSettings(exposureMicroseconds=ir_default),
    )
    assert blown.camera.exposureMicroseconds == rgbw_default

    black = DeviceSettings(
        photoIlluminationSource="ir",
        camera=CameraSettings(exposureMicroseconds=rgbw_default),
    )
    assert black.camera.exposureMicroseconds == ir_default

    # A deliberate in-range choice is respected, not overwritten.
    for source, value in (("ir", 5_000_000), ("rgbw", 50_000)):
        s = DeviceSettings(
            photoIlluminationSource=source,
            camera=CameraSettings(exposureMicroseconds=value),
        )
        assert s.camera.exposureMicroseconds == value
