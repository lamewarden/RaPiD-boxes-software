import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { restartAppToHome } from "@/lib/restartApp";
import type { UpdateCheckResult } from "@shared/api";

/**
 * OTA self-update card for Settings -> General. Two-step, both explicit:
 * "Check for Updates" only fetches + compares (never touches the working
 * tree); "Update Now" does the fast-forward-only pull. The same monthly
 * unattended check (deploy/rapidboxes-update.timer) runs the identical git
 * logic headlessly and restarts automatically on success -- here a human is
 * present, so we ask before restarting instead.
 */
type Phase = "idle" | "checking" | "available" | "applying" | "restart-prompt";

export default function UpdatePanel() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [check, setCheck] = useState<UpdateCheckResult | null>(null);
  const [restarting, setRestarting] = useState(false);

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
      if (result.status === "error") {
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
      setPhase("restart-prompt");
    } catch (e) {
      toast.error(`Update failed: ${(e as Error).message}`);
      setPhase("available");
    }
  };

  const dismiss = () => {
    setCheck(null);
    setPhase("idle");
  };

  return (
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

      {phase === "restart-prompt" && (
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
                onClick={() => {
                  setCheck(null);
                  setPhase("idle");
                }}
                disabled={restarting}
                className="flex-1 rounded-md bg-app-bg-tertiary py-2 text-[12px] font-semibold text-app-text-secondary transition-colors hover:bg-app-border-primary disabled:opacity-60"
              >
                Later
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
