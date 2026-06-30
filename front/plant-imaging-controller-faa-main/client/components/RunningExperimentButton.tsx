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

  return (
    <button
      onClick={() => navigate(target)}
      className={`flex items-center justify-center gap-2 rounded-md border border-app-green/50 bg-app-green/15 px-3 py-2 text-white transition-colors hover:bg-app-green/25 ${className}`.trim()}
    >
      <Activity className="h-[16px] w-[16px] text-app-green" strokeWidth={1.75} />
      <span className="text-[12px] font-bold leading-4">
        {protocolLabel}: {stateLabel}
      </span>
    </button>
  );
}