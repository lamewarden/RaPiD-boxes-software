"""Serialize/parse a SavedExperimentConfig as the per-experiment <name>.xml.

Written once when an experiment starts (see engine/runner.py) and read back by
GET /api/experiments/{id}/config so a past run's phases/light/camera settings
can be reloaded into the setup form. Plain stdlib xml.etree — no new dependency.

The file holds a complete snapshot of DeviceSettings, so importing a past run
restores exactly the device configuration its images were taken with.

Schema history, all still readable — parse() falls back to defaults (or, for
the illumination source, to <growth>'s legacy attribute) whenever a newer
element is missing from an older file:

  v2  `photoIlluminationSource` lived on the growth-only <growth> element.
  v3  moved it to a shared <illumination> element (it applies to Tropism dark
      captures too) and added a <leds> snapshot.
  v4  added an <ir> snapshot, completing the DeviceSettings coverage.
  v5  dropped <camera awbRedGain/awbBlueGain/settleSeconds> -- AWB is now
      fixed, settle is derived from exposure, neither is user-tunable -- and
      added <camera zoom>. Older files' awb/settle attributes are simply
      ignored; a missing zoom defaults to 1.0 (no crop).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import CameraSettings, IrSettings, LedSettings, SavedExperimentConfig

_BOOL = {"true": True, "false": False}


def serialize(config: SavedExperimentConfig) -> bytes:
    root = ET.Element("experimentConfig", version="5", protocol=config.protocol)

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
        )

    light_kwargs = {"intervalMinutes": str(config.intervalMinutes)}
    if config.protocol == "tropism":
        light_kwargs["intensity"] = str(config.intensity)
    else:
        light_kwargs["dayIntensity"] = str(config.dayIntensity)
    light = ET.SubElement(root, "light", **light_kwargs)
    for spectrum in config.spectra:
        ET.SubElement(light, "spectrum").text = spectrum

    ET.SubElement(root, "illumination", source=config.photoIlluminationSource)

    ET.SubElement(root, "ir", pins=",".join(str(p) for p in config.ir.pins))

    leds = config.leds
    ET.SubElement(
        root,
        "leds",
        pixelCount=str(leds.pixelCount),
        pixelOrder=leds.pixelOrder,
        topSegment=f"{leds.topSegment[0]},{leds.topSegment[1]}",
        lateralSegment=f"{leds.lateralSegment[0]},{leds.lateralSegment[1]}",
        spiHz=str(leds.spiHz),
        stride=str(leds.stride),
    )

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
        jpegQuality=str(cam.jpegQuality),
        zoom=str(cam.zoom),
    )

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _parse_segment(text: str, fallback: tuple) -> tuple:
    try:
        start, end = text.split(",")
        return (int(start), int(end))
    except (ValueError, AttributeError):
        return fallback


def _parse_leds(root: ET.Element) -> LedSettings:
    defaults = LedSettings()
    el = root.find("leds")
    if el is None:
        return defaults
    return LedSettings(
        pixelCount=int(el.get("pixelCount", str(defaults.pixelCount))),
        pixelOrder=el.get("pixelOrder", defaults.pixelOrder),
        topSegment=_parse_segment(el.get("topSegment", ""), defaults.topSegment),
        lateralSegment=_parse_segment(el.get("lateralSegment", ""), defaults.lateralSegment),
        spiHz=int(el.get("spiHz", str(defaults.spiHz))),
        stride=int(el.get("stride", str(defaults.stride))),
    )


def _parse_ir(root: ET.Element) -> IrSettings:
    defaults = IrSettings()
    el = root.find("ir")
    if el is None:
        return defaults
    try:
        pins = [int(p) for p in el.get("pins", "").split(",") if p.strip()]
    except ValueError:
        return defaults
    return IrSettings(pins=pins or defaults.pins)


def _parse_illumination_source(root: ET.Element, growth: ET.Element | None) -> str:
    """Newer <illumination source=...> element, falling back to the legacy
    per-growth attribute (v2 files) and finally the "ir" default."""
    el = root.find("illumination")
    if el is not None:
        return el.get("source", "ir")
    if growth is not None:
        return growth.get("photoIlluminationSource", "ir")
    return "ir"


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
        jpegQuality=int(cam_el.get("jpegQuality", str(defaults.jpegQuality))),
        zoom=float(cam_el.get("zoom", str(defaults.zoom))),
    )

    spectra = [el.text for el in light.findall("spectrum") if el.text] if light is not None else ["white"]
    interval = float(light.get("intervalMinutes", "20.0")) if light is not None else 20.0
    source = _parse_illumination_source(root, growth)
    leds = _parse_leds(root)
    ir = _parse_ir(root)

    if protocol == "growth" or growth is not None:
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
            leds=leds,
            ir=ir,
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
        photoIlluminationSource=source,
        leds=leds,
        ir=ir,
        camera=camera,
    )
