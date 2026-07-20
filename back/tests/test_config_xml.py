from rapidboxes import config_xml
from rapidboxes.models import (
    CameraSettings,
    DeviceSettings,
    IrSettings,
    LedSettings,
    SavedExperimentConfig,
)


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
            jpegQuality=80,
            zoom=2.4,
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
            jpegQuality=90,
            zoom=1.0,
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
    xml_bytes = b"""<?xml version='1.0' encoding='utf-8'?>
<experimentConfig version="2" protocol="growth"><phases><growth dayLengthHours="16" experimentLengthDays="14" photoIlluminationSource="ir" /></phases><light intervalMinutes="30.0" dayIntensity="25"><spectrum>white</spectrum></light><camera width="2304" height="1296" exposureMicroseconds="3343100" iso="100" autofocusEnabled="true" focusDistance="0.0" grayscale="true" awbRedGain="2.0" awbBlueGain="1.0" jpegQuality="92" settleSeconds="1.0" /></experimentConfig>"""

    restored = config_xml.parse(xml_bytes)

    assert restored.protocol == "growth"
    assert restored.photoIlluminationSource == "ir"
    assert restored.leds == LedSettings()
    assert restored.ir == IrSettings()  # <ir> absent too -> defaults


def test_parse_v4_file_without_zoom_attribute():
    """v4 files (written before AWB/settle were dropped and zoom was added)
    have <camera awbRedGain=... awbBlueGain=... settleSeconds=...> but no
    zoom attribute. The stale attributes are simply ignored; a missing zoom
    defaults to 1.0 (no crop) rather than failing to parse."""
    xml_bytes = b"""<?xml version='1.0' encoding='utf-8'?>
<experimentConfig version="4" protocol="growth"><phases><growth dayLengthHours="16" experimentLengthDays="14" /></phases><light intervalMinutes="30.0" dayIntensity="25"><spectrum>white</spectrum></light><illumination source="ir"/><ir pins="26,23"/><leds pixelCount="70" pixelOrder="GRBW" topSegment="22,64" lateralSegment="0,21" spiHz="6400000" stride="1"/><camera width="2304" height="1296" exposureMicroseconds="3500000" iso="100" autofocusEnabled="true" focusDistance="0.0" grayscale="true" awbRedGain="2.0" awbBlueGain="1.0" jpegQuality="92" settleSeconds="1.0" /></experimentConfig>"""

    restored = config_xml.parse(xml_bytes)

    assert restored.camera.zoom == 1.0
    assert restored.camera.exposureMicroseconds == 3_500_000


def test_round_trip_ir_pins():
    """IR pins survive the XML round trip, so Import can replay them."""
    original = SavedExperimentConfig(ir=IrSettings(pins=[17, 27, 22]))
    restored = config_xml.parse(config_xml.serialize(original))
    assert restored.ir.pins == [17, 27, 22]
    assert restored == original


def test_saved_config_covers_every_device_setting():
    """SavedExperimentConfig must mirror every DeviceSettings field, and each
    must survive the XML round trip.

    Import replays this snapshot into the live device settings, so a field
    present in DeviceSettings but missing here would silently fail to be saved
    and restored. This fails the moment a new setting is added to
    DeviceSettings without also being carried through to the saved config and
    config_xml serialize/parse.
    """
    missing = set(DeviceSettings.model_fields) - set(SavedExperimentConfig.model_fields)
    assert not missing, f"DeviceSettings fields not saved in the experiment config: {missing}"

    # Non-default values for every device field, to prove each is really
    # serialized rather than silently falling back to a default on parse.
    original = SavedExperimentConfig(
        photoIlluminationSource="rgbw",
        leds=LedSettings(pixelCount=120, topSegment=(10, 90), lateralSegment=(0, 9), stride=3),
        ir=IrSettings(pins=[5, 6]),
        camera=CameraSettings(exposureMicroseconds=3_000_000, iso=800, grayscale=False),
    )
    restored = config_xml.parse(config_xml.serialize(original))

    for field in DeviceSettings.model_fields:
        assert getattr(restored, field) == getattr(original, field), f"{field} did not round trip"
