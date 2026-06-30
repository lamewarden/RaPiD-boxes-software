import { Link } from "react-router-dom";
import { Sprout, Sun } from "lucide-react";
import TopNav from "@/components/TopNav";

export default function Index() {
  return (
    <div className="relative flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto">
      <TopNav />
      
      <div className="flex h-[415px] p-2 flex-col justify-center items-start gap-6 flex-shrink-0 self-stretch bg-app-bg-primary overflow-hidden">
        <div className="text-center w-full">
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

      <img
        src="/ueb-logo-white.svg"
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute bottom-2 left-1/2 h-[80px] w-auto -translate-x-1/2 select-none opacity-20"
      />
    </div>
  );
}
