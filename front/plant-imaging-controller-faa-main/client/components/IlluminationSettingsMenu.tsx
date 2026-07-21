import { useEffect, useState } from "react";
import { Check, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import SegmentedCard from "@/components/SegmentedCard";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import { formatExposure } from "@/lib/exposure";
import {
  EXPOSURE_PROFILES,
  type DeviceSettings,
  type LedSettings,
  type PhotoIlluminationSource,
} from "@shared/api";

const DEFAULT_LEDS: LedSettings = {
  pixelCount: 70,
  pixelOrder: "GRBW",
  topSegment: [22, 64],
  lateralSegment: [0, 21],
  spiHz: 6_400_000,
  stride: 1,
};
const DEFAULT_IR_PINS: [number, number] = [26, 23];
const DEFAULT_SOURCE: PhotoIlluminationSource = "ir";

const STRIDE_OPTIONS: { key: string; label: string; value: number }[] = [
  { key: "1", label: "All", value: 1 },
  { key: "2", label: "1/2", value: 2 },
  { key: "3", label: "1/3", value: 3 },
  { key: "4", label: "1/4", value: 4 },
  { key: "5", label: "1/5", value: 5 },
];

function ordinal(n: number): string {
  if (n === 2) return "2nd";
  if (n === 3) return "3rd";
  return `${n}th`;
}

export default function IlluminationSettingsMenu() {
  const [deviceSettings, setDeviceSettings] = useState<DeviceSettings | null>(null);
  const [leds, setLeds] = useState<LedSettings>(DEFAULT_LEDS);
  const [irPins, setIrPins] = useState<[number, number]>(DEFAULT_IR_PINS);
  const [source, setSource] = useState<PhotoIlluminationSource>(DEFAULT_SOURCE);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const { status } = useExperimentStatus();
  const locked = status?.state === "running" || status?.state === "paused";

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setDeviceSettings(s);
        setLeds(s.leds);
        setIrPins([s.ir.pins[0] ?? DEFAULT_IR_PINS[0], s.ir.pins[1] ?? DEFAULT_IR_PINS[1]]);
        setSource(s.photoIlluminationSource);
      })
      .catch((e) => toast.error(`Could not load illumination settings: ${(e as Error).message}`))
      .finally(() => setLoading(false));
  }, []);

  const patchLeds = (p: Partial<LedSettings>) => setLeds((c) => ({ ...c, ...p }));

  const handleSave = async () => {
    if (!deviceSettings) return;
    setSaving(true);
    try {
      const saved = await api.saveSettings({
        ...deviceSettings,
        leds,
        ir: { pins: irPins },
        photoIlluminationSource: source,
      });
      setDeviceSettings(saved);
      setLeds(saved.leds);
      setIrPins([saved.ir.pins[0] ?? DEFAULT_IR_PINS[0], saved.ir.pins[1] ?? DEFAULT_IR_PINS[1]]);
      setSource(saved.photoIlluminationSource);
      toast.success("Illumination settings saved — applies to the next experiment.");
    } catch (e) {
      toast.error(`Could not save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setLeds(DEFAULT_LEDS);
    setIrPins(DEFAULT_IR_PINS);
    setSource(DEFAULT_SOURCE);
  };

  return (
    <div className="flex h-full flex-col">
      {locked && (
        <div className="border-b border-app-border-primary bg-app-orange/20 px-3 py-1.5 text-[11px] font-semibold text-app-orange-light">
          An experiment is running — illumination settings are read-only until it finishes.
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-2">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-app-text-muted">
            Loading…
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                Photo Illumination
              </div>
              <p className="mt-1 text-[10px] leading-[14px] text-app-text-muted">
                Lights every dark-type capture — Tropism dark phase, Growth baseline, and Growth
                night — plus the Camera Settings test photo. Applies to every next experiment.
              </p>
              <div className="mt-2 flex w-full items-start gap-1.5">
                {(["ir", "rgbw"] as const).map((opt) => {
                  const isSelected = source === opt;
                  return (
                    <button
                      key={opt}
                      onClick={() => setSource(opt)}
                      disabled={locked}
                      className={`flex flex-1 flex-col items-center justify-center rounded border px-5 py-2 transition-all disabled:cursor-not-allowed disabled:opacity-50 ${
                        isSelected
                          ? "border-[rgba(194,122,255,0.5)] bg-[rgba(194,122,255,0.4)] text-white"
                          : "border-transparent bg-[rgba(194,122,255,0.15)] text-white/60"
                      }`}
                    >
                      <div className="text-center text-[10px] font-bold uppercase leading-[15px]">
                        {opt === "ir" ? "IR (Dark)" : "RGBW (White, Top)"}
                      </div>
                      <div className="text-center text-[9px] leading-[13px] opacity-80">
                        {formatExposure(EXPOSURE_PROFILES[opt].default)} exposure
                      </div>
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-[10px] leading-[14px] text-app-text-muted">
                Exposure follows the source: saving switches the camera to{" "}
                {formatExposure(EXPOSURE_PROFILES[source].default)} and rescales the Camera
                exposure slider to{" "}
                {source === "ir" ? "0.2–10 s" : "10–500 ms"}. IR needs a long integration; the
                RGBW flash does not.
              </p>
            </div>

            <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                LED Strip
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Pixel count</span>
                  <input
                    type="number"
                    min={1}
                    max={600}
                    value={leds.pixelCount}
                    disabled={locked}
                    onChange={(e) => patchLeds({ pixelCount: Number(e.target.value) })}
                    className="rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Pixel order</span>
                  <div className="rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-app-text-secondary">
                    {leds.pixelOrder}
                  </div>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Lateral segment [start, end)</span>
                  <div className="flex gap-1">
                    <input
                      type="number"
                      min={0}
                      max={leds.pixelCount}
                      value={leds.lateralSegment[0]}
                      disabled={locked}
                      onChange={(e) =>
                        patchLeds({
                          lateralSegment: [Number(e.target.value), leds.lateralSegment[1]],
                        })
                      }
                      className="w-full rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                    />
                    <input
                      type="number"
                      min={0}
                      max={leds.pixelCount}
                      value={leds.lateralSegment[1]}
                      disabled={locked}
                      onChange={(e) =>
                        patchLeds({
                          lateralSegment: [leds.lateralSegment[0], Number(e.target.value)],
                        })
                      }
                      className="w-full rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                    />
                  </div>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Top segment [start, end)</span>
                  <div className="flex gap-1">
                    <input
                      type="number"
                      min={0}
                      max={leds.pixelCount}
                      value={leds.topSegment[0]}
                      disabled={locked}
                      onChange={(e) =>
                        patchLeds({ topSegment: [Number(e.target.value), leds.topSegment[1]] })
                      }
                      className="w-full rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                    />
                    <input
                      type="number"
                      min={0}
                      max={leds.pixelCount}
                      value={leds.topSegment[1]}
                      disabled={locked}
                      onChange={(e) =>
                        patchLeds({ topSegment: [leds.topSegment[0], Number(e.target.value)] })
                      }
                      className="w-full rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                    />
                  </div>
                </label>
              </div>
              <p className="mt-2 text-[10px] leading-[14px] text-app-text-muted">
                Lateral segment is used for Tropism bending light; top segment for Growth day
                lighting and RGBW dark-type captures.
              </p>
            </div>

            <div className={locked ? "pointer-events-none opacity-50" : undefined}>
              <SegmentedCard
                label="LED Spacing"
                footer={
                  leds.stride === 1
                    ? "Every pixel in a lit segment fires."
                    : `Every ${ordinal(leds.stride)} pixel fires; the rest stay off.`
                }
                options={STRIDE_OPTIONS.map((o) => ({
                  key: o.key,
                  label: o.label,
                  active: leds.stride === o.value,
                  onClick: () => patchLeds({ stride: o.value }),
                }))}
              />
            </div>

            <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                IR Boards (BCM pins)
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Board 1</span>
                  <input
                    type="number"
                    value={irPins[0]}
                    disabled={locked}
                    onChange={(e) => setIrPins([Number(e.target.value), irPins[1]])}
                    className="rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] text-app-text-muted">Board 2</span>
                  <input
                    type="number"
                    value={irPins[1]}
                    disabled={locked}
                    onChange={(e) => setIrPins([irPins[0], Number(e.target.value)])}
                    className="rounded-md border border-app-border-primary bg-app-bg-tertiary px-2 py-1.5 text-[12px] text-white disabled:opacity-50"
                  />
                </label>
              </div>
              <p className="mt-2 text-[10px] text-app-text-muted">
                Both boards fire together. Only change these if the wiring itself changes.
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
        <button
          onClick={handleReset}
          disabled={loading || locked}
          className="flex items-center gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RotateCcw className="h-[16px] w-[16px]" strokeWidth={1.5} />
          <span className="text-[12px] font-bold uppercase tracking-[1px]">Default</span>
        </button>
        <div className="flex-1" />
        <button
          onClick={handleSave}
          disabled={loading || saving || locked}
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
