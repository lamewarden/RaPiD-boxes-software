from pathlib import Path

from rapidboxes import camera_defaults
from rapidboxes.models import CameraSettings


def test_load_for_missing_file_returns_none(tmp_path: Path):
    assert camera_defaults.load_for(tmp_path / "nope.json", "lev") is None


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "camera_user_defaults.json"
    saved = CameraSettings(focusDistance=12.5, iso=200)

    result = camera_defaults.save_for(path, "lev", saved)

    assert result == saved
    assert camera_defaults.load_for(path, "lev") == saved


def test_username_matching_is_trimmed_and_case_insensitive(tmp_path: Path):
    path = tmp_path / "camera_user_defaults.json"
    camera_defaults.save_for(path, "  Lev  ", CameraSettings(iso=400))

    assert camera_defaults.load_for(path, "lev") == CameraSettings(iso=400)
    assert camera_defaults.load_for(path, "LEV") == CameraSettings(iso=400)


def test_saving_for_one_user_does_not_disturb_another(tmp_path: Path):
    path = tmp_path / "camera_user_defaults.json"
    camera_defaults.save_for(path, "lev", CameraSettings(iso=200))
    camera_defaults.save_for(path, "kashkan", CameraSettings(iso=800))

    assert camera_defaults.load_for(path, "lev").iso == 200
    assert camera_defaults.load_for(path, "kashkan").iso == 800


def test_saving_again_overwrites_the_same_users_entry(tmp_path: Path):
    path = tmp_path / "camera_user_defaults.json"
    camera_defaults.save_for(path, "lev", CameraSettings(iso=200))
    camera_defaults.save_for(path, "lev", CameraSettings(iso=1600))

    assert camera_defaults.load_for(path, "lev").iso == 1600


def test_corrupt_file_treated_as_empty(tmp_path: Path):
    path = tmp_path / "camera_user_defaults.json"
    path.write_text("not json")

    assert camera_defaults.load_for(path, "lev") is None
    assert camera_defaults.load_all(path) == {}
