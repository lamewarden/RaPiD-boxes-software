import { Copy } from "lucide-react";
import { toast } from "sonner";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import RemoteSyncPanel from "@/components/RemoteSyncPanel";
import TelegramLinkPanel from "@/components/TelegramLinkPanel";
import UpdatePanel from "@/components/UpdatePanel";
import { formatBytes } from "@/lib/format";

const LOW_DISK_BYTES = 2 * 1024 * 1024 * 1024;

export default function GeneralSettingsMenu() {
  const [system] = useSystemInfo();

  const diskLow = system != null && system.diskFreeBytes < LOW_DISK_BYTES;
  const diskUsedPct =
    system && system.diskTotalBytes > 0
      ? Math.round(((system.diskTotalBytes - system.diskFreeBytes) / system.diskTotalBytes) * 100)
      : 0;

  const sshCommand = system?.sshUser && system?.ip ? `ssh ${system.sshUser}@${system.ip}` : null;

  const handleCopySsh = async () => {
    if (!sshCommand) return;
    try {
      await navigator.clipboard.writeText(sshCommand);
      toast.success("SSH command copied");
    } catch {
      toast.error("Could not copy — select the text and copy it manually");
    }
  };

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
              <dt className="text-app-text-muted">IP Address</dt>
              <dd className="truncate font-semibold text-white">{system?.ip ?? "—"}</dd>
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
              SSH Access
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
              <dt className="text-app-text-muted">Status</dt>
              <dd
                className={`font-semibold ${
                  system == null
                    ? "text-white"
                    : system.sshEnabled
                      ? "text-app-green"
                      : "text-app-orange"
                }`}
              >
                {system == null ? "—" : system.sshEnabled ? "Enabled" : "Disabled"}
              </dd>
              <dt className="text-app-text-muted">User</dt>
              <dd className="truncate font-semibold text-white">{system?.sshUser || "—"}</dd>
            </dl>
            {sshCommand ? (
              <button
                onClick={handleCopySsh}
                title="Tap to copy"
                className="mt-2 flex w-full items-center justify-between gap-2 rounded-md bg-app-bg-tertiary px-3 py-2 transition-colors hover:bg-app-border-primary"
              >
                <span className="truncate font-mono text-[12px] text-white">{sshCommand}</span>
                <Copy className="h-[14px] w-[14px] flex-shrink-0 text-app-text-secondary" strokeWidth={1.75} />
              </button>
            ) : (
              system && (
                <p className="mt-2 text-[10px] text-app-text-muted">
                  {system.sshEnabled
                    ? "Waiting for network info…"
                    : "SSH is disabled on this device."}
                </p>
              )
            )}
          </div>

          <RemoteSyncPanel />

          <TelegramLinkPanel />

          <UpdatePanel />
        </div>
      </div>
    </div>
  );
}
