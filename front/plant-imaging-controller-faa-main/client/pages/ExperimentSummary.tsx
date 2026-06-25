import { useLocation, useNavigate } from "react-router-dom";
import { Home } from "lucide-react";

export default function ExperimentSummary() {
  const location = useLocation();
  const navigate = useNavigate();

  const state = location.state as {
    programType: "growth" | "tropism";
    elapsed: number;
    imagesCaptured: number;
    phase: string;
  } | null;

  // Fallback values if state is not provided
  const programType = state?.programType || "growth";
  const elapsed = state?.elapsed || 0;
  const imagesCaptured = state?.imagesCaptured || 0;
  const phase = state?.phase || "Light";

  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  const programName =
    programType === "growth" ? "Growth Program" : "Tropism Program";

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto bg-app-bg-primary">
      {/* Top nav */}
      <div className="flex p-0.5 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary w-full">
        <div className="text-white text-center text-[13px] font-semibold leading-5 flex-1 py-1.5">
          Experiment Summary
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex flex-col w-full p-6 gap-6 overflow-hidden justify-center items-center">
        {/* Title */}
        <div className="text-center">
          <h1 className="text-2xl font-bold text-white mb-2">
            {programName}
          </h1>
          <p className="text-app-text-muted text-sm">Experiment Completed</p>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-2 gap-4 w-full max-w-md">
          {/* Elapsed Time */}
          <div className="bg-app-bg-secondary border-2 border-app-border-primary rounded-lg p-4 text-center">
            <div className="text-app-text-muted text-xs font-bold uppercase mb-2">
              Elapsed Time
            </div>
            <div className="text-2xl font-black text-app-green tabular-nums">
              {formatTime(elapsed)}
            </div>
          </div>

          {/* Images Captured */}
          <div className="bg-app-bg-secondary border-2 border-app-border-primary rounded-lg p-4 text-center">
            <div className="text-app-text-muted text-xs font-bold uppercase mb-2">
              Images Captured
            </div>
            <div className="text-2xl font-black text-app-orange">
              {imagesCaptured}
            </div>
          </div>

          {/* Phase */}
          <div className="bg-app-bg-secondary border-2 border-app-border-primary rounded-lg p-4 text-center col-span-2">
            <div className="text-app-text-muted text-xs font-bold uppercase mb-2">
              Stopped In Phase
            </div>
            <div className="text-xl font-bold text-app-blue">
              {phase}
            </div>
          </div>
        </div>

        {/* Action Button */}
        <button
          onClick={() => navigate("/")}
          className="flex items-center justify-center gap-2 px-8 py-3 bg-app-green hover:bg-app-green-light text-white font-bold rounded-lg transition-colors mt-4"
        >
          <Home className="w-5 h-5" />
          <span>Return to Main Menu</span>
        </button>
      </div>
    </div>
  );
}
