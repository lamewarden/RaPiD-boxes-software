from pathlib import Path

from rapidboxes import user_defaults
from rapidboxes.models import CameraSettings, DeviceSettings, IrSettings, LedSettings


def test_load_for_missing_file_returns_none(tmp_path: Path):
    assert user_defaults.load_for(tmp_path / "nope.json", "lev") is None


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "user_defaults.json"
    saved = DeviceSettings(
        camera=CameraSettings(focusDistance=12.5, iso=200),
        leds=LedSettings(pixelCount=80),
        ir=IrSettings(pins=[5, 6]),
        photoIlluminationSource="rgbw",
    )

    result = user_defaults.save_for(path, "lev", saved)

    assert result == saved
    assert user_defaults.load_for(path, "lev") == saved


def test_username_matching_is_trimmed_and_case_insensitive(tmp_path: Path):
    path = tmp_path / "user_defaults.json"
    user_defaults.save_for(path, "  Lev  ", DeviceSettings(camera=CameraSettings(iso=400)))

    assert user_defaults.load_for(path, "lev").camera.iso == 400
    assert user_defaults.load_for(path, "LEV").camera.iso == 400


def test_saving_for_one_user_does_not_disturb_another(tmp_path: Path):
    path = tmp_path / "user_defaults.json"
    user_defaults.save_for(path, "lev", DeviceSettings(camera=CameraSettings(iso=200)))
    user_defaults.save_for(path, "kashkan", DeviceSettings(camera=CameraSettings(iso=800)))

    assert user_defaults.load_for(path, "lev").camera.iso == 200
    assert user_defaults.load_for(path, "kashkan").camera.iso == 800


def test_saving_again_overwrites_the_same_users_entry(tmp_path: Path):
    path = tmp_path / "user_defaults.json"
    user_defaults.save_for(path, "lev", DeviceSettings(camera=CameraSettings(iso=200)))
    user_defaults.save_for(path, "lev", DeviceSettings(camera=CameraSettings(iso=1600)))

    assert user_defaults.load_for(path, "lev").camera.iso == 1600


def test_corrupt_file_treated_as_empty(tmp_path: Path):
    path = tmp_path / "user_defaults.json"
    path.write_text("not json")

    assert user_defaults.load_for(path, "lev") is None
    assert user_defaults.load_all(path) == {}
