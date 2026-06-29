import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { SavedExperimentConfig } from "@shared/api";

export default function ImportConfigMenu({
  onClose,
  onLoad,
}: {
  onClose: () => void;
  onLoad: (config: SavedExperimentConfig) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: () => api.history(),
  });
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const entries = data ?? [];

  const handlePick = async (id: string) => {
    if (loadingId) return;
    setLoadingId(id);
    try {
      const config = await api.experimentConfig(id);
      onLoad(config);
      onClose();
    } catch (e) {
      toast.error(`Could not load that experiment's config: ${(e as Error).message}`);
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">
      <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
        <span className="text-[15px] font-bold uppercase tracking-wide text-white">
          Import Experiment Config
        </span>
        <button
          onClick={onClose}
          className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
        >
          <X className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-app-text-muted">
            Loading…
          </div>
        ) : entries.length === 0 ? (
          <p className="mt-12 text-center text-app-text-muted">No previous experiments yet.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {entries.map((entry) => (
              <button
                key={entry.id}
                onClick={() => handlePick(entry.id)}
                disabled={loadingId !== null}
                className="flex items-center justify-between gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2.5 text-left transition-colors hover:border-app-green disabled:cursor-not-allowed disabled:opacity-60"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-bold text-white">
                    {entry.name ?? entry.id}
                  </div>
                  <div className="truncate text-[10px] text-app-text-muted">
                    {entry.startedAt ?? "unknown date"} · {entry.username ?? "?"} ·{" "}
                    {entry.imagesCaptured} images
                  </div>
                </div>
                <Download className="h-[16px] w-[16px] flex-shrink-0 text-app-text-secondary" strokeWidth={1.5} />
                {loadingId === entry.id && (
                  <span className="flex-shrink-0 text-[10px] text-app-text-muted">Loading…</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
