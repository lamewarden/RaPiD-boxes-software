import { RotateCcw } from "lucide-react";
import type { RecoveryNotice } from "@shared/api";

/** One-time banner shown after the app resumes a run that survived a crash,
 *  power loss, or reboot -- reports how long the box was offline and how
 *  many captures were missed, so a gap in the sequence isn't a mystery. */
export default function RecoveryNoticeBanner({ notice }: { notice: RecoveryNotice | null }) {
  if (!notice) return null;

  return (
    <div className="flex flex-shrink-0 items-center gap-1.5 rounded-lg border border-app-blue bg-app-blue/10 px-2.5 py-1 text-[10px] font-semibold text-app-blue">
      <RotateCcw className="h-3 w-3 flex-shrink-0" />
      <span className="truncate">{notice.message}</span>
    </div>
  );
}
