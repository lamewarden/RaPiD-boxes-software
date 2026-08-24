import { AlertTriangle } from "lucide-react";

/** Shown once MoldWatchService confirms a mid-run anomaly (see
 *  ExperimentStatus.issueDetected/issueDetail) -- live over the WS while the
 *  progress screen is open, and still visible on a later view since it's
 *  part of the persisted status/metadata snapshot. */
export default function IssueDetectedBanner({
  detected,
  detail,
}: {
  detected: boolean;
  detail: string | null;
}) {
  if (!detected) return null;

  return (
    <div className="flex flex-shrink-0 items-center gap-1.5 rounded-lg border border-app-orange bg-app-orange/10 px-2.5 py-1 text-[10px] font-semibold text-app-orange">
      <AlertTriangle className="h-3 w-3 flex-shrink-0" />
      <span className="truncate">{detail || "A possible issue was detected in this run."}</span>
    </div>
  );
}
