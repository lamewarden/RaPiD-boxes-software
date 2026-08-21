#!/usr/bin/env python3
"""Diagnose Live-view freezes when switching IR / RGBW backlight.

Measures:
  1. Current camera exposure vs actual /api/preview/frame.jpg latency
  2. Backlight switch latency (off ↔ white ↔ ir) while idle
  3. Same switches while concurrent frame polling mimics the Live UI (250 ms)
  4. Raspberry Pi undervoltage / throttle flags around each illumination edge
  5. Whether power brown-out (throttled bits) correlates with freezes

Usage (on the Pi, service running):
  python3 deploy/check_live_backlight_freeze.py
  python3 deploy/check_live_backlight_freeze.py --base http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

THROTTLE_BITS = {
    0: "under-voltage now",
    1: "arm freq capped now",
    2: "throttled now",
    3: "soft temp limit now",
    16: "under-voltage occurred",
    17: "arm freq capped occurred",
    18: "throttled occurred",
    19: "soft temp limit occurred",
}


def http_json(url: str, method: str = "GET", body: Optional[dict] = None, timeout: float = 60.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return resp.status, None
        try:
            return resp.status, json.loads(raw.decode())
        except json.JSONDecodeError:
            return resp.status, raw


def http_bytes(url: str, timeout: float = 60.0) -> Tuple[int, bytes, float]:
    t0 = time.perf_counter()
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        return resp.status, data, time.perf_counter() - t0


def vcgencmd(cmd: str) -> str:
    try:
        out = subprocess.check_output(["vcgencmd", *cmd.split()], text=True, stderr=subprocess.DEVNULL)
        return out.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def read_throttled() -> int:
    raw = vcgencmd("get_throttled")
    if not raw or "=" not in raw:
        return 0
    return int(raw.split("=", 1)[1], 0)


def decode_throttled(value: int) -> List[str]:
    return [name for bit, name in THROTTLE_BITS.items() if value & (1 << bit)]


def read_volts() -> str:
    return vcgencmd("measure_volts") or "n/a"


def read_temp() -> str:
    return vcgencmd("measure_temp") or "n/a"


@dataclass
class Sample:
    label: str
    elapsed_s: float
    ok: bool
    detail: str = ""
    throttled_before: int = 0
    throttled_after: int = 0
    volts_before: str = ""
    volts_after: str = ""


@dataclass
class Report:
    samples: List[Sample] = field(default_factory=list)
    frame_times_s: List[float] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ConcurrentPoller:
    """Mimic Live UI pressure on the hardware lock.

    CameraLive.tsx retargets <img src> every 250ms. Browsers usually keep only a
    couple of GETs alive, so we cap in-flight requests (default 2) while still
    issuing on the 250ms cadence — enough to prove queueing without DoS'ing the
    single HardwareManager lock with dozens of 3.6s captures.
    """

    def __init__(self, base: str, interval_s: float = 0.25, max_in_flight: int = 2):
        self.base = base.rstrip("/")
        self.interval_s = interval_s
        self.max_allowed = max_in_flight
        self._stop = threading.Event()
        self.latencies: List[float] = []
        self.errors = 0
        self.skipped = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._workers: List[threading.Thread] = []

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="preview-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        deadline = time.time() + 120
        while self.in_flight and time.time() < deadline:
            time.sleep(0.05)
        for t in self._workers:
            t.join(timeout=1)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                busy = self.in_flight >= self.max_allowed
            if busy:
                self.skipped += 1
            else:
                t = threading.Thread(target=self._one_frame, daemon=True)
                self._workers.append(t)
                t.start()
            self._stop.wait(self.interval_s)

    def _one_frame(self) -> None:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            _, _, elapsed = http_bytes(
                f"{self.base}/api/preview/frame.jpg?ts={time.time_ns()}",
                timeout=90.0,
            )
            with self._lock:
                self.latencies.append(elapsed)
        except Exception:
            with self._lock:
                self.errors += 1
        finally:
            with self._lock:
                self.in_flight -= 1


def timed_backlight(base: str, mode: str, timeout: float = 90.0) -> Sample:
    url = f"{base.rstrip('/')}/api/preview/backlight"
    thr0 = read_throttled()
    v0 = read_volts()
    t0 = time.perf_counter()
    ok = True
    detail = ""
    try:
        status, payload = http_json(url, method="POST", body={"mode": mode}, timeout=timeout)
        detail = f"http={status} body={payload}"
        if status >= 400:
            ok = False
    except Exception as exc:
        ok = False
        detail = f"error={exc}"
    elapsed = time.perf_counter() - t0
    # brief settle so sticky throttle bits can latch if a spike happened
    time.sleep(0.15)
    thr1 = read_throttled()
    v1 = read_volts()
    return Sample(
        label=f"backlight→{mode}",
        elapsed_s=elapsed,
        ok=ok,
        detail=detail,
        throttled_before=thr0,
        throttled_after=thr1,
        volts_before=v0,
        volts_after=v1,
    )


def measure_frames(base: str, n: int = 3) -> List[float]:
    times: List[float] = []
    for _ in range(n):
        try:
            _, data, elapsed = http_bytes(
                f"{base.rstrip('/')}/api/preview/frame.jpg?ts={time.time_ns()}",
                timeout=90.0,
            )
            if len(data) < 100:
                raise RuntimeError(f"tiny jpeg ({len(data)} B)")
            times.append(elapsed)
        except Exception as exc:
            raise RuntimeError(f"frame grab failed: {exc}") from exc
    return times


def fmt_stats(xs: List[float]) -> str:
    if not xs:
        return "n/a"
    return (
        f"n={len(xs)} min={min(xs):.3f}s median={statistics.median(xs):.3f}s "
        f"max={max(xs):.3f}s mean={statistics.mean(xs):.3f}s"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--poll-seconds", type=float, default=8.0, help="how long to poll under load")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    report = Report()

    print("=== RaPiD-boxes live backlight freeze check ===")
    print(f"base={base}")
    print(f"host temp={read_temp()}  core={read_volts()}  throttled=0x{read_throttled():x} ({decode_throttled(read_throttled()) or ['none']})")
    print()

    # --- settings / expected exposure ---
    try:
        _, settings = http_json(f"{base}/api/settings")
        assert isinstance(settings, dict)
        cam = settings.get("camera") or {}
        exp_us = int(cam.get("exposureMicroseconds") or 0)
        source = settings.get("photoIlluminationSource")
        print(f"settings.photoIlluminationSource = {source}")
        print(f"settings.camera.exposureMicroseconds = {exp_us} ({exp_us/1e6:.3f}s)")
        if exp_us >= 500_000:
            report.notes.append(
                f"Camera exposure is {exp_us/1e6:.2f}s — each Live frame holds the hardware "
                f"lock that long; UI polls every 250ms so backlog looks like a freeze."
            )
    except Exception as exc:
        report.errors.append(f"settings: {exc}")
        print(f"ERROR reading settings: {exc}")
        settings = {}

    try:
        _, system = http_json(f"{base}/api/system")
        print(f"system.cameraAvailable = {system.get('cameraAvailable') if isinstance(system, dict) else system}")
    except Exception as exc:
        report.errors.append(f"system: {exc}")

    # Ensure lights start off
    try:
        timed_backlight(base, "off")
    except Exception as exc:
        report.errors.append(f"initial off: {exc}")

    print("\n--- 1) Baseline frame latency (lights off, sequential) ---")
    try:
        times = measure_frames(base, n=3)
        report.frame_times_s.extend(times)
        print(fmt_stats(times))
        for i, t in enumerate(times, 1):
            print(f"  frame {i}: {t:.3f}s")
    except Exception as exc:
        report.errors.append(str(exc))
        print(f"ERROR: {exc}")
        times = []

    print("\n--- 2) Backlight switches while idle (no concurrent poll) ---")
    for mode in ("white", "off", "white", "off"):
        s = timed_backlight(base, mode)
        report.samples.append(s)
        flags = decode_throttled(s.throttled_after)
        new_bits = s.throttled_after & ~s.throttled_before
        print(
            f"  {s.label:16s}  {s.elapsed_s:7.3f}s  ok={s.ok}  "
            f"throttled 0x{s.throttled_before:x}→0x{s.throttled_after:x}  "
            f"volts {s.volts_before}→{s.volts_after}"
            + (f"  NEW_FLAGS={decode_throttled(new_bits)}" if new_bits else "")
        )
        if not s.ok:
            report.errors.append(f"{s.label}: {s.detail}")

    print("\n--- 3) Concurrent Live-style poll + backlight switches ---")
    poller = ConcurrentPoller(base, interval_s=0.25)
    poller.start()
    time.sleep(1.0)  # let a few frames queue
    load_samples: List[Sample] = []
    for mode in ("white", "off", "white", "off"):
        s = timed_backlight(base, mode)
        load_samples.append(s)
        report.samples.append(s)
        new_bits = s.throttled_after & ~s.throttled_before
        print(
            f"  {s.label:16s}  {s.elapsed_s:7.3f}s  ok={s.ok}  "
            f"throttled 0x{s.throttled_before:x}→0x{s.throttled_after:x}"
            + (f"  NEW_FLAGS={decode_throttled(new_bits)}" if new_bits else "")
        )
        time.sleep(0.5)
    # keep polling a bit after last switch
    time.sleep(max(0.0, args.poll_seconds - 1.0 - 4 * 0.5))
    poller.stop()
    # ensure off
    timed_backlight(base, "off")

    print(
        f"  poller frames completed: {len(poller.latencies)}  "
        f"errors={poller.errors}  skipped_ticks={poller.skipped}"
    )
    print(f"  poller max_in_flight:    {poller.max_in_flight}")
    print(f"  poller latency:          {fmt_stats(poller.latencies)}")
    if poller.max_in_flight >= 2:
        report.notes.append(
            f"Live-style poller held {poller.max_in_flight} in-flight frame request(s) "
            f"and skipped {poller.skipped} poll ticks — backlight shares the same lock."
        )

    idle = report.samples[:4]
    idle_ms = [s.elapsed_s for s in idle if s.ok]
    load_ms = [s.elapsed_s for s in load_samples if s.ok]

    print("\n--- 4) Power / undervoltage summary ---")
    final_thr = read_throttled()
    flags = decode_throttled(final_thr)
    print(f"  final throttled=0x{final_thr:x}  flags={flags or ['none']}")
    print(f"  final {read_volts()}  {read_temp()}")
    any_uv = any(
        (s.throttled_after | s.throttled_before) & ((1 << 0) | (1 << 16))
        for s in report.samples
    )
    any_throttle = any(
        (s.throttled_after | s.throttled_before) & ((1 << 2) | (1 << 18))
        for s in report.samples
    )
    if any_uv:
        report.notes.append(
            "Undervoltage flag seen during/after backlight edges — current spike / weak PSU "
            "is a plausible contributor to camera or SoC stalls."
        )
    else:
        report.notes.append(
            "No undervoltage/throttled sticky bits latched during backlight switching — "
            "a pure current-spike brown-out is unlikely for this run."
        )

    print("\n=== VERDICT ===")
    med_frame = statistics.median(times) if times else None
    if med_frame is not None and med_frame >= 1.0:
        print(
            f"PRIMARY: software stall — median frame {med_frame:.2f}s "
            f"(matches long IR exposure). Live UI polls 4×/s → backlog + frozen image."
        )
    elif med_frame is not None:
        print(f"Frame latency looks healthy (median {med_frame:.3f}s).")

    if idle_ms and load_ms:
        print(
            f"Backlight latency idle median={statistics.median(idle_ms):.3f}s  "
            f"vs under Live poll median={statistics.median(load_ms):.3f}s"
        )
        if statistics.median(load_ms) > max(0.5, 3 * statistics.median(idle_ms) + 0.2):
            print(
                "PRIMARY amplifier: HardwareManager lock — backlight waits for in-flight "
                "capture_jpeg while Live keeps stacking requests."
            )

    if any_uv or any_throttle:
        print("SECONDARY: power events detected (see notes). Check 5V supply for strip/IR.")
    else:
        print("Power: no Pi undervoltage/throttle evidence during this check.")

    print("\nNotes:")
    for n in report.notes:
        print(f"  • {n}")
    if report.errors:
        print("\nErrors:")
        for e in report.errors:
            print(f"  • {e}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        raise SystemExit(130)
    except urllib.error.URLError as exc:
        print(f"Cannot reach API: {exc}", file=sys.stderr)
        raise SystemExit(2)
