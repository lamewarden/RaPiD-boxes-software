import { AlertTriangle, Info } from "lucide-react";
import type { StorageNotice } from "@shared/api";

/** Retention-policy reminder for the experiment progress screen: either an
 *  "your data expires soon, back it up" warning or the standing 90-day
 *  auto-clean notice, computed once by the backend when the run started. */
export default function StorageNoticeBanner({ notice }: { notice: StorageNotice | null }) {
  if (!notice) return null;
  const expiring = notice.kind === "expiring";

  return (
    <div
      className={`flex flex-shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[10px] font-semibold ${
        expiring
          ? "border-app-orange bg-app-orange/10 text-app-orange-light"
          : "border-app-border-primary bg-app-bg-secondary text-app-text-muted"
      }`}
    >
      {expiring ? <AlertTriangle className="h-3 w-3 flex-shrink-0" /> : <Info className="h-3 w-3 flex-shrink-0" />}
      <span className="truncate">{notice.message}</span>
    </div>
  );
}
