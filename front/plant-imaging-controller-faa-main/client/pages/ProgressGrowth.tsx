import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { Folder, Home, Pause, Play, Square, X } from "lucide-react";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import type { ExperimentPhase } from "@shared/api";

const PHASE_LABEL: Partial<Record<ExperimentPhase, string>> = {
  baseline: "Baseline photo",
  day: "Day (lit)",
  night: "Night (dark)",
};

function formatTime(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

export default function ProgressGrowth() {
  const navigate = useNavigate();
  const { status, connected } = useExperimentStatus();
  const summaryOpened = useRef(false);

  const isPaused = status?.state === "paused";
  const isActive = status?.state === "running" || status?.state === "paused";
  const phaseLabel = status?.phase
    ? PHASE_LABEL[status.phase] ?? status.phase
    : isActive
      ? "Starting…"
      : "—";
  const elapsed = status?.elapsedSeconds ?? 0;
  const captured = status?.imagesCaptured ?? 0;
  const planned = status?.imagesPlanned ?? 0;
  const progressPct =
    status && status.totalSeconds > 0
      ? Math.min(100, (status.elapsedSeconds / status.totalSeconds) * 100)
      : 0;
  const experimentId = status?.experimentId ?? null;
  const lastImageUrl =
    experimentId && status?.lastImageId
      ? `/api/images/${experimentId}/${status.lastImageId}`
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
        experimentId,
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

  const handleAbort = async () => {
    try {
      await api.abort();
    } catch {
      /* ignore */
    }
    window.location.replace("/");
  };

  const openGallery = () => {
    if (!experimentId) return;
    navigate("/gallery", {
      state: {
        experimentId,
        returnTo: "/progress-growth",
      },
    });
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto bg-app-bg-primary">
      {/* Top nav */}
      <div className="flex p-0.5 items-center self-stretch border-b border-app-border-primary bg-app-bg-secondary w-full gap-1">
        <button
          onClick={() => navigate("/")}
          className="flex min-w-[140px] py-1.5 px-3 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <Home className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">Home</span>
        </button>
        <button
          onClick={openGallery}
          disabled={!experimentId}
          className="flex min-w-[140px] py-1.5 px-3 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Folder className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">Gallery</span>
        </button>
      </div>

      <div className="flex-1 flex flex-col w-full p-4 gap-3 overflow-hidden">
        <div className="flex items-center justify-between flex-shrink-0">
          <h1 className="text-lg font-bold text-white">Growth Program</h1>
          <span className={`text-xs font-semibold ${connected ? "text-app-green" : "text-app-orange"}`}>
            {connected ? "● live" : "○ reconnecting"}
          </span>
        </div>

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
                  {isActive ? "Waiting for first image…" : status?.message || "No active experiment"}
                </p>
              </div>
            )}
          </div>

          {/* Info panel */}
          <div className="flex flex-col gap-2 flex-shrink-0 w-36">
            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-3 text-center">
              <div className="text-2xl font-black text-app-green tabular-nums">{formatTime(elapsed)}</div>
              <p className="text-app-text-muted text-[10px] mt-1">Elapsed</p>
            </div>

            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
              <div className="text-xl font-black text-app-orange">
                {captured}
                <span className="text-app-text-muted text-sm font-semibold"> / {planned}</span>
              </div>
              <p className="text-app-text-muted text-[10px]">Images</p>
            </div>

            <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-2 text-center">
              <p className="text-[11px] font-semibold text-app-blue">{phaseLabel}</p>
              <p className="text-app-text-muted text-[10px]">
                {isPaused ? "Paused" : status?.state === "done" ? "Finished" : "Phase"}
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
              <button
                onClick={handleAbort}
                disabled={!isActive && !experimentId}
                className="flex items-center justify-center gap-1 px-2 py-1.5 bg-red-600 hover:bg-red-500 text-white font-semibold rounded text-xs transition-colors disabled:opacity-50"
              >
                <X className="w-3 h-3" />
                Abort
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
