from rapidboxes.models import (
    EXPOSURE_PROFILES,
    CameraSettings,
    DeviceSettings,
    IrSettings,
    LedSettings,
)
from rapidboxes.settings_store import (
    load_device_settings,
    load_device_settings_for_new_session,
    save_device_settings,
)


def test_new_session_resets_camera_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    save_device_settings(
        path,
        DeviceSettings(
            camera=CameraSettings(iso=800, exposureMicroseconds=5_000_000),
            ir=IrSettings(pins=[1, 2]),
        ),
    )

    restored = load_device_settings_for_new_session(path)

    # Camera returns to defaults, except exposure, which is not tuned freely:
    # it belongs to the illumination source and follows it across the reset.
    expected = CameraSettings(exposureMicroseconds=EXPOSURE_PROFILES["ir"]["default"])
    assert restored.camera == expected
    assert restored.camera.iso == CameraSettings().iso  # the in-session tweak is gone
    assert restored.ir == IrSettings(pins=[1, 2])  # non-camera settings survive
    assert load_device_settings(path).camera == expected  # reset is persisted too


def test_new_session_with_no_existing_file_uses_defaults(tmp_path):
    path = tmp_path / "settings.json"
    restored = load_device_settings_for_new_session(path)
    assert restored == DeviceSettings()
    assert path.exists()


def test_session_reset_repairs_exposure_for_persisted_source(tmp_path):
    """A new session resets the camera to defaults but keeps the illumination
    source. The two must land coherent: an IR box must not come back at flash
    exposure and capture nothing but black frames."""
    path = tmp_path / "settings.json"
    save_device_settings(
        path,
        DeviceSettings(
            photoIlluminationSource="ir",
            camera=CameraSettings(exposureMicroseconds=8_000_000),
            leds=LedSettings(stride=4),
        ),
    )

    fresh = load_device_settings_for_new_session(path)

    # Camera is back to defaults except exposure, which follows the source.
    assert fresh.camera.exposureMicroseconds == EXPOSURE_PROFILES["ir"]["default"]
    assert fresh.camera.iso == CameraSettings().iso
    # Non-camera settings persist untouched.
    assert fresh.photoIlluminationSource == "ir"
    assert fresh.leds.stride == 4
