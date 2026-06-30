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


def test_round_trip_growth_values():
    original = SavedExperimentConfig(
        protocol="growth",
        preIlluminationEnabled=False,
        spectra=["white", "blue"],
        intervalMinutes=30.0,
        dayLengthHours=18,
        experimentLengthDays=10,
        dayIntensity=45,
        photoIlluminationSource="rgbw",
        camera=CameraSettings(
            width=2304,
            height=1296,
            exposureMicroseconds=120_000,
            iso=200,
            grayscale=True,
            awbRedGain=2.0,
            awbBlueGain=1.0,
            jpegQuality=90,
            settleSeconds=1.5,
        ),
    )
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored == original
