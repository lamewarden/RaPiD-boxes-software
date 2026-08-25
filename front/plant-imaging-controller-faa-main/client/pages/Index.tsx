import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Sprout, Sun } from "lucide-react";
import TopNav from "@/components/TopNav";
import AssistantChat from "@/components/AssistantChat";
import { inferProtocol } from "@/components/RunningExperimentButton";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";

// How long the home screen can sit untouched, with an experiment running,
// before it auto-returns to that experiment's Progress screen. Without
// this, a kiosk left on "Select Your Program" (its natural resting state
// after Close/reload) stays there indefinitely even mid-run -- so anyone
// glancing at the physical screen, or asking PidiBot for a remote
// screenshot, sees the home screen instead of anything useful. Long
// enough that a moment spent reading it doesn't yank it away; short
// enough that a screenshot taken a minute or two later shows something
// real.
const IDLE_REDIRECT_S = 45;

export default function Index() {
  const navigate = useNavigate();
  const [assistantOpen, setAssistantOpen] = useState(false);
  const { status } = useExperimentStatus();
  const idleTimer = useRef<ReturnType<typeof setTimeout>>();

  // Derived from just state/phase/protocol, not the whole status object --
  // that object gets a new reference on every websocket tick (elapsed
  // seconds, images captured, ...), and using it directly as an effect
  // dependency below would re-arm the idle timer on every tick, so it
  // would never actually fire while a run is live -- the exact opposite
  // of the point.
  const redirectTarget = useMemo(() => {
    if (!status || (status.state !== "running" && status.state !== "paused")) return null;
    const protocol = inferProtocol(status);
    return protocol ? (protocol === "growth" ? "/progress-growth" : "/progress-tropism") : null;
  }, [status?.state, status?.phase, status?.config?.protocol]);

  useEffect(() => {
    if (assistantOpen || !redirectTarget) return; // chat open = active use; nothing running = nothing to return to

    const reset = () => {
      clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(() => navigate(redirectTarget), IDLE_REDIRECT_S * 1000);
    };
    reset();
    window.addEventListener("pointerdown", reset);
    window.addEventListener("keydown", reset);
    return () => {
      clearTimeout(idleTimer.current);
      window.removeEventListener("pointerdown", reset);
      window.removeEventListener("keydown", reset);
    };
  }, [assistantOpen, redirectTarget, navigate]);

  return (
    <div className="relative flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto">
      <TopNav />

      <div className="flex h-[415px] p-2 flex-col justify-center items-start flex-shrink-0 self-stretch bg-app-bg-primary overflow-hidden">
        {/* Shifted up ~10% of the 415px band above (~42px) from its centered
            position, per request -- a translate on this wrapper rather than
            touching the parent's own centering math. */}
        <div className="flex w-full flex-col gap-6 -translate-y-[42px]">
          <div className="text-center w-full">
            <button
              onClick={() => setAssistantOpen(true)}
              title="IEB Image Facility raPIDBOx assistanT"
              className="mb-4 inline-flex items-center gap-3 rounded-full border border-white/50 bg-white/15 px-7 py-3 text-white shadow-lg transition-colors hover:bg-white/25"
            >
              <img src="/pidibot-logo.png" alt="" className="h-[44px] w-[44px] object-contain" />
              <span className="text-[18px] font-bold">PidiBot</span>
            </button>
            <h2 className="text-xl font-bold text-white mb-2">Select Your Program</h2>
            <p className="text-app-text-secondary text-sm mb-4">
              Choose a program and click to configure your imaging experiment
            </p>
          </div>

          <div className="flex h-[60px] p-1 flex-col justify-center items-center flex-shrink-0 self-stretch rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
            <div className="flex w-full h-[50px] justify-center items-start gap-2 flex-shrink-0">
              <Link
                to="/growth"
                className="flex h-[50px] py-1.5 px-0 justify-center items-center gap-2 flex-1 rounded bg-app-green hover:bg-app-green-light transition-colors shadow-lg"
              >
                <Sprout className="w-4 h-4 text-white" strokeWidth={1.33} />
                <span className="text-white text-center text-[14px] font-bold leading-5">
                  Growth Program
                </span>
              </Link>
              <Link
                to="/tropism"
                className="flex h-[50px] py-1.5 px-0 justify-center items-center gap-2 flex-1 rounded bg-app-orange hover:bg-app-orange-light transition-colors shadow-lg"
              >
                <Sun className="w-4 h-4 text-white" strokeWidth={1.33} />
                <span className="text-white text-center text-[14px] font-bold leading-5">
                  Tropism Program
                </span>
              </Link>
            </div>
          </div>
        </div>
      </div>

      <img
        src="/ueb-logo-white.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute bottom-2 left-1/2 h-[80px] w-auto -translate-x-1/2 select-none opacity-20"
      />

      {assistantOpen && <AssistantChat onClose={() => setAssistantOpen(false)} />}
    </div>
  );
}
