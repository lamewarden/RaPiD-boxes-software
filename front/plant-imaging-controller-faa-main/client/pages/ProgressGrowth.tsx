import { useEffect, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { X, Pause, Play, Square, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import { formatCountdown, formatElapsed } from "@/lib/progress";
import type { ExperimentPhase } from "@shared/api";

const PHASE_LABEL: Partial<Record<ExperimentPhase, string>> = {
  baseline: "Baseline photo",
  pre_illumination: "Pre-illumination",
  day: "Day (lit)",
  night: "Night (dark)",
};

export default function ProgressGrowth() {
  const navigate = useNavigate();
  const { status, connected } = useExperimentStatus();
  const summaryOpened = useRef(false);

  const isPaused = status?.state === "paused";
  const isActive = status?.state === "running" || status?.state === "paused";
  const isError = status?.state === "error";
  const phaseLabel = status?.phase
    ? PHASE_LABEL[status.phase] ?? status.phase
    : isActive
      ? "Starting…"
      : "—";
  const elapsed = status?.elapsedSeconds ?? 0;
  const captured = status?.imagesCaptured ?? 0;
  const planned = status?.imagesPlanned ?? 0;
  const nextCapture = formatCountdown(status?.nextCaptureInSeconds);
  const progressPct =
    status && status.totalSeconds > 0
      ? Math.min(100, (status.elapsedSeconds / status.totalSeconds) * 100)
      : 0;
  const lastImageUrl =
    status?.experimentId && status?.lastImageId
      ? `/api/images/${status.experimentId}/${status.lastImageId}`
      : null;
  const dayLabel =
    status?.dayIndex != null && status?.totalDays != null
      ? `Day ${status.dayIndex} / ${status.totalDays}`
      : null;

  const togglePause = () => (isPaused ? api.resume() : api.pause()).catch(() => {});

  const openSummary = () => {
    if (summaryOpened.current) return;
    summaryOpened.current = true;
    navigate("/summary", {
      state: {
        programType: "growth",
        experimentId: status?.experimentId ?? null,
        elapsed,
        imagesCaptured: captured,
      },
    });
  };

  useEffect(() => {
    if (status?.state === "done") {
      openSummary();
    }
  }, [status?.state]);

  const handleStop = async () => {
    try {
      await api.stop();
    } catch {
      /* ignore */
    }
    openSummary();
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto bg-app-bg-primary">
      {/* Top nav */}
      <div className="flex p-0.5 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary w-full">
        <Link
          to="/"
          className="flex w-[199.25px] py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">Close</span>
        </Link>
      </div>

      <div className="flex-1 flex flex-col w-full p-4 gap-3 overflow-hidden">
        <div className="flex items-center justify-between flex-shrink-0">
          <h1 className="text-lg font-bold text-white">Growth Program</h1>
          <span className={`text-xs font-semibold ${connected ? "text-app-green" : "text-app-orange"}`}>
            {connected ? "● live" : "○ reconnecting"}
          </span>
        </div>

        {isError && (
          <div className="flex items-start gap-2 rounded-lg border border-app-orange bg-app-orange/15 p-2.5 flex-shrink-0">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-app-orange" />
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-bold text-app-orange-light">Experiment failed</p>
              <p className="mt-0.5 text-[11px] text-app-text-secondary">
                {status?.message ?? "An unexpected error occurred."}
              </p>
            </div>
            <Link
              to="/"
              className="flex-shrink-0 rounded-md bg-app-bg-tertiary px-2 py-1 text-[10px] font-bold uppercase text-white"
            >
              Home
            </Link>
          </div>
        )}

        {/* Progress bar */}
        <div className="h-2 w-full rounded-full bg-app-bg-tertiary flex-shrink-0">
          <div className="h-2 rounded-full bg-app-green transition-all" style={{ width: `${progressPct}%` }} />
        </div>

        <div className="flex flex-1 gap-3 min-h-0">
          {/* Last captured image */}
          <div className="flex-1 bg-gradient-to-br from-app-bg-secondary to-app-bg-tertiary border-2 border-app-border-primary rounded-lg overflow-hidden flex items-center justify-center min-w-0">
            {lastImageUrl ? (
              <img src={lastImageUrl} alt="last capture" className="h-full w-full object-contain" />
            ) : (
              <div className="text-center p-6">
                <p className="text-app-text-muted text-sm">
                  {isError
                    ? status?.message || "Experiment ended with an error"
                    : isActive
                      ? "Waiting for first image…"
                      : status?.message || "No active experiment"}
                </p>
              </div>
            )}
          </div>

          {/* Info panel */}
          <div className="flex flex-col gap-2 flex-shrink-0 w-36">
            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-3 text-center">
              <div className="text-2xl font-black text-app-green tabular-nums">{formatElapsed(elapsed)}</div>
              <p className="text-app-text-muted text-[10px] mt-1">Elapsed</p>
            </div>

            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
              <div className="text-xl font-black text-app-orange">
                {captured}
                <span className="text-app-text-muted text-sm font-semibold"> / {planned}</span>
              </div>
              <p className="text-app-text-muted text-[10px]">Images</p>
            </div>

            {nextCapture && isActive && (
              <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
                <p className="text-[11px] font-semibold text-app-green">{nextCapture}</p>
                <p className="text-app-text-muted text-[10px]">Next capture</p>
              </div>
            )}

            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
              <p className="text-[11px] font-semibold text-app-blue">{phaseLabel}</p>
              <p className="text-app-text-muted text-[10px]">
                {isPaused ? "Paused" : isError ? "Error" : status?.state === "done" ? "Finished" : "Phase"}
              </p>
            </div>

            {dayLabel && (
              <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
                <p className="text-[11px] font-semibold text-app-green">{dayLabel}</p>
                <p className="text-app-text-muted text-[10px]">Day</p>
              </div>
            )}

            <div className="flex flex-col gap-1 mt-auto">
              <button
                onClick={togglePause}
                disabled={!isActive}
                className="flex items-center justify-center gap-1 px-2 py-1.5 bg-app-orange hover:bg-app-orange-light text-white font-semibold rounded text-xs transition-colors disabled:opacity-50"
              >
                {isPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                {isPaused ? "Resume" : "Pause"}
              </button>
              <button
                onClick={handleStop}
                className="flex items-center justify-center gap-1 px-2 py-1.5 bg-app-bg-secondary border border-app-border-primary hover:bg-app-bg-tertiary text-white font-semibold rounded text-xs transition-colors"
              >
                <Square className="w-3 h-3" />
                {isActive ? "Stop" : "Summary"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
