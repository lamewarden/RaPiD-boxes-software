import { useEffect, useState } from "react";
import { Check, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";
import ParameterControl from "@/components/ParameterControl";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import type { CameraSettings, DeviceSettings } from "@shared/api";

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
  grayscale: true,
  awbRedGain: 2.0,
  awbBlueGain: 1.0,
  jpegQuality: 92,
  settleSeconds: 1.0,
};

const RESOLUTIONS = [
  { label: "Full", width: 4608, height: 2592 },
  { label: "Half", width: 2304, height: 1296 },
  { label: "Quarter", width: 1152, height: 648 },
];

const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));

function SegmentedCard({
  label,
  options,
  footer,
}: {
  label: string;
  options: { key: string; label: string; active: boolean; onClick: () => void }[];
  footer?: string;
}) {
  return (
    <div className="flex h-[88px] flex-col justify-between gap-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2">
      <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase">
        {label}
      </div>
      <div className="flex gap-1.5">
        {options.map((o) => (
          <button
            key={o.key}
            onClick={o.onClick}
            className={`flex-1 rounded-md py-1.5 text-[11px] font-bold transition-colors ${
              o.active
                ? "bg-app-green text-white"
                : "bg-app-bg-tertiary text-app-text-secondary hover:bg-app-border-primary"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      {footer && <div className="text-[10px] text-app-text-muted">{footer}</div>}
    </div>
  );
}

export default function CameraSettingsMenu({ onClose }: { onClose: () => void }) {
  const [deviceSettings, setDeviceSettings] = useState<DeviceSettings | null>(null);
  const [camera, setCamera] = useState<CameraSettings>(DEFAULT_CAMERA);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { status } = useExperimentStatus();
  const locked = status?.state === "running" || status?.state === "paused";

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setDeviceSettings(s);
        setCamera(s.camera);
      })
      .catch((e) => toast.error(`Could not load camera settings: ${(e as Error).message}`))
      .finally(() => setLoading(false));
  }, []);

  const patch = (p: Partial<CameraSettings>) => setCamera((c) => ({ ...c, ...p }));

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

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">
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

            <ParameterControl
              label="Exposure"
              value={`${Math.round(camera.exposureMicroseconds / 1000)} ms`}
              valueColor="#2B7FFF"
              sliderColor="#2B7FFF"
              sliderValue={camera.exposureMicroseconds}
              sliderMin={100}
              sliderMax={6_000_000}
              sliderStep={1000}
              onSliderChange={(v) => patch({ exposureMicroseconds: v })}
              onIncrement={() =>
                patch({ exposureMicroseconds: clamp(camera.exposureMicroseconds + 1000, 100, 6_000_000) })
              }
              onDecrement={() =>
                patch({ exposureMicroseconds: clamp(camera.exposureMicroseconds - 1000, 100, 6_000_000) })
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

            <div className="flex h-[88px] flex-col justify-center gap-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2">
              <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                Note
              </div>
              <p className="text-[10px] leading-[13px] text-app-text-secondary">
                Camera settings always reset to default at the start of a new session.
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
        <button
          onClick={() => setCamera(DEFAULT_CAMERA)}
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
    </div>
  );
}
