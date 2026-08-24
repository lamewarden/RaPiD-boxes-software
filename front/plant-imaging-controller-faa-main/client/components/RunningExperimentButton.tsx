import { Activity } from "lucide-react";
import { useNavigate } from "react-router-dom";
import type { ExperimentPhase, ExperimentStatus } from "@shared/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";

function inferProtocol(status: ExperimentStatus): "growth" | "tropism" | null {
  const protocol = status.config?.protocol;
  if (protocol === "growth" || protocol === "tropism") {
    return protocol;
  }

  const phase = status.phase;
  if (phase === "baseline" || phase === "day" || phase === "night") {
    return "growth";
  }
  if (phase === "dark" || phase === "bending") {
    return "tropism";
  }

  return null;
}

function phaseLabel(phase: ExperimentPhase | null): string {
  switch (phase) {
    case "baseline":
      return "Baseline";
    case "day":
      return "Day";
    case "night":
      return "Night";
    case "dark":
      return "Dark";
    case "bending":
      return "Bending";
    default:
      return "Running";
  }
}

export default function RunningExperimentButton({ className = "" }: { className?: string }) {
  const navigate = useNavigate();
  const { status } = useExperimentStatus();

  if (status?.state !== "running" && status?.state !== "paused") {
    return null;
  }

  const protocol = inferProtocol(status);
  if (!protocol) {
    return null;
  }

  const target = protocol === "growth" ? "/progress-growth" : "/progress-tropism";
  const protocolLabel = protocol === "growth" ? "Growth" : "Tropism";
  const stateLabel = status.state === "paused" ? "Paused" : phaseLabel(status.phase);
  const progressPct =
    status.totalSeconds > 0
      ? Math.min(100, Math.max(0, (status.elapsedSeconds / status.totalSeconds) * 100))
      : 0;

  return (
    <button
      onClick={() => navigate(target)}
      className={`relative flex items-center justify-center gap-2 overflow-hidden rounded-md border border-app-green/50 bg-app-bg-tertiary px-3 py-2 text-white transition-colors hover:bg-app-green/15 ${className}`.trim()}
    >
      {/* Progress fill, grown smoothly on every status update -- a light
          wave loops inside it, clipped to this width so it visibly stops at
          the current point instead of sweeping the whole button. */}
      <div
        className="absolute inset-y-0 left-0 overflow-hidden bg-gradient-to-r from-app-green/30 to-app-green/10 transition-[width] duration-1000 ease-out"
        style={{ width: `${progressPct}%` }}
      >
        <div className="absolute inset-y-0 w-1/4 animate-progress-wave bg-gradient-to-r from-transparent via-white/40 to-transparent" />
      </div>

      {/* "You are here" -- a soft breathing dot right at the progress edge. */}
      {status.state === "running" && (
        <div
          className="absolute top-1/2 left-0 h-1.5 w-1.5 animate-pulse-glow rounded-full bg-app-green-bright shadow-[0_0_6px_2px_hsl(var(--app-green-bright)/0.6)]"
          style={{ left: `${progressPct}%` }}
        />
      )}

      <Activity className="relative z-10 h-[18px] w-[18px] text-app-green" strokeWidth={1.75} />
      <span className="relative z-10 whitespace-nowrap text-[12px] font-semibold leading-5">
        {protocolLabel}: {stateLabel}
      </span>
    </button>
  );
}