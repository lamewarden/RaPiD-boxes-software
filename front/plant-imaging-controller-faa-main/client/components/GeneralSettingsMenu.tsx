import { useSystemInfo } from "@/hooks/useSystemInfo";

const LOW_DISK_BYTES = 2 * 1024 * 1024 * 1024;

function formatBytes(n: number): string {
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(0)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

export default function GeneralSettingsMenu() {
  const [system] = useSystemInfo();

  const diskLow = system != null && system.diskFreeBytes < LOW_DISK_BYTES;
  const diskUsedPct =
    system && system.diskTotalBytes > 0
      ? Math.round(((system.diskTotalBytes - system.diskFreeBytes) / system.diskTotalBytes) * 100)
      : 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-2">
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
        </div>
      </div>
    </div>
  );
}
