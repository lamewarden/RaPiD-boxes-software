import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { X, Pause, Square } from "lucide-react";

interface ProgressProps {
  program: "growth" | "tropism";
}

export default function Progress({ program }: ProgressProps) {
  const [elapsed, setElapsed] = useState(0);
  const [isPaused, setIsPaused] = useState(false);

  useEffect(() => {
    if (isPaused) return;

    const interval = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(interval);
  }, [isPaused]);

  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
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
          <span className="text-white text-center text-[13px] font-semibold leading-5">
            Close
          </span>
        </Link>
      </div>

      {/* Progress content */}
      <div className="flex-1 flex flex-col items-center justify-center w-full p-8 gap-8">
        <div className="text-center">
          <h1 className="text-3xl font-bold text-white mb-2">
            {program === "growth" ? "Growth" : "Tropism"} Program Running
          </h1>
          <p className="text-app-text-secondary text-sm">Experiment in progress</p>
        </div>

        {/* Timer */}
        <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-8">
          <div className="text-5xl font-black text-app-green tabular-nums">
            {formatTime(elapsed)}
          </div>
          <p className="text-app-text-muted text-xs mt-2 text-center">
            Elapsed Time
          </p>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-4 w-full max-w-xs">
          <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-4 text-center">
            <p className="text-app-text-muted text-xs mb-1">Images Captured</p>
            <p className="text-2xl font-bold text-app-green">{Math.floor(elapsed / 30)}</p>
          </div>
          <div className="bg-app-bg-secondary border border-app-border-primary rounded-lg p-4 text-center">
            <p className="text-app-text-muted text-xs mb-1">Status</p>
            <p className="text-sm font-semibold text-app-orange">
              {isPaused ? "Paused" : "Running"}
            </p>
          </div>
        </div>

        {/* Controls */}
        <div className="flex gap-3">
          <button
            onClick={() => setIsPaused(!isPaused)}
            className="flex items-center justify-center gap-2 px-6 py-2 bg-app-orange hover:bg-app-orange-light text-white font-semibold rounded-lg transition-colors"
          >
            <Pause className="w-4 h-4" />
            {isPaused ? "Resume" : "Pause"}
          </button>
          <Link
            to="/"
            className="flex items-center justify-center gap-2 px-6 py-2 bg-app-bg-secondary border border-app-border-primary hover:bg-app-bg-tertiary text-white font-semibold rounded-lg transition-colors"
          >
            <Square className="w-4 h-4" />
            Stop
          </Link>
        </div>
      </div>
    </div>
  );
}
