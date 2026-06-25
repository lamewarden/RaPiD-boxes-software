import { Link, useLocation } from "react-router-dom";
import { Sprout, Sun } from "lucide-react";

export default function ProgramTabs() {
  const location = useLocation();
  const isGrowth = location.pathname === "/growth";
  const isTropism = location.pathname === "/tropism";

  void isGrowth;

  return (
    <div className="flex p-1 flex-col items-start self-stretch rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
      <div className="flex w-full justify-center items-start gap-2">
        <div
          aria-disabled
          title="Growth program — coming soon"
          className="flex py-1.5 px-0 justify-center items-center gap-2 flex-1 rounded bg-app-bg-tertiary opacity-50 cursor-not-allowed"
        >
          <Sprout className="w-4 h-4 text-app-text-secondary" strokeWidth={1.33} />
          <span className="text-center text-[14px] font-bold leading-5 text-app-text-secondary">
            Growth (soon)
          </span>
        </div>
        <Link
          to="/tropism"
          className={`flex py-1.5 px-0 justify-center items-center gap-2 flex-1 rounded transition-colors ${
            isTropism
              ? "bg-app-orange hover:bg-app-orange-light"
              : "bg-app-bg-tertiary hover:bg-app-border-primary"
          }`}
        >
          <Sun
            className={`w-4 h-4 ${isTropism ? "text-white" : "text-app-text-secondary"}`}
            strokeWidth={1.33}
          />
          <span
            className={`text-center text-[14px] font-bold leading-5 ${
              isTropism ? "text-white" : "text-app-text-secondary"
            }`}
          >
            Tropism Program
          </span>
        </Link>
      </div>
    </div>
  );
}
