import { useEffect, useState } from "react";
import { Camera, Check, RotateCcw, X, ZoomIn } from "lucide-react";
import { toast } from "sonner";
import ParameterControl from "@/components/ParameterControl";
import SegmentedCard from "@/components/SegmentedCard";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import {
  EXPOSURE_SLIDER_STEPS,
  exposureToPosition,
  formatExposure,
  positionToExposure,
  stepExposure,
} from "@/lib/exposure";
import {
  EXPOSURE_PROFILES,
  type CameraSettings,
  type DeviceSettings,
  type PhotoIlluminationSource,
} from "@shared/api";

/**
 * The "standard" camera settings. Mirrors the pydantic field defaults in
 * back/rapidboxes/models.py CameraSettings — keep the two in sync.
 * The backend also resets the persisted camera settings to these values at
 * every process start, so in-session tweaks never carry over to a new session.
 */
const DEFAULT_CAMERA: CameraSettings = {
  width: 2304,
  height: 1296,
  exposureMicroseconds: 100_000,
  iso: 100,
  autofocusEnabled: true,
  focusDistance: 0.0,
  grayscale: true,
  awbRedGain: 2.0,
  awbBlueGain: 1.0,
  jpegQuality: 92,
  settleSeconds: 1.0,
};

/** Defaults for the active light source — exposure is tied to it, so a reset
 *  under IR must land on the IR default, not the flash-speed one. */
const defaultCameraFor = (source: PhotoIlluminationSource): CameraSettings => ({
  ...DEFAULT_CAMERA,
  exposureMicroseconds: EXPOSURE_PROFILES[source].default,
});

const RESOLUTIONS = [
  { label: "Full", width: 4608, height: 2592 },
  { label: "Half", width: 2304, height: 1296 },
  { label: "Quarter", width: 1152, height: 648 },
];

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

interface CameraSettingsMenuProps {
  onClose?: () => void;
  embedded?: boolean;
}

