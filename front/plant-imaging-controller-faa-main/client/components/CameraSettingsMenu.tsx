import { useEffect, useState } from "react";
import { Camera, X } from "lucide-react";
import { toast } from "sonner";
import ParameterControl from "@/components/ParameterControl";
import SegmentedCard from "@/components/SegmentedCard";
import { api } from "@/lib/api";
import {
  EXPOSURE_SLIDER_STEPS,
  exposureToPosition,
  formatExposure,
  positionToExposure,
  stepExposure,
} from "@/lib/exposure";
import { applyZoomStickiness, formatZoom, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP } from "@/lib/zoom";
import type { CameraSettings, PhotoIlluminationSource } from "@shared/api";

const RESOLUTIONS = [
  { label: "Full", width: 4608, height: 2592 },
  { label: "Half", width: 2304, height: 1296 },
  { label: "Quarter", width: 1152, height: 648 },
];

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

interface CameraSettingsMenuProps {
  camera: CameraSettings;
  /** The illumination source shapes the exposure control; it's owned by the
   *  Illumination tab, this panel only reads it. */
  source: PhotoIlluminationSource;
  onChange: (patch: Partial<CameraSettings>) => void;
  locked: boolean;
}

export default function CameraSettingsMenu({ camera, source, onChange, locked }: CameraSettingsMenuProps) {
  const [takingPhoto, setTakingPhoto] = useState(false);
  const [testPhotoUrl, setTestPhotoUrl] = useState<string | null>(null);
  const manualFocusDisabled = camera.autofocusEnabled;

  // Revoke the previous blob URL whenever it's replaced or the menu unmounts.
  useEffect(() => {
    return () => {
      if (testPhotoUrl) URL.revokeObjectURL(testPhotoUrl);
    };
  }, [testPhotoUrl]);

  const handleTestPhoto = async () => {
    if (takingPhoto) return;
    setTakingPhoto(true);
    try {
      const blob = await api.testPhotoWithSettings(camera);
      setTestPhotoUrl(URL.createObjectURL(blob));
    } catch (e) {
      toast.error(`Could not take test photo: ${(e as Error).message}`);
    } finally {
      setTakingPhoto(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-2">
        <div className="grid grid-cols-3 gap-2">
          <div className={locked ? "pointer-events-none opacity-50" : undefined}>
            <SegmentedCard
              label="Resolution"
              footer={`${camera.width}×${camera.height}`}
              options={RESOLUTIONS.map((r) => ({
                key: r.label,
                label: r.label,
                active: camera.width === r.width && camera.height === r.height,
                onClick: () => onChange({ width: r.width, height: r.height }),
              }))}
            />
          </div>

          <div className={locked ? "pointer-events-none opacity-50" : undefined}>
            <SegmentedCard
              label="Color Mode"
              options={[
                {
                  key: "gray",
                  label: "Grayscale",
                  active: camera.grayscale,
                  onClick: () => onChange({ grayscale: true }),
                },
                {
                  key: "color",
                  label: "Color",
                  active: !camera.grayscale,
                  onClick: () => onChange({ grayscale: false }),
                },
              ]}
            />
          </div>

          {/* Range and curve follow the illumination source: IR snaps 0.2–10 s
              in 0.2 s steps; RGBW is a 0.01–0.5 s log sweep. */}
          <ParameterControl
            label={`Exposure — ${source === "ir" ? "IR 0.2–10 s · 0.2 s steps" : "RGBW 10–500 ms"}`}
            value={formatExposure(camera.exposureMicroseconds)}
            valueColor="#2B7FFF"
            sliderColor="#2B7FFF"
            sliderValue={exposureToPosition(source, camera.exposureMicroseconds)}
            sliderMin={0}
            sliderMax={EXPOSURE_SLIDER_STEPS}
            sliderStep={1}
            disabled={locked}
            onSliderChange={(pos) => onChange({ exposureMicroseconds: positionToExposure(source, pos) })}
            onIncrement={() =>
              onChange({ exposureMicroseconds: stepExposure(source, camera.exposureMicroseconds, 10) })
            }
            onDecrement={() =>
              onChange({ exposureMicroseconds: stepExposure(source, camera.exposureMicroseconds, -10) })
            }
          />

          <ParameterControl
            label="ISO"
            value={`${camera.iso}`}
            valueColor="#FF6900"
            sliderColor="#FF6900"
            sliderValue={camera.iso}
            sliderMin={50}
            sliderMax={1600}
            sliderStep={50}
            disabled={locked}
            onSliderChange={(v) => onChange({ iso: v })}
            onIncrement={() => onChange({ iso: clamp(camera.iso + 50, 50, 1600) })}
            onDecrement={() => onChange({ iso: clamp(camera.iso - 50, 50, 1600) })}
          />

          <div className={locked ? "pointer-events-none opacity-50" : undefined}>
            <SegmentedCard
              label="Focus Mode"
              footer={camera.autofocusEnabled ? "Continuous autofocus" : "Manual lens position"}
              options={[
                {
                  key: "auto",
                  label: "Autofocus",
                  active: camera.autofocusEnabled,
                  onClick: () => onChange({ autofocusEnabled: true }),
                },
                {
                  key: "manual",
                  label: "Manual",
                  active: !camera.autofocusEnabled,
                  onClick: () => onChange({ autofocusEnabled: false }),
                },
              ]}
            />
          </div>

          <ParameterControl
            label="Focus Distance"
            value={camera.autofocusEnabled ? "AUTO" : camera.focusDistance.toFixed(1)}
            valueColor={camera.autofocusEnabled ? "#9CA3AF" : "#10B981"}
            sliderColor="#10B981"
            sliderValue={camera.focusDistance}
            sliderMin={0}
            sliderMax={32}
            sliderStep={0.1}
            disabled={locked || manualFocusDisabled}
            onSliderChange={(v) => onChange({ focusDistance: v })}
            onIncrement={() => onChange({ focusDistance: clamp(camera.focusDistance + 0.1, 0, 32) })}
            onDecrement={() => onChange({ focusDistance: clamp(camera.focusDistance - 0.1, 0, 32) })}
          />

          {/* Continuous 1x-5x with a magnetic snap at each integer: drag
              near 3x and it locks to exactly 3x, pull further and it moves
              freely (3.2x, 4.5x, ...). Center-crops every capture and
              scales back to width x height, so framing tightens without
              changing the saved image dimensions. */}
          <ParameterControl
            label="Zoom"
            value={formatZoom(camera.zoom)}
            valueColor="#7BF1A8"
            sliderColor="#7BF1A8"
            sliderValue={camera.zoom}
            sliderMin={ZOOM_MIN}
            sliderMax={ZOOM_MAX}
            sliderStep={ZOOM_STEP}
            disabled={locked}
            onSliderChange={(v) => onChange({ zoom: applyZoomStickiness(v) })}
            onIncrement={() => onChange({ zoom: applyZoomStickiness(camera.zoom + 0.1) })}
            onDecrement={() => onChange({ zoom: applyZoomStickiness(camera.zoom - 0.1) })}
          />

          <div className="col-span-2 flex flex-col justify-center gap-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2">
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Note
            </div>
            <p className="text-[10px] leading-[13px] text-app-text-secondary">
              Autofocus uses continuous tracking. In manual mode, focus distance 0.0 means infinity.
              White balance is fixed and settle time follows exposure automatically — neither needs
              tuning on this sensor.
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
        <button
          onClick={handleTestPhoto}
          disabled={takingPhoto || locked}
          title={
            locked
              ? "Cannot take a test photo while an experiment is running"
              : "Preview a capture at the current camera and illumination settings, including zoom"
          }
          className="flex items-center gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Camera className="h-[16px] w-[16px]" strokeWidth={1.5} />
          <span className="text-[12px] font-bold uppercase tracking-[1px]">
            {takingPhoto ? "Capturing…" : "Test Photo"}
          </span>
        </button>
      </div>

      {testPhotoUrl && (
        <div
          className="fixed inset-0 z-[60] flex flex-col items-center justify-center bg-black/90 p-4"
          onClick={() => setTestPhotoUrl(null)}
        >
          <button
            onClick={() => setTestPhotoUrl(null)}
            className="absolute right-4 top-4 rounded-md bg-app-bg-tertiary p-2 text-white transition-colors hover:bg-app-border-primary"
          >
            <X className="h-[20px] w-[20px]" strokeWidth={1.5} />
          </button>
          <img
            src={testPhotoUrl}
            alt="Test capture"
            className="max-h-[88%] max-w-[92%] rounded-lg object-contain"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
