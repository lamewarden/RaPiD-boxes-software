"""Serialize/parse a SavedExperimentConfig as the per-experiment <name>.xml.

Written once when an experiment starts (see engine/runner.py) and read back by
GET /api/experiments/{id}/config so a past run's phases/light/camera settings
can be reloaded into the setup form. Plain stdlib xml.etree — no new dependency.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import CameraSettings, SavedExperimentConfig

_BOOL = {"true": True, "false": False}


def serialize(config: SavedExperimentConfig) -> bytes:
    root = ET.Element("experimentConfig", version="2", protocol=config.protocol)

    phases = ET.SubElement(root, "phases")

    if config.protocol == "tropism":
        ET.SubElement(
            phases,
            "dark",
            enabled=str(config.darkPhaseEnabled).lower(),
            hours=str(config.darkPhaseHours),
        )
        ET.SubElement(phases, "bending", hours=str(config.lateralIlluminationHours))
    else:
        ET.SubElement(
            phases,
            "growth",
            dayLengthHours=str(config.dayLengthHours),
            experimentLengthDays=str(config.experimentLengthDays),
            photoIlluminationSource=config.photoIlluminationSource,
        )

    light_kwargs = {"intervalMinutes": str(config.intervalMinutes)}
    if config.protocol == "tropism":
        light_kwargs["intensity"] = str(config.intensity)
    else:
        light_kwargs["dayIntensity"] = str(config.dayIntensity)
    light = ET.SubElement(root, "light", **light_kwargs)
    for spectrum in config.spectra:
        ET.SubElement(light, "spectrum").text = spectrum

    cam = config.camera
    ET.SubElement(
        root,
        "camera",
        width=str(cam.width),
        height=str(cam.height),
        exposureMicroseconds=str(cam.exposureMicroseconds),
        iso=str(cam.iso),
        autofocusEnabled=str(cam.autofocusEnabled).lower(),
        focusDistance=str(cam.focusDistance),
        grayscale=str(cam.grayscale).lower(),
        awbRedGain=str(cam.awbRedGain),
        awbBlueGain=str(cam.awbBlueGain),
        jpegQuality=str(cam.jpegQuality),
        settleSeconds=str(cam.settleSeconds),
    )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse(xml_bytes: bytes) -> SavedExperimentConfig:
    root = ET.fromstring(xml_bytes)
    protocol = root.get("protocol") or "tropism"

    phases = root.find("phases")
    dark = phases.find("dark") if phases is not None else None
    bending = phases.find("bending") if phases is not None else None
    growth = phases.find("growth") if phases is not None else None
    light = root.find("light")
    cam_el = root.find("camera")
    defaults = CameraSettings()

    camera = CameraSettings(
        width=int(cam_el.get("width", str(defaults.width))),
        height=int(cam_el.get("height", str(defaults.height))),
        exposureMicroseconds=int(cam_el.get("exposureMicroseconds", str(defaults.exposureMicroseconds))),
        iso=int(cam_el.get("iso", str(defaults.iso))),
        autofocusEnabled=_BOOL.get(
            cam_el.get("autofocusEnabled", str(defaults.autofocusEnabled).lower()),
            defaults.autofocusEnabled,
        ),
        focusDistance=float(cam_el.get("focusDistance", str(defaults.focusDistance))),
        grayscale=_BOOL.get(cam_el.get("grayscale", str(defaults.grayscale).lower()), defaults.grayscale),
        awbRedGain=float(cam_el.get("awbRedGain", str(defaults.awbRedGain))),
        awbBlueGain=float(cam_el.get("awbBlueGain", str(defaults.awbBlueGain))),
        jpegQuality=int(cam_el.get("jpegQuality", str(defaults.jpegQuality))),
        settleSeconds=float(cam_el.get("settleSeconds", str(defaults.settleSeconds))),
    )

    spectra = [el.text for el in light.findall("spectrum") if el.text] if light is not None else ["white"]
    interval = float(light.get("intervalMinutes", "20.0")) if light is not None else 20.0

    if protocol == "growth" or growth is not None:
        source = "ir"
        if growth is not None:
            source = growth.get("photoIlluminationSource", "ir")
        return SavedExperimentConfig(
            protocol="growth",
            spectra=spectra,
            intervalMinutes=interval,
            dayLengthHours=int(growth.get("dayLengthHours", "16")) if growth is not None else 16,
            experimentLengthDays=int(growth.get("experimentLengthDays", "14")) if growth is not None else 14,
            dayIntensity=int(
                light.get("dayIntensity") or light.get("intensity") or "25"
            ) if light is not None else 25,
            photoIlluminationSource=source,
            camera=camera,
        )

    return SavedExperimentConfig(
        protocol="tropism",
        darkPhaseEnabled=_BOOL.get(dark.get("enabled", "true"), True) if dark is not None else True,
        darkPhaseHours=float(dark.get("hours", "90.0")) if dark is not None else 90.0,
        lateralIlluminationHours=float(bending.get("hours", "20.0")) if bending is not None else 20.0,
        spectra=spectra,
        intervalMinutes=interval,
        intensity=int(light.get("intensity", "25")) if light is not None else 25,
        camera=camera,
    )
