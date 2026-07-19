from rapidboxes import config_xml
from rapidboxes.models import CameraSettings, SavedExperimentConfig


def test_round_trip_default():
    original = SavedExperimentConfig()
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored == original


def test_round_trip_custom_values():
    original = SavedExperimentConfig(
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
            autofocusEnabled=False,
            focusDistance=4.5,
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
            autofocusEnabled=True,
            focusDistance=0.0,
            grayscale=True,
            awbRedGain=2.0,
            awbBlueGain=1.0,
            jpegQuality=90,
            settleSeconds=1.5,
        ),
    )
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored == original


def test_parse_growth_legacy_light_intensity_field():
        xml_bytes = b"""<?xml version='1.0' encoding='utf-8'?>
<experimentConfig version='2' protocol='growth'>
    <phases>
        <growth dayLengthHours='16' experimentLengthDays='14' photoIlluminationSource='rgbw' />
    </phases>
    <light intervalMinutes='30.0' intensity='35'>
        <spectrum>white</spectrum>
    </light>
    <camera width='2304' height='1296' exposureMicroseconds='100000' iso='100' grayscale='true' awbRedGain='2.0' awbBlueGain='1.0' jpegQuality='92' settleSeconds='1.0' />
</experimentConfig>
"""

        restored = config_xml.parse(xml_bytes)

        assert restored.protocol == "growth"
        assert restored.dayIntensity == 35
        assert restored.photoIlluminationSource == "rgbw"
        assert restored.camera.autofocusEnabled is True
        assert restored.camera.focusDistance == 0.0


def test_parse_v2_file_without_illumination_or_leds_elements():
    """v2 files (written before the illumination-settings refactor) have no
    top-level <illumination> or <leds> element, and photoIlluminationSource
    lives on <growth> instead. These files already exist on disk; parse()
    must still read them (falls back to LedSettings() defaults)."""
    from rapidboxes.models import LedSettings

    xml_bytes = b"""<?xml version='1.0' encoding='utf-8'?>
<experimentConfig version="2" protocol="growth"><phases><growth dayLengthHours="16" experimentLengthDays="14" photoIlluminationSource="ir" /></phases><light intervalMinutes="30.0" dayIntensity="25"><spectrum>white</spectrum></light><camera width="2304" height="1296" exposureMicroseconds="3343100" iso="100" autofocusEnabled="true" focusDistance="0.0" grayscale="true" awbRedGain="2.0" awbBlueGain="1.0" jpegQuality="92" settleSeconds="1.0" /></experimentConfig>"""

    restored = config_xml.parse(xml_bytes)

    assert restored.protocol == "growth"
    assert restored.photoIlluminationSource == "ir"
    assert restored.leds == LedSettings()
