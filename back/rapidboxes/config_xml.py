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
    root = ET.Element("experimentConfig", version="1")

    phases = ET.SubElement(root, "phases")
    ET.SubElement(
        phases,
        "preIllumination",
        enabled=str(config.preIlluminationEnabled).lower(),
        hours=str(config.preIlluminationHours),
    )
    ET.SubElement(
        phases,
        "dark",
        enabled=str(config.darkPhaseEnabled).lower(),
        hours=str(config.darkPhaseHours),
    )
    ET.SubElement(phases, "bending", hours=str(config.lateralIlluminationHours))

    light = ET.SubElement(
        root,
        "light",
        intervalMinutes=str(config.intervalMinutes),
        intensity=str(config.intensity),
    )
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
        grayscale=str(cam.grayscale).lower(),
        awbRedGain=str(cam.awbRedGain),
        awbBlueGain=str(cam.awbBlueGain),
        jpegQuality=str(cam.jpegQuality),
        settleSeconds=str(cam.settleSeconds),
    )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse(xml_bytes: bytes) -> SavedExperimentConfig:
    root = ET.fromstring(xml_bytes)

    phases = root.find("phases")
    pre = phases.find("preIllumination")
    dark = phases.find("dark")
    bending = phases.find("bending")
    light = root.find("light")
    cam_el = root.find("camera")

    camera = CameraSettings(
        width=int(cam_el.get("width")),
        height=int(cam_el.get("height")),
        exposureMicroseconds=int(cam_el.get("exposureMicroseconds")),
        iso=int(cam_el.get("iso")),
        grayscale=_BOOL[cam_el.get("grayscale")],
        awbRedGain=float(cam_el.get("awbRedGain")),
        awbBlueGain=float(cam_el.get("awbBlueGain")),
        jpegQuality=int(cam_el.get("jpegQuality")),
        settleSeconds=float(cam_el.get("settleSeconds")),
    )

    return SavedExperimentConfig(
        preIlluminationEnabled=_BOOL[pre.get("enabled")],
        preIlluminationHours=float(pre.get("hours")),
        darkPhaseEnabled=_BOOL[dark.get("enabled")],
        darkPhaseHours=float(dark.get("hours")),
        lateralIlluminationHours=float(bending.get("hours")),
        spectra=[el.text for el in light.findall("spectrum") if el.text],
        intervalMinutes=float(light.get("intervalMinutes")),
        intensity=int(light.get("intensity")),
        camera=camera,
    )
