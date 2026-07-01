"""Tests for structured JSONL logging."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from rapidboxes.config import AppConfig
from rapidboxes.logging import init_logging, journal_step, log_error, log_system, shutdown_logging
from rapidboxes.logging.context import get_run_id, set_experiment_context
from rapidboxes.logging.jsonl import JsonlWriter


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_jsonl_writer_rotation(tmp_path: Path):
    writer = JsonlWriter(tmp_path / "test.jsonl", max_bytes=200, backup_count=2)
    for i in range(30):
        writer.write({"n": i, "payload": "x" * 20})
    assert (tmp_path / "test.jsonl").exists()
    assert (tmp_path / "test.jsonl.1").exists()


def test_init_logging_creates_files(tmp_path: Path):
    cfg = AppConfig(
        simulation=True,
        storage_root=tmp_path / "exp",
        settings_path=tmp_path / "settings.json",
        log_root=tmp_path / "logs",
    )
    cfg.ensure_dirs()
    run_id = init_logging(cfg)
    assert run_id
    assert get_run_id() == run_id

    log_error("test", "sample_error", "something broke", detail="x")
    journal_step("experiment.start", "started", detail="protocol=tropism")
    set_experiment_context("exp-1", protocol="tropism")
    journal_step("phase.dark", "done")
    log_system("test.event", "hello")

    shutdown_logging()

    errors = _read_jsonl(tmp_path / "logs" / "errors.jsonl")
    journal = _read_jsonl(tmp_path / "logs" / "experiment.jsonl")
    system = _read_jsonl(tmp_path / "logs" / "system.jsonl")

    assert any(r["event"] == "sample_error" for r in errors)
    assert errors[0]["software_version"]
    assert errors[0]["where"]["function"]

    assert journal[0]["step_id"] == "experiment.start"
    assert journal[0]["status"] == "started"
    assert journal[-1]["experiment_id"] == "exp-1"

    assert any(r["event"] == "app.startup" for r in system)
    assert any(r["event"] == "app.shutdown" for r in system)


def test_stdlib_errors_mirrored_to_jsonl(tmp_path: Path, caplog):
    cfg = AppConfig(
        simulation=True,
        storage_root=tmp_path / "exp",
        settings_path=tmp_path / "settings.json",
        log_root=tmp_path / "logs",
    )
    init_logging(cfg)
    log = logging.getLogger("rapidboxes.test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("caught failure")
    shutdown_logging()

    errors = _read_jsonl(tmp_path / "logs" / "errors.jsonl")
    mirrored = [r for r in errors if r.get("message") == "caught failure"]
    assert mirrored
    assert mirrored[0]["exception"]["type"] == "RuntimeError"


def test_journal_rejects_invalid_status():
    with pytest.raises(ValueError):
        journal_step("step", "invalid")
