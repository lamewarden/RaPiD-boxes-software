import { useQuery } from "@tanstack/react-query";
import { FolderDown, X } from "lucide-react";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";

export default function DownloadsMenu({ onClose }: { onClose: () => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: () => api.history(),
  });
  const username = getUsername();
  // No server-side auth on this box -- this filter is purely a UI convenience
  // so a researcher sees "my experiments" rather than everyone's.
  const entries = (data ?? []).filter((entry) => entry.username === username);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">
      <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
        <span className="text-[15px] font-bold uppercase tracking-wide text-white">
          My Experiment Files
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
          <p className="mt-12 text-center text-app-text-muted">
            No experiments found for "{username}" yet.
          </p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {entries.map((entry) => (
              <div
                key={entry.id}
                className="flex items-center justify-between gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-bold text-white">
                    {entry.name ?? entry.id}
                  </div>
                  <div className="truncate text-[10px] text-app-text-muted">
                    {entry.startedAt ?? "unknown date"} · {entry.imagesCaptured} images
                  </div>
                </div>
                <a
                  href={api.experimentDownloadUrl(entry.id)}
                  download
                  className="flex flex-shrink-0 items-center gap-1.5 rounded-md bg-app-bg-tertiary px-2.5 py-1.5 text-[11px] font-semibold text-white transition-colors hover:bg-app-border-primary"
                >
                  <FolderDown className="h-[14px] w-[14px]" strokeWidth={1.5} />
                  Download ZIP
                </a>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
