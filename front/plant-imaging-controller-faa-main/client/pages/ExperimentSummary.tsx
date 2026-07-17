import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Power } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { restartAppToHome } from "@/lib/restartApp";
import type { ImageInfo } from "@shared/api";

export default function ExperimentSummary() {
  const location = useLocation();
  const navigate = useNavigate();
  const [restarting, setRestarting] = useState(false);

  const state = location.state as {
    programType: "growth" | "tropism";
    experimentId: string | null;
    elapsed: number;
    imagesCaptured: number;
  } | null;

  const programType = state?.programType || "tropism";
  const experimentId = state?.experimentId ?? null;
  const elapsed = state?.elapsed || 0;
  const imagesCaptured = state?.imagesCaptured || 0;

  useEffect(() => {
    if (!state) {
      navigate("/", { replace: true });
    }
  }, [navigate, state]);

  const { data: system } = useQuery({
    queryKey: ["system"],
    queryFn: () => api.system(),
  });

  const { data: imageList, isLoading: imagesLoading } = useQuery({
    queryKey: ["summary-images", experimentId],
    queryFn: () => api.images(experimentId ?? undefined),
  });

  const images = imageList?.images ?? [];
  const firstFrame: ImageInfo | null = images.length > 0 ? images[0] : null;
  const lastFrame: ImageInfo | null = images.length > 0 ? images[images.length - 1] : null;
  const effectiveCount = Math.max(imagesCaptured, images.length);

  const storagePath = useMemo(() => {
    if (!system?.storageRoot) return "Unknown";
    if (!experimentId) return system.storageRoot;
    return `${system.storageRoot}/${experimentId}`;
  }, [system?.storageRoot, experimentId]);

  const formatTime = (seconds: number) => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  const handleClose = async () => {
    if (restarting) return;
    await restartAppToHome(setRestarting);
  };

  const programName = programType === "growth" ? "Growth Measurement" : "Tropism Measurement";

  return (
    <div className="flex w-[800px] h-[452px] flex-col mx-auto bg-app-bg-primary">
      <div className="flex p-1 justify-center items-center self-stretch border-b border-app-border-primary bg-app-bg-secondary">
        <div className="text-white text-center text-[14px] font-semibold leading-5 flex-1 py-1">
          Measurement Finished
        </div>
      </div>

      <div className="flex-1 flex flex-col w-full p-3 gap-2 overflow-hidden">
        <div className="text-center">
          <h1 className="text-[18px] font-bold text-white">{programName} Completed</h1>
        </div>

        <div className="grid grid-cols-2 gap-2 flex-shrink-0">
          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2 text-center">
            <div className="text-app-text-muted text-[10px] font-bold uppercase">Total Time</div>
            <div className="text-[22px] font-black text-app-green tabular-nums leading-7">
              {formatTime(elapsed)}
            </div>
          </div>
          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2 text-center">
            <div className="text-app-text-muted text-[10px] font-bold uppercase">Frames Captured</div>
            <div className="text-[22px] font-black text-app-orange leading-7">{effectiveCount}</div>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 flex-1 min-h-0">
          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary overflow-hidden flex flex-col min-h-0">
            <div className="text-app-text-muted text-[10px] font-bold uppercase px-2 py-1 border-b border-app-border-primary">
              First Frame
            </div>
            <div className="flex-1 flex items-center justify-center bg-app-bg-tertiary min-h-0">
              {firstFrame ? (
                <img src={firstFrame.url} alt="First frame" className="h-full w-full object-contain" />
              ) : (
                <span className="text-app-text-muted text-[11px]">
                  {imagesLoading ? "Loading..." : "No frame"}
                </span>
              )}
            </div>
          </div>

          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary overflow-hidden flex flex-col min-h-0">
            <div className="text-app-text-muted text-[10px] font-bold uppercase px-2 py-1 border-b border-app-border-primary">
              Last Frame
            </div>
            <div className="flex-1 flex items-center justify-center bg-app-bg-tertiary min-h-0">
              {lastFrame ? (
                <img src={lastFrame.url} alt="Last frame" className="h-full w-full object-contain" />
              ) : (
                <span className="text-app-text-muted text-[11px]">
                  {imagesLoading ? "Loading..." : "No frame"}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary px-3 py-2 flex items-center gap-2">
          <span className="text-app-text-muted text-[10px] font-bold uppercase">Stored At</span>
          <span className="text-[11px] font-semibold text-app-text-secondary truncate">{storagePath}</span>
        </div>

        <button
          onClick={handleClose}
          disabled={restarting}
          className="flex items-center justify-center gap-2 py-2 rounded-[10px] bg-app-green hover:bg-app-green-light text-white font-black uppercase tracking-[1.2px] transition-colors disabled:opacity-60"
        >
          <Power className="w-4 h-4" strokeWidth={1.75} />
          <span>{restarting ? "Restarting Service..." : "Close"}</span>
        </button>
      </div>
    </div>
  );
}
