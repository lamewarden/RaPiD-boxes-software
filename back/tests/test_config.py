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
