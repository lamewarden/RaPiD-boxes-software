import { useEffect, useState } from "react";
import { Check, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import type { DeviceSettings } from "@shared/api";

const LOW_DISK_BYTES = 2 * 1024 * 1024 * 1024;

const DEFAULT_LEDS: DeviceSettings["leds"] = {
  pixelCount: 70,
  pixelOrder: "GRBW",
  topSegment: [22, 64],
  lateralSegment: [0, 21],
  spiHz: 6_400_000,
};

function formatBytes(n: number): string {
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(0)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

export default function GeneralSettingsMenu() {
  const [deviceSettings, setDeviceSettings] = useState<DeviceSettings | null>(null);
  const [leds, setLeds] = useState(DEFAULT_LEDS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [system] = useSystemInfo();
  const { status } = useExperimentStatus();
  const locked = status?.state === "running" || status?.state === "paused";

  useEffect(() => {
    api
      .settings()
      .then((s) => {
        setDeviceSettings(s);
        setLeds(s.leds);
      })
      .catch((e) => toast.error(`Could not load settings: ${(e as Error).message}`))
      .finally(() => setLoading(false));
  }, []);

  const patchLeds = (p: Partial<DeviceSettings["leds"]>) =>
    setLeds((current) => ({ ...current, ...p }));

  const handleSave = async () => {
    if (!deviceSettings) return;
    setSaving(true);
    try {
      const saved = await api.saveSettings({ ...deviceSettings, leds });
      setDeviceSettings(saved);
      setLeds(saved.leds);
      toast.success("LED settings saved.");
    } catch (e) {
      toast.error(`Could not save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const diskLow = system != null && system.diskFreeBytes < LOW_DISK_BYTES;
  const diskUsedPct =
    system && system.diskTotalBytes > 0
      ? Math.round(((system.diskTotalBytes - system.diskFreeBytes) / system.diskTotalBytes) * 100)
      : 0;

  return (
    <div className="flex h-full flex-col">
      {locked && (
        <div className="border-b border-app-border-primary bg-app-orange/20 px-3 py-1.5 text-[11px] font-semibold text-app-orange-light">
          An experiment is running — LED settings are read-only until it finishes.
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
                System
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
                <dt className="text-app-text-muted">Hostname</dt>
                <dd className="truncate font-semibold text-white">{system?.hostname ?? "—"}</dd>
                <dt className="text-app-text-muted">Version</dt>
                <dd className="font-semibold text-white">{system?.version ?? "—"}</dd>
                <dt className="text-app-text-muted">Mode</dt>
                <dd className="font-semibold text-white">
                  {system?.simulation ? "Simulation" : "Hardware"}
                </dd>
                <dt className="text-app-text-muted">Storage</dt>
                <dd className="truncate font-semibold text-white" title={system?.storageRoot}>
                  {system?.storageRoot ?? "—"}
                </dd>
              </dl>
            </div>

            <div
              className={`rounded-[10px] border p-3 ${
                diskLow
                  ? "border-app-orange bg-app-orange/10"
                  : "border-app-border-primary bg-app-bg-secondary"
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                  Disk Space
                </div>
                {diskLow && (
                  <span className="text-[10px] font-bold uppercase text-app-orange">Low</span>
                )}
              </div>
              <div className="mt-2 text-[15px] font-black text-white">
                {system ? formatBytes(system.diskFreeBytes) : "—"}{" "}
                <span className="text-[11px] font-semibold text-app-text-muted">free</span>
              </div>
              {system && (
                <>
                  <div className="mt-2 h-1.5 w-full rounded-full bg-app-bg-tertiary">
                    <div
                      className={`h-1.5 rounded-full ${diskLow ? "bg-app-orange" : "bg-app-green"}`}
                      style={{ width: `${diskUsedPct}%` }}
                    />
                  </div>
                  <p className="mt-1.5 text-[10px] text-app-text-muted">
                    {formatBytes(system.diskTotalBytes - system.diskFreeBytes)} used of{" "}
                    {formatBytes(system.diskTotalBytes)}
                  </p>
                </>
              )}
              {diskLow && (
                <p className="mt-2 text-[10px] font-semibold text-app-orange-light">
                  Less than 2 GB free — consider exporting or removing old experiments.
                </p>
              )}
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
                lighting and RGBW night captures.
              </p>
            </div>

            <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
                IR Boards
              </div>
              <p className="mt-2 text-[12px] font-semibold text-white">
                BCM pins: {deviceSettings?.ir.pins.join(", ") ?? "—"}
              </p>
              <p className="mt-1 text-[10px] text-app-text-muted">
                IR pin mapping is fixed in firmware wiring; contact an administrator to change it.
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
        <button
          onClick={() => setLeds(DEFAULT_LEDS)}
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
