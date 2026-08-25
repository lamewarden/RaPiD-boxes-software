"""Application configuration (infrastructure-level, from env / .env).

Distinct from `DeviceSettings` in models.py, which are the user-editable hardware
defaults persisted to a JSON file and exposed over the API.
"""
from __future__ import annotations

import platform
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _auto_simulation() -> bool:
    """Default to simulation unless we're on a Linux box with picamera2 available.

    Keeps laptop development safe (no hardware) while letting the Pi run real
    hardware without extra config. Can always be overridden by RAPIDBOXES_SIMULATION.
    """
    if platform.system() != "Linux":
        return True
    try:
        import picamera2  # noqa: F401  (Pi-only, present on device)

        return False
    except Exception:
        return True


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAPIDBOXES_",
        env_file=".env",
        extra="ignore",
    )

    # Hardware mode. True -> fully simulated backends (no Pi required).
    simulation: bool = _auto_simulation()

    # HTTP server
    host: str = "0.0.0.0"
    port: int = 8000

    # Where experiment folders and images are written.
    storage_root: Path = Path.home() / "rapidboxes" / "experiments"

    # Persisted, user-editable device settings (camera/leds/ir). Created on first run.
    settings_path: Path = Path.home() / "rapidboxes" / "settings.json"

    # Per-user saved settings baseline (Settings -> "Save Mine"), keyed
    # by username, covering camera + illumination as one bundle. Distinct
    # from settings_path: that one's camera half is reset to the system
    # default every process start; this one only changes when a user
    # explicitly saves over it. See rapidboxes/user_defaults.py.
    user_defaults_path: Path = Path.home() / "rapidboxes" / "user_defaults.json"

    # Built React SPA (dist/spa). When set and present, it is served at "/".
    # In dev we leave this unset and use the Vite dev server + proxy instead.
    spa_dir: Optional[Path] = None

    # Live preview (MJPEG) target frame rate.
    preview_fps: float = 5.0

    # Branch this device tracks for the OTA self-update feature (Settings ->
    # General -> Update button, and the monthly rapidboxes-update.timer).
    # Judgment call: defaults to "main" as the intended long-term stable
    # branch, but whatever branch is actually checked out on a given device
    # may differ (e.g. this repo is developed on "v2" while "main" is the
    # older/stable branch) -- override with RAPIDBOXES_UPDATE_BRANCH so
    # `git fetch origin <branch>` / `git merge --ff-only origin/<branch>`
    # compares against the branch this deployment is meant to track, not
    # blindly against origin/main.
    update_branch: str = "main"

    # Record of applied OTA updates/rollbacks (see rapidboxes/update_history.py),
    # used by Settings -> General -> Version to show "running commit X for Y"
    # and to know what the "Roll back" button targets. Same directory
    # convention as settings_path.
    update_history_path: Path = Path.home() / "rapidboxes" / "update_history.json"

    # Remote CIFS sync (Settings -> General -> Remote Sync). Only the
    # non-secret half lives here -- server, CIFS username, on/off, researcher.
    # The password is session-only and is never written to this (or any) file;
    # see rapidboxes/remote_sync.py.
    remote_sync_path: Path = Path.home() / "rapidboxes" / "remote_sync.json"

    # QA chat assistant -- an OpenAI-compatible remote API (e-INFRA CZ's
    # shared LLM gateway), not a local model. A local Ollama model was tried
    # first (see PROJECT_BRIEFING.md) but was unreliable at the JSON-action
    # protocol and risked destabilizing this 4GB Pi; this API supports real
    # tool/function calling instead, which every model tested got right.
    # assistant_api_key deliberately has no default -- it must come from
    # /etc/rapidboxes.env (RAPIDBOXES_ASSISTANT_API_KEY, git-ignored) in
    # production, never from source.
    assistant_api_base_url: str = "https://llm.ai.e-infra.cz/v1"
    assistant_api_key: str = ""
    assistant_model: str = "qwen3.5-122b"
    # Separate model for image-anomaly checks (check_my_images tool, the
    # end-of-experiment summary). Real testing showed qwen3.5-122b is
    # noticeably slower at vision specifically (up to 7.6s) than command-a
    # (consistently under 6s, equally or more accurate on real device
    # images) -- different models are better at different jobs here.
    assistant_vision_model: str = "command-a"
    assistant_archive_dir: Path = Path.home() / "rapidboxes" / "assistant_archive"

    # Opt-in issue-alert delivery (MoldWatchService, see telegram_link.py).
    # Both deliberately have no default -- unlike assistant_api_key this
    # whole feature is optional: unset means "not configured yet", not an
    # error, and reportOnIssueEnabled simply can't be turned on until an
    # admin creates a bot (via @BotFather) and sets these two from
    # /etc/rapidboxes.env, same as the LLM key -- never from source.
    telegram_bot_token: Optional[str] = None
    # No leading "@" -- shown in the UI as "@{telegram_bot_username}".
    telegram_bot_username: Optional[str] = None
    # Per-user chat_id links, keyed by lowercased RapiDBoxes username. Same
    # directory convention as settings_path/user_defaults_path.
    telegram_links_path: Path = Path.home() / "rapidboxes" / "telegram_links.json"
    # Active /monitor subscriptions, keyed by experiment_id -- persisted (not
    # just in-memory) specifically so a subscription survives the exact kind
    # of restart it exists to report on: see TelegramLinkService's blackout
    # notification, sent right after ExperimentRunner.recover().
    telegram_monitors_path: Path = Path.home() / "rapidboxes" / "telegram_monitors.json"

    def ensure_dirs(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.assistant_archive_dir.mkdir(parents=True, exist_ok=True)
        self.telegram_links_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> AppConfig:
    cfg = AppConfig()
    cfg.ensure_dirs()
    return cfg
