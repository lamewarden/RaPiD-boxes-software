"""Filesystem storage: experiment folders, atomic metadata, image + thumbnails.

Layout (flat, easy to resolve from a URL):
    {storage_root}/{YYYY-MM-DD}_{username}_{name}/
        dark_00000.png ...
        bending_00000.png ...
        thumbs/<image_id>.jpg
        metadata.json
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from . import user_defaults

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str) -> str:
    return _SAFE.sub("-", text.strip()) or "x"


class ExperimentDir:
    def __init__(self, path: Path):
        self.path = path
        self.experiment_id = path.name
        (self.path / "thumbs").mkdir(parents=True, exist_ok=True)

    # --- writing during a run -------------------------------------------
    def image_path(self, phase: str, index: int) -> Tuple[Path, str]:
        image_id = f"{phase}_{index:05d}"
        return self.path / f"{image_id}.png", image_id

    def write_metadata(self, data: dict) -> None:
        """Atomic write so a crash mid-write can't corrupt the file."""
        tmp = self.path / "metadata.json.tmp"
        final = self.path / "metadata.json"
        with tmp.open("w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, final)

    def append_event(self, message: str) -> None:
        """One timestamped line appended to this experiment's own events.log
        -- durable, guaranteed-scoped to this experiment_id, and covered by
        the same 90-day retention as its images (it lives inside the
        experiment folder, deleted for free by delete_experiment()) unlike
        the shared systemd journal, which has no per-experiment tagging and
        no guaranteed time-floor retention. Best-effort: a logging failure
        here must never break capture/control flow, so this never raises."""
        ts = datetime.now().isoformat(timespec="seconds")
        try:
            with (self.path / "events.log").open("a") as f:
                f.write(f"{ts} {message}\n")
        except OSError:
            pass

    def read_events(self, max_lines: int = 200) -> str:
        """Last `max_lines` of this experiment's events.log, oldest first
        within that window. Empty string if the file doesn't exist (older
        experiments predating this feature, or one with no logged events)."""
        f = self.path / "events.log"
        if not f.exists():
            return ""
        try:
            lines = f.read_text().splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-max_lines:])

    def read_metadata(self) -> Optional[dict]:
        f = self.path / "metadata.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except Exception:
            return None

    def write_config_xml(self, xml_bytes: bytes, experiment_name: str) -> None:
        """Atomic write of the saved-config XML, named after the experiment."""
        final = self.path / f"{_slug(experiment_name)}.xml"
        tmp = final.with_suffix(".xml.tmp")
        tmp.write_bytes(xml_bytes)
        os.replace(tmp, final)

    def read_config_xml(self) -> Optional[bytes]:
        """Reads whatever single config xml is in the folder, if any."""
        found = next(self.path.glob("*.xml"), None)
        return found.read_bytes() if found else None

    # --- ownership / lifecycle -------------------------------------------
    def username(self) -> Optional[str]:
        """Owning username: metadata.json if present, else the folder-name
        segment set at creation time ({YYYY-MM-DD}_{username}_{name...})."""
        meta = self.read_metadata() or {}
        name = meta.get("username")
        if name:
            return name
        parts = self.experiment_id.split("_")
        return parts[1] if len(parts) >= 3 else None

    def started_date(self) -> Optional[date]:
        """Start date parsed from the folder name's leading YYYY-MM-DD."""
        try:
            return datetime.strptime(self.experiment_id[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.path.rglob("*") if f.is_file())

    # --- reading for the gallery ----------------------------------------
    def list_capture_images(self) -> List[dict]:
        """Camera capture frames only (excludes growth/plant-mask artifacts)."""
        out = []
        for p in sorted(self.path.glob("*.png")):
            name = p.stem
            if name in ("growth", "plant_overlay") or "_" not in name:
                continue
            phase, _, idx = name.rpartition("_")
            try:
                index = int(idx)
            except ValueError:
                continue
            out.append(
                {
                    "id": name,
                    "phase": phase,
                    "index": index,
                    "timestamp": datetime.fromtimestamp(p.stat().st_mtime),
                    "url": f"/api/images/{self.experiment_id}/{name}",
                    "thumbUrl": f"/api/images/{self.experiment_id}/{name}/thumb",
                }
            )
        out.sort(key=lambda d: (d["timestamp"], d["index"]))
        return out

    def list_images(self) -> List[dict]:
        out = self.list_capture_images()
        # Derived artifacts appear at the end of the gallery when present.
        artifacts = [
            ("plant_mask", self.path / "plant_mask.png", "image/png"),
            ("plant_overlay", self.path / "plant_overlay.jpg", "image/jpeg"),
        ]
        for art_id, path, _media in artifacts:
            if not path.is_file():
                continue
            out.append(
                {
                    "id": art_id,
                    "phase": "artifact",
                    "index": 10_000_000,
                    "timestamp": datetime.fromtimestamp(path.stat().st_mtime),
                    "url": f"/api/images/{self.experiment_id}/artifacts/{art_id}",
                    "thumbUrl": f"/api/images/{self.experiment_id}/artifacts/{art_id}/thumb",
                }
            )
        return out

    def artifact_file(self, artifact_id: str) -> Optional[Path]:
        mapping = {
            "plant_mask": self.path / "plant_mask.png",
            "plant_overlay": self.path / "plant_overlay.jpg",
        }
        p = mapping.get(artifact_id)
        return p if p is not None and p.is_file() else None

    def artifact_thumb(self, artifact_id: str) -> Optional[Path]:
        src = self.artifact_file(artifact_id)
        if src is None:
            return None
        thumb = self.path / "thumbs" / f"{artifact_id}.jpg"
        if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
            try:
                img = Image.open(src)
                img.thumbnail((320, 240))
                img.convert("RGB").save(thumb, "JPEG", quality=80)
            except Exception:
                return None
        return thumb

    def image_file(self, image_id: str) -> Optional[Path]:
        p = self.path / f"{_slug(image_id)}.png"
        return p if p.exists() else None

    def thumb_file(self, image_id: str) -> Optional[Path]:
        src = self.image_file(image_id)
        if src is None:
            return None
        thumb = self.path / "thumbs" / f"{_slug(image_id)}.jpg"
        if not thumb.exists() or thumb.stat().st_mtime < src.stat().st_mtime:
            try:
                img = Image.open(src)
                img.thumbnail((320, 240))
                img.convert("RGB").save(thumb, "JPEG", quality=80)
            except Exception:
                return None
        return thumb


class Storage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_experiment(self, username: str, name: str) -> ExperimentDir:
        date = datetime.now().strftime("%Y-%m-%d")
        base = f"{date}_{_slug(username)}_{_slug(name)}"
        candidate = self.root / base
        n = 2
        while candidate.exists():
            candidate = self.root / f"{base}_{n}"
            n += 1
        candidate.mkdir(parents=True)
        return ExperimentDir(candidate)

    def list_experiments(self) -> List[Path]:
        dirs = [p for p in self.root.iterdir() if p.is_dir()] if self.root.exists() else []
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return dirs

    def latest_experiment(self) -> Optional[ExperimentDir]:
        dirs = self.list_experiments()
        return ExperimentDir(dirs[0]) if dirs else None

    def get_experiment(self, experiment_id: str) -> Optional[ExperimentDir]:
        p = self.root / _slug(experiment_id)
        return ExperimentDir(p) if p.is_dir() else None

    def delete_experiment(self, experiment_id: str) -> bool:
        """Remove an experiment folder and all its images. Returns True if deleted."""
        exp = self.get_experiment(experiment_id)
        if exp is None:
            return False
        resolved = exp.path.resolve()
        root = self.root.resolve()
        if resolved == root or root not in resolved.parents:
            raise ValueError(f"refusing to delete path outside storage root: {resolved}")
        shutil.rmtree(resolved)
        return True


@dataclass
class UserTally:
    count: int = 0
    bytes_used: int = 0


def tally_by_user(storage: "Storage", user_defaults_path: Path) -> Dict[str, UserTally]:
    """Per-username experiment count + total bytes, keyed lower-case.

    Shared by GET /api/users (api/users.py) and the assistant's my_storage
    tool -- lives here rather than in api/users.py so the assistant module
    can import it without a circular dependency back through api/deps.py.
    Full re-walk + re-stat of every experiment folder on every call, same
    cost as the original inline version; no caching, callers should treat
    this as O(all files on disk)."""
    tallies: Dict[str, UserTally] = {}
    for d in storage.list_experiments():
        exp = ExperimentDir(d)
        name = (exp.username() or "").strip().lower()
        if not name:
            continue
        tally = tallies.setdefault(name, UserTally())
        tally.count += 1
        tally.bytes_used += exp.size_bytes()
    for key in user_defaults.load_all(user_defaults_path):
        tallies.setdefault(key, UserTally())
    return tallies
