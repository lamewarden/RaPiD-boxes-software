import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { restartAppToHome } from "@/lib/restartApp";
import type { UpdateCheckResult, VersionStatus } from "@shared/api";

/**
 * OTA self-update card for Settings -> General. Two independent flows:
 *  - "Software Update": "Check for Updates" only fetches + compares (never
 *    touches the working tree); "Update Now" does the fast-forward-only
 *    pull. The same monthly unattended check (deploy/rapidboxes-update.timer)
 *    runs the identical git logic headlessly and restarts automatically on
 *    success -- here a human is present, so we ask before restarting instead.
 *  - "Version": shows the currently-running commit + how long it's been
 *    running, and (once a previous version is on record) a "Roll back"
 *    button. Both flows end at the same restart-prompt overlay on success.
 */
type Phase = "idle" | "checking" | "available" | "applying";

function formatDuration(ms: number): string {
  const totalMinutes = Math.floor(Math.max(0, ms) / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return "just now";
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function UpdatePanel() {
  // "Check for updates" / "Update Now" flow.
  const [phase, setPhase] = useState<Phase>("idle");
  const [check, setCheck] = useState<UpdateCheckResult | null>(null);

  // "Version" / rollback flow -- independent of the above.
  const [version, setVersion] = useState<VersionStatus | null>(null);
  const [versionLoading, setVersionLoading] = useState(true);
  const [rollbackConfirming, setRollbackConfirming] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);

  // Shared: both flows end here on a successful, rebuild-clean git move.
  const [restartPromptVisible, setRestartPromptVisible] = useState(false);
  const [restarting, setRestarting] = useState(false);

  const refreshVersion = async () => {
    try {
      const v = await api.versionStatus();
      setVersion(v);
    } catch {
      // best-effort -- leave whatever we last had (or null on first load)
    } finally {
      setVersionLoading(false);
    }
  };

  useEffect(() => {
    refreshVersion();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCheck = async () => {
    if (phase === "checking" || phase === "applying") return;
    setPhase("checking");
    try {
      const result = await api.checkForUpdate();
      if (result.error) {
        toast.error(`Update check failed: ${result.error}`);
        setPhase("idle");
        return;
      }
      if (!result.updateAvailable) {
        toast.success(`Up to date (origin/${result.branch}).`);
        setCheck(null);
        setPhase("idle");
        return;
      }
      setCheck(result);
      setPhase("available");
    } catch (e) {
      toast.error(`Update check failed: ${(e as Error).message}`);
      setPhase("idle");
    }
  };

  const handleApply = async () => {
    setPhase("applying");
    try {
      const result = await api.applyUpdate();
      if (result.status === "error" || result.status === "experiment_active") {
        toast.error(result.message);
        setPhase("available");
        return;
      }
      if (result.status === "up_to_date") {
        toast.success("Already up to date.");
        setCheck(null);
        setPhase("idle");
        return;
      }
      // status === "updated"
      await refreshVersion();
      if (result.rebuildStatus === "failed") {
        // The git pull succeeded but the dependency/build step didn't --
        // code and installed deps are now mismatched. Don't offer to
        // restart into that; surface it clearly so a human can SSH in and
        // finish the job (e.g. deploy/update.sh) instead.
        toast.error(
          `Update pulled but the rebuild failed: ${result.rebuildMessage ?? "unknown error"}. ` +
            "Do not restart until this is resolved manually.",
          { duration: 15000 },
        );
        setCheck(null);
        setPhase("idle");
        return;
      }
      setCheck(null);
      setPhase("idle");
      setRestartPromptVisible(true);
    } catch (e) {
      toast.error(`Update failed: ${(e as Error).message}`);
      setPhase("available");
    }
  };

  const dismiss = () => {
    setCheck(null);
    setPhase("idle");
  };

  const handleRollback = async () => {
    setRollingBack(true);
    try {
      const result = await api.rollbackUpdate();
      if (result.status === "error" || result.status === "experiment_active") {
        toast.error(result.message);
        return;
      }
      if (result.status === "nothing_to_roll_back_to") {
        toast.error(result.message);
        await refreshVersion();
        return;
      }
      // status === "rolled_back"
      await refreshVersion();
      if (result.rebuildStatus === "failed") {
        toast.error(
          `Rollback moved the code back but the rebuild failed: ${
            result.rebuildMessage ?? "unknown error"
          }. Do not restart until this is resolved manually.`,
          { duration: 15000 },
        );
        return;
      }
      setRestartPromptVisible(true);
    } catch (e) {
      toast.error(`Rollback failed: ${(e as Error).message}`);
    } finally {
      setRollingBack(false);
      setRollbackConfirming(false);
    }
  };

  const currentRunningFor = version?.current
    ? formatDuration(Date.now() - new Date(version.current.appliedAt).getTime())
    : null;
  const previousRanFor =
    version?.current && version?.previous
      ? formatDuration(
          new Date(version.current.appliedAt).getTime() - new Date(version.previous.appliedAt).getTime(),
        )
      : null;

  return (
    <>
      <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
        <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
          Software Update
        </div>

        {phase !== "available" && phase !== "applying" && (
          <button
            onClick={handleCheck}
            disabled={phase === "checking"}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-md bg-app-bg-tertiary py-2 text-[12px] font-semibold text-white transition-colors hover:bg-app-border-primary disabled:opacity-60"
          >
            <RefreshCw
              className={`h-[14px] w-[14px] ${phase === "checking" ? "animate-spin" : ""}`}
              strokeWidth={1.75}
            />
            {phase === "checking" ? "Checking…" : "Check for Updates"}
          </button>
        )}

        {(phase === "available" || phase === "applying") && check && (
          <div className="mt-2 rounded-md border border-app-green/40 bg-app-green/10 p-2.5">
            <p className="text-[11px] font-semibold text-white">
              {check.commitsBehind} commit{check.commitsBehind === 1 ? "" : "s"} behind{" "}
              origin/{check.branch}
            </p>
            {check.commitLog.length > 0 && (
              <ul className="mt-1.5 max-h-24 space-y-0.5 overflow-y-auto text-[10px] text-app-text-muted">
                {check.commitLog.map((line, i) => (
                  <li key={i} className="truncate">
                    {line}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-2 flex gap-2">
              <button
                onClick={handleApply}
                disabled={phase === "applying"}
                className="flex-1 rounded-md bg-app-green py-1.5 text-[11px] font-bold uppercase tracking-wide text-white transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {phase === "applying" ? "Updating…" : "Update Now"}
              </button>
              <button
                onClick={dismiss}
                disabled={phase === "applying"}
                className="flex-1 rounded-md bg-app-bg-tertiary py-1.5 text-[11px] font-semibold text-app-text-secondary transition-colors hover:bg-app-border-primary disabled:opacity-60"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
        <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
          Version
        </div>

        {version?.current ? (
          <>
            <p className="mt-2 text-[11px] text-white">
              Running <span className="font-mono font-semibold">{version.current.commit}</span>
              {currentRunningFor ? ` for ${currentRunningFor}` : ""}
            </p>

            {version.previous ? (
              !rollbackConfirming ? (
                <button
                  onClick={() => setRollbackConfirming(true)}
                  className="mt-2 flex w-full items-center justify-center gap-2 rounded-md bg-app-bg-tertiary py-2 text-[12px] font-semibold text-white transition-colors hover:bg-app-border-primary"
                >
                  Roll back to <span className="font-mono">{version.previous.commit}</span>
                </button>
              ) : (
                <div className="mt-2 rounded-md border border-app-orange/40 bg-app-orange/10 p-2.5">
                  <p className="text-[11px] font-semibold text-white">
                    Roll back to <span className="font-mono">{version.previous.commit}</span>
                    {previousRanFor ? ` (ran for ${previousRanFor}` : ""}
                    {previousRanFor ? `, until ${formatDate(version.current.appliedAt)})` : ""}?
                  </p>
                  <p className="mt-1.5 text-[10px] text-app-text-muted">
                    Note: if this device auto-updates monthly from the same branch, next month's
                    check may fast-forward right back onto the commit you're rolling back from.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button
                      onClick={handleRollback}
                      disabled={rollingBack}
                      className="flex-1 rounded-md bg-app-orange py-1.5 text-[11px] font-bold uppercase tracking-wide text-white transition-opacity hover:opacity-90 disabled:opacity-60"
                    >
                      {rollingBack ? "Rolling back…" : "Roll Back"}
                    </button>
                    <button
                      onClick={() => setRollbackConfirming(false)}
                      disabled={rollingBack}
                      className="flex-1 rounded-md bg-app-bg-tertiary py-1.5 text-[11px] font-semibold text-app-text-secondary transition-colors hover:bg-app-border-primary disabled:opacity-60"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )
            ) : (
              <p className="mt-2 text-[10px] text-app-text-muted">
                No previous version recorded yet.
              </p>
            )}
          </>
        ) : (
          <p className="mt-2 text-[10px] text-app-text-muted">
            {versionLoading ? "Loading…" : "Version info unavailable."}
          </p>
        )}
      </div>

      {restartPromptVisible && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-[360px] rounded-xl border border-app-border-primary bg-app-bg-secondary p-4 shadow-2xl">
            <p className="text-[13px] font-bold text-white">Update installed</p>
            <p className="mt-1 text-[12px] text-app-text-muted">
              Restart now to apply changes?
            </p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => restartAppToHome(setRestarting)}
                disabled={restarting}
                className="flex-1 rounded-md bg-app-green py-2 text-[12px] font-bold uppercase tracking-wide text-white transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {restarting ? "Restarting…" : "Restart"}
              </button>
              <button
                onClick={() => setRestartPromptVisible(false)}
                disabled={restarting}
                className="flex-1 rounded-md bg-app-bg-tertiary py-2 text-[12px] font-semibold text-app-text-secondary transition-colors hover:bg-app-border-primary disabled:opacity-60"
              >
                Later
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
