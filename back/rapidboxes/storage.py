"""Filesystem storage: experiment folders, atomic metadata, image + thumbnails.

Layout (flat, easy to resolve from a URL):
    {storage_root}/{YYYY-MM-DD}_{username}_{name}/
        dark_00000.jpg ...
        bending_00000.jpg ...
        thumbs/<image_id>.jpg
        metadata.json
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

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
        return self.path / f"{image_id}.jpg", image_id

    def write_metadata(self, data: dict) -> None:
        """Atomic write so a crash mid-write can't corrupt the file."""
        tmp = self.path / "metadata.json.tmp"
        final = self.path / "metadata.json"
        with tmp.open("w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, final)

    def read_metadata(self) -> Optional[dict]:
        f = self.path / "metadata.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except Exception:
            return None

    # --- reading for the gallery ----------------------------------------
    def list_images(self) -> List[dict]:
        out = []
        for p in sorted(self.path.glob("*.jpg")):
            name = p.stem
            if "_" not in name:
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

    def image_file(self, image_id: str) -> Optional[Path]:
        p = self.path / f"{_slug(image_id)}.jpg"
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
