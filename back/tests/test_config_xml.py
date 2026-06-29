from rapidboxes import config_xml
from rapidboxes.models import CameraSettings, SavedExperimentConfig


def test_round_trip_default():
    original = SavedExperimentConfig()
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored == original


def test_round_trip_custom_values():
    original = SavedExperimentConfig(
        preIlluminationEnabled=True,
        preIlluminationHours=3.5,
        darkPhaseEnabled=False,
        darkPhaseHours=12.0,
        lateralIlluminationHours=8.0,
        spectra=["red", "blue"],
        intervalMinutes=15.0,
        intensity=60,
        camera=CameraSettings(
            width=1152,
            height=648,
            exposureMicroseconds=250_000,
            iso=400,
            grayscale=False,
            awbRedGain=1.5,
            awbBlueGain=2.5,
            jpegQuality=80,
            settleSeconds=2.0,
        ),
    )
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored == original
