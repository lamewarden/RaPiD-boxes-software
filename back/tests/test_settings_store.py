from rapidboxes.models import CameraSettings, DeviceSettings, IrSettings
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
            camera=CameraSettings(iso=800, exposureMicroseconds=500_000),
            ir=IrSettings(pins=[1, 2]),
        ),
    )

    restored = load_device_settings_for_new_session(path)

    assert restored.camera == CameraSettings()
    assert restored.ir == IrSettings(pins=[1, 2])  # non-camera settings survive
    assert load_device_settings(path).camera == CameraSettings()  # reset is persisted too


def test_new_session_with_no_existing_file_uses_defaults(tmp_path):
    path = tmp_path / "settings.json"
    restored = load_device_settings_for_new_session(path)
    assert restored == DeviceSettings()
    assert path.exists()
