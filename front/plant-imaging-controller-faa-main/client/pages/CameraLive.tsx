import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { X } from "lucide-react";
import { toast } from "sonner";
import RunningExperimentButton from "@/components/RunningExperimentButton";
import { api } from "@/lib/api";

type BacklightMode = "off" | "white" | "ir";

export default function CameraLive() {
  const navigate = useNavigate();
  const location = useLocation();
  const previousPage = location.state?.from || "/";
  const [backlight, setBacklight] = useState<BacklightMode>("off");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    return () => {
      // Live left the page — kill assist lights (backend also clears on stream end).
      void api.setLiveBacklight("off").catch(() => {});
    };
  }, []);

  const applyBacklight = async (mode: BacklightMode) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await api.setLiveBacklight(mode);
      setBacklight(res.mode);
    } catch (e) {
      toast.error(`Backlight: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (mode: "white" | "ir") => {
    void applyBacklight(backlight === mode ? "off" : mode);
  };

  const handleClose = async () => {
    try {
      await api.setLiveBacklight("off");
    } catch {
      /* best-effort; stream teardown also clears */
    }
    setBacklight("off");
    navigate(previousPage);
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto bg-app-bg-primary">
      {/* Top nav with close button */}
      <div className="flex p-0.5 justify-between items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary w-full">
        <button
          onClick={handleClose}
          className="flex w-[199.25px] py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">
            Close
          </span>
        </button>
        <RunningExperimentButton />
      </div>

      {/* Full-screen camera feed (MJPEG stream from the backend) */}
      <div className="flex-1 flex items-center justify-center w-full p-2 min-h-0">
        <img
          src="/api/preview"
          alt="Live camera feed"
          className="h-full w-full rounded-lg border-2 border-app-border-primary bg-black object-contain"
        />
      </div>

      <div className="flex w-full gap-2 px-2 pb-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => toggle("white")}
          className={`flex flex-1 items-center justify-center py-2 rounded-[10px] text-[13px] font-semibold transition-colors disabled:opacity-50 ${
            backlight === "white"
              ? "bg-app-green text-white"
              : "bg-app-bg-tertiary border border-app-border-primary text-white hover:bg-app-border-primary"
          }`}
        >
          White backlight
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => toggle("ir")}
          className={`flex flex-1 items-center justify-center py-2 rounded-[10px] text-[13px] font-semibold transition-colors disabled:opacity-50 ${
            backlight === "ir"
              ? "bg-app-violet text-white"
              : "bg-app-bg-tertiary border border-app-border-primary text-white hover:bg-app-border-primary"
          }`}
        >
          IR backlight
        </button>
      </div>
    </div>
  );
}
