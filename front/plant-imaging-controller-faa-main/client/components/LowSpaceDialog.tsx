import { HardDrive, X } from "lucide-react";
import { formatBytes } from "@/lib/format";
import type { StorageSuggestion } from "@shared/api";

/** Blocking confirm shown when starting an experiment doesn't fit free disk
 *  space. Only ever offers to delete the requesting user's own folders. */
export default function LowSpaceDialog({
  estimatedBytes,
  availableBytes,
  suggestion,
  onCancel,
  onConfirm,
}: {
  estimatedBytes: number | null;
  availableBytes: number | null;
  suggestion: StorageSuggestion | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-[420px] rounded-xl border border-app-border-primary bg-app-bg-secondary p-4 shadow-2xl">
        <div className="mb-2 flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-sm font-bold uppercase tracking-wide text-app-orange">
            <HardDrive className="h-4 w-4" />
            Not enough storage
          </span>
          <button onClick={onCancel} className="rounded p-1 text-app-text-secondary hover:bg-app-bg-tertiary">
            <X className="h-5 w-5" />
          </button>
        </div>

        <p className="mb-3 text-[13px] text-app-text-secondary">
          This experiment needs about{" "}
          <span className="font-semibold text-white">
            {estimatedBytes != null ? formatBytes(estimatedBytes) : "an unknown amount"}
          </span>
          , but only{" "}
          <span className="font-semibold text-white">
            {availableBytes != null ? formatBytes(availableBytes) : "very little"}
          </span>{" "}
          is free.
        </p>

        {suggestion ? (
          <>
            <p className="mb-3 text-[13px] text-app-text-secondary">
              Delete your <span className="font-semibold text-white">{suggestion.count}</span> oldest
              experiment{suggestion.count === 1 ? "" : "s"} (
              <span className="font-semibold text-white">{formatBytes(suggestion.freedBytes)}</span>) to
              free enough space?
            </p>
            <div className="flex gap-1.5">
              <button
                onClick={onCancel}
                className="flex-1 rounded-md border border-app-border-primary bg-app-bg-tertiary py-2 text-[12px] font-semibold text-white hover:bg-app-border-primary"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                className="flex-1 rounded-md bg-red-600 py-2 text-[12px] font-bold text-white hover:bg-red-500"
              >
                Delete &amp; Continue
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mb-3 text-[13px] text-app-orange-light">
              Not enough storage even after deleting all of your own experiments. Free up space
              manually, or use a different device.
            </p>
            <button
              onClick={onCancel}
              className="w-full rounded-md border border-app-border-primary bg-app-bg-tertiary py-2 text-[12px] font-semibold text-white hover:bg-app-border-primary"
            >
              Close
            </button>
          </>
        )}
      </div>
    </div>
  );
}