export default function CameraSettingsMenu({ onClose, embedded = false }: CameraSettingsMenuProps) {
  const [deviceSettings, setDeviceSettings] = useState<DeviceSettings | null>(null);
  const [camera, setCamera] = useState<CameraSettings>(DEFAULT_CAMERA);
  // The illumination source shapes the exposure control; it is owned by the
  // Illumination tab, so this panel only reads it.
  const [source, setSource] = useState<PhotoIlluminationSource>("ir");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [takingPhoto, setTakingPhoto] = useState(false);
  const [testPhotoUrl, setTestPhotoUrl] = useState<string | null>(null);
  const [testZoom, setTestZoom] = useState<1 | 2>(1);
  const { status } = useExperimentStatus();
  const locked = status?.state === "running" || status?.state === "paused";
  const manualFocusDisabled = camera.autofocusEnabled;

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setDeviceSettings(s);
        setCamera(s.camera);
        setSource(s.photoIlluminationSource);
      })
      .catch((e) => toast.error(`Could not load camera settings: ${(e as Error).message}`))
      .finally(() => setLoading(false));
  }, []);

  // Revoke the previous blob URL whenever it's replaced or the menu unmounts.
  useEffect(() => {
    return () => {
      if (testPhotoUrl) URL.revokeObjectURL(testPhotoUrl);
    };
  }, [testPhotoUrl]);

  const patch = (p: Partial<CameraSettings>) => setCamera((c) => ({ ...c, ...p }));

  const handleTestPhoto = async (zoom: 1 | 2) => {
    if (takingPhoto) return;
    setTakingPhoto(true);
    try {
      const blob = await api.testPhotoWithSettings(camera);
      setTestPhotoUrl(URL.createObjectURL(blob));
      setTestZoom(zoom);
    } catch (e) {
      toast.error(`Could not take test photo: ${(e as Error).message}`);
    } finally {
      setTakingPhoto(false);
    }
  };

  const handleSave = async () => {
    if (!deviceSettings) return;
    setSaving(true);
    try {
      const saved = await api.saveSettings({ ...deviceSettings, camera });
      setDeviceSettings(saved);
      setCamera(saved.camera);
      toast.success("Camera settings saved.");
    } catch (e) {
      toast.error(`Could not save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const content = (
    <>
      {!embedded && (
        <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
          <span className="text-[15px] font-bold uppercase tracking-wide text-white">
            Camera Settings
          </span>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
          >
            <X className="h-[18px] w-[18px]" strokeWidth={1.5} />
          </button>
        </div>
      )}

      {locked && (
        <div className="border-b border-app-border-primary bg-app-orange/20 px-3 py-1.5 text-[11px] font-semibold text-app-orange-light">
          An experiment is running — settings are read-only until it finishes.
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-app-text-muted">
            Loading…
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            <SegmentedCard
              label="Resolution"
              footer={`${camera.width}×${camera.height}`}
              options={RESOLUTIONS.map((r) => ({
                key: r.label,
                label: r.label,
                active: camera.width === r.width && camera.height === r.height,
                onClick: () => patch({ width: r.width, height: r.height }),
              }))}
            />

            <SegmentedCard
              label="Color Mode"
              options={[
                {
                  key: "gray",
                  label: "Grayscale",
                  active: camera.grayscale,
                  onClick: () => patch({ grayscale: true }),
                },
                {
                  key: "color",
                  label: "Color",
                  active: !camera.grayscale,
                  onClick: () => patch({ grayscale: false }),
                },
              ]}
            />

            <ParameterControl
              label="JPEG Quality"
              value={`${camera.jpegQuality}%`}
              valueColor="#F0B100"
              sliderColor="#F0B100"
              sliderValue={camera.jpegQuality}
              sliderMin={40}
              sliderMax={100}
              sliderStep={1}
              onSliderChange={(v) => patch({ jpegQuality: v })}
              onIncrement={() => patch({ jpegQuality: clamp(camera.jpegQuality + 1, 40, 100) })}
              onDecrement={() => patch({ jpegQuality: clamp(camera.jpegQuality - 1, 40, 100) })}
            />

            {/* Range and curve follow the illumination source: a 1-10s linear
                sweep for IR, a 0.01-0.5s log sweep for the RGBW flash. */}
            <ParameterControl
              label={`Exposure — ${source === "ir" ? "IR 1–10 s" : "RGBW 10–500 ms"}`}
              value={formatExposure(camera.exposureMicroseconds)}
              valueColor="#2B7FFF"
              sliderColor="#2B7FFF"
              sliderValue={exposureToPosition(source, camera.exposureMicroseconds)}
              sliderMin={0}
              sliderMax={EXPOSURE_SLIDER_STEPS}
              sliderStep={1}
              onSliderChange={(pos) => patch({ exposureMicroseconds: positionToExposure(source, pos) })}
              onIncrement={() =>
                patch({ exposureMicroseconds: stepExposure(source, camera.exposureMicroseconds, 10) })
              }
              onDecrement={() =>
                patch({ exposureMicroseconds: stepExposure(source, camera.exposureMicroseconds, -10) })
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
              onSliderChange={(v) => patch({ iso: v })}
              onIncrement={() => patch({ iso: clamp(camera.iso + 50, 50, 1600) })}
              onDecrement={() => patch({ iso: clamp(camera.iso - 50, 50, 1600) })}
            />

            <SegmentedCard
              label="Focus Mode"
              footer={camera.autofocusEnabled ? "Continuous autofocus" : "Manual lens position"}
              options={[
                {
                  key: "auto",
                  label: "Autofocus",
                  active: camera.autofocusEnabled,
                  onClick: () => patch({ autofocusEnabled: true }),
                },
                {
                  key: "manual",
                  label: "Manual",
                  active: !camera.autofocusEnabled,
                  onClick: () => patch({ autofocusEnabled: false }),
                },
              ]}
            />

            <ParameterControl
              label="Focus Distance"
              value={camera.autofocusEnabled ? "AUTO" : camera.focusDistance.toFixed(1)}
              valueColor={camera.autofocusEnabled ? "#9CA3AF" : "#10B981"}
              sliderColor="#10B981"
              sliderValue={camera.focusDistance}
              sliderMin={0}
              sliderMax={32}
              sliderStep={0.1}
              disabled={manualFocusDisabled}
              onSliderChange={(v) => patch({ focusDistance: v })}
              onIncrement={() => patch({ focusDistance: clamp(camera.focusDistance + 0.1, 0, 32) })}
              onDecrement={() => patch({ focusDistance: clamp(camera.focusDistance - 0.1, 0, 32) })}
            />

            <ParameterControl
              label="Settle Time"
              value={`${camera.settleSeconds.toFixed(1)}s`}
              valueColor="#C27AFF"
              sliderColor="#C27AFF"
              sliderValue={camera.settleSeconds}
              sliderMin={0}
              sliderMax={30}
              sliderStep={0.5}
              onSliderChange={(v) => patch({ settleSeconds: v })}
              onIncrement={() => patch({ settleSeconds: clamp(camera.settleSeconds + 0.5, 0, 30) })}
              onDecrement={() => patch({ settleSeconds: clamp(camera.settleSeconds - 0.5, 0, 30) })}
            />

            <ParameterControl
              label="AWB Red Gain"
              value={camera.awbRedGain.toFixed(1)}
              valueColor="#FB2C36"
              sliderColor="#FB2C36"
              sliderValue={camera.awbRedGain}
              sliderMin={0}
              sliderMax={8}
              sliderStep={0.1}
              onSliderChange={(v) => patch({ awbRedGain: v })}
              onIncrement={() => patch({ awbRedGain: clamp(camera.awbRedGain + 0.1, 0, 8) })}
              onDecrement={() => patch({ awbRedGain: clamp(camera.awbRedGain - 0.1, 0, 8) })}
            />

            <ParameterControl
              label="AWB Blue Gain"
              value={camera.awbBlueGain.toFixed(1)}
              valueColor="#51A2FF"
              sliderColor="#51A2FF"
              sliderValue={camera.awbBlueGain}
              sliderMin={0}
              sliderMax={8}
              sliderStep={0.1}
              onSliderChange={(v) => patch({ awbBlueGain: v })}
              onIncrement={() => patch({ awbBlueGain: clamp(camera.awbBlueGain + 0.1, 0, 8) })}
              onDecrement={() => patch({ awbBlueGain: clamp(camera.awbBlueGain - 0.1, 0, 8) })}
            />

            <div className="flex h-[74px] flex-col justify-between gap-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2">
              <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                Note
              </div>
              <p className="text-[10px] leading-[13px] text-app-text-secondary">
                Autofocus uses continuous tracking. In manual mode, focus distance 0.0 means infinity.
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
        <div className="flex overflow-hidden rounded-[10px] border border-app-border-primary">
          <button
            onClick={() => handleTestPhoto(1)}
            disabled={loading || takingPhoto || locked}
            title={locked ? "Cannot take a test photo while an experiment is running" : undefined}
            className="flex items-center gap-2 bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Camera className="h-[16px] w-[16px]" strokeWidth={1.5} />
            <span className="text-[12px] font-bold uppercase tracking-[1px]">
              {takingPhoto ? "Capturing…" : "Test Photo"}
            </span>
          </button>
          <div className="w-px bg-app-border-primary" />
          <button
            onClick={() => handleTestPhoto(2)}
            disabled={loading || takingPhoto || locked}
            title={
              locked
                ? "Cannot take a test photo while an experiment is running"
                : "Take a test photo and view it zoomed in 2x"
            }
            className="flex items-center gap-1 bg-app-bg-tertiary px-3 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ZoomIn className="h-[14px] w-[14px]" strokeWidth={1.5} />
            <span className="text-[12px] font-black uppercase tracking-[1px]">2x</span>
          </button>
        </div>

        <button
          onClick={() => setCamera(defaultCameraFor(source))}
          disabled={loading}
          className="flex items-center gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw className="h-[16px] w-[16px]" strokeWidth={1.5} />
          <span className="text-[12px] font-bold uppercase tracking-[1px]">Default</span>
        </button>

        <div className="flex-1" />

        <button
          onClick={handleSave}
          disabled={loading || saving || locked}
          title={locked ? "Cannot change settings while an experiment is running" : undefined}
          className="flex items-center gap-2 rounded-[10px] bg-app-green px-6 py-2 text-white transition-colors hover:bg-app-green-light disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Check className="h-[16px] w-[16px]" strokeWidth={1.5} />
          <span className="text-[12px] font-black uppercase tracking-[1.4px]">
            {saving ? "Saving…" : "Save"}
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
          {testZoom === 2 ? (
            <div className="h-full w-full overflow-auto" onClick={(e) => e.stopPropagation()}>
              <img
                src={testPhotoUrl}
                alt="Test capture, zoomed 2x"
                className="mx-auto"
                style={{ width: "200%", maxWidth: "none" }}
              />
            </div>
          ) : (
            <img
              src={testPhotoUrl}
              alt="Test capture"
              className="max-h-[88%] max-w-[92%] rounded-lg object-contain"
              onClick={(e) => e.stopPropagation()}
            />
          )}
        </div>
      )}
    </>
  );

  if (embedded) {
    return <div className="flex h-full flex-col">{content}</div>;
  }

  return <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">{content}</div>;
}
