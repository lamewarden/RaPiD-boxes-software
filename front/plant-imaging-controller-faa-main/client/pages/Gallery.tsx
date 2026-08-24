import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Download, Folder, Sprout, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import RunningExperimentButton from "@/components/RunningExperimentButton";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import type { HistoryEntry, ImageInfo, PlantMaskStatus } from "@shared/api";

type GalleryNavState = {
  experimentId?: string | null;
  returnTo?: string;
};

function slugUser(username: string): string {
  return username.trim().replace(/[^A-Za-z0-9._-]+/g, "-") || "x";
}

/** Match history entry to the on-screen session user (metadata or folder slug). */
function belongsToUser(entry: HistoryEntry, username: string): boolean {
  const want = username.trim().toLowerCase();
  if (!want) return false;
  if (entry.username && entry.username.trim().toLowerCase() === want) return true;
  const parts = entry.id.split("_");
  // {YYYY-MM-DD}_{userSlug}_{name...}
  if (parts.length >= 3 && parts[1].toLowerCase() === slugUser(username).toLowerCase()) {
    return true;
  }
  return false;
}

type GrowthView =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; url: string; frameCount: number }
  | { kind: "error"; message: string };

type PlantView =
  | { kind: "idle" }
  | { kind: "running"; status: PlantMaskStatus }
  | { kind: "ready"; overlayUrl: string; maskUrl?: string }
  | { kind: "error"; message: string };

export default function Gallery() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const navState = (location.state as GalleryNavState | null) ?? null;
  const navExperimentId = navState?.experimentId ?? undefined;
  const returnTo = navState?.returnTo ?? "/";
  const username = getUsername();

  const [activeExperimentId, setActiveExperimentId] = useState<string | undefined>(navExperimentId);
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);
  const [selected, setSelected] = useState<ImageInfo | null>(null);
  const [growth, setGrowth] = useState<GrowthView>({ kind: "idle" });
  const [plant, setPlant] = useState<PlantView>({ kind: "idle" });
  const pollRef = useRef<number | null>(null);
  const { status } = useExperimentStatus();
  const runningId =
    status?.state === "running" || status?.state === "paused" ? status.experimentId : null;

  // Two-tap delete: first tap arms (turns red), a second tap within the
  // window actually deletes. Auto-disarms so an armed button doesn't stay a
  // trap if the user wanders off -- this is real, irreversible data loss on
  // a shared kiosk, worth the extra tap.
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deletingAll, setDeletingAll] = useState(false);
  const disarmTimer = useRef<number | null>(null);

  useEffect(() => {
    setActiveExperimentId(navExperimentId);
  }, [navExperimentId]);

  const { data, isLoading } = useQuery({
    queryKey: ["images", activeExperimentId ?? "current"],
    queryFn: () => api.images(activeExperimentId),
    refetchInterval: 5000,
  });

  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["history"],
    queryFn: () => api.history(),
    enabled: folderPickerOpen,
  });

  const userFolders = useMemo(() => {
    const entries = history ?? [];
    return entries.filter((e) => belongsToUser(e, username));
  }, [history, username]);

  const images = data?.images ?? [];
  const captures = images.filter((img) => img.phase !== "artifact");
  const resolvedId = data?.experimentId ?? activeExperimentId ?? null;
  const canAnalyze = Boolean(resolvedId) && captures.length >= 2;

  useEffect(() => {
    return () => {
      if (pollRef.current != null) window.clearInterval(pollRef.current);
      if (disarmTimer.current != null) window.clearTimeout(disarmTimer.current);
    };
  }, []);

  const openGrowth = () => {
    if (!resolvedId || captures.length < 2) return;
    const url = api.growthUrl(resolvedId, captures.length);
    setGrowth({ kind: "loading" });
    const img = new Image();
    img.onload = () => setGrowth({ kind: "ready", url, frameCount: captures.length });
    img.onerror = () =>
      setGrowth({ kind: "error", message: "Could not compute growth view (need clearer changes)." });
    img.src = url;
  };

  const closeGrowth = () => setGrowth({ kind: "idle" });

  const stopPlantPoll = () => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const applyPlantStatus = (status: PlantMaskStatus) => {
    if (status.status === "done") {
      stopPlantPoll();
      const overlayUrl = status.overlayUrl
        ? `${status.overlayUrl}?v=${Date.now()}`
        : "";
      setPlant({
        kind: "ready",
        overlayUrl,
        maskUrl: status.maskUrl,
      });
      void queryClient.invalidateQueries({ queryKey: ["images", activeExperimentId ?? "current"] });
      return;
    }
    if (status.status === "error") {
      stopPlantPoll();
      setPlant({ kind: "error", message: status.error || status.message || "Plant mask failed" });
      return;
    }
    setPlant({ kind: "running", status });
  };

  const openPlantShape = async () => {
    if (!resolvedId || captures.length < 2) return;
    stopPlantPoll();
    setPlant({
      kind: "running",
      status: { status: "running", current: 0, total: captures.length, message: "Starting…", percent: 0 },
    });
    try {
      const started = await api.startPlantMask(resolvedId);
      applyPlantStatus(started);
      if (started.status === "done" || started.status === "error") return;
      pollRef.current = window.setInterval(() => {
        void api
          .plantMaskStatus(resolvedId)
          .then(applyPlantStatus)
          .catch((err: Error) => {
            stopPlantPoll();
            setPlant({ kind: "error", message: err.message });
          });
      }, 500);
    } catch (err) {
      setPlant({ kind: "error", message: err instanceof Error ? err.message : "Plant mask failed" });
    }
  };

  const closePlant = () => {
    stopPlantPoll();
    setPlant({ kind: "idle" });
  };

  const pickFolder = (id: string) => {
    stopPlantPoll();
    setGrowth({ kind: "idle" });
    setPlant({ kind: "idle" });
    setSelected(null);
    setActiveExperimentId(id);
    setFolderPickerOpen(false);
  };

  const formatWhen = (startedAt: string | null) => {
    if (!startedAt) return "unknown date";
    try {
      return new Date(startedAt).toLocaleString();
    } catch {
      return startedAt;
    }
  };

  const disarmDelete = () => {
    if (disarmTimer.current != null) window.clearTimeout(disarmTimer.current);
    setConfirmDeleteId(null);
    setConfirmDeleteAll(false);
  };

  const armDelete = (id: string | "all") => {
    if (disarmTimer.current != null) window.clearTimeout(disarmTimer.current);
    setConfirmDeleteId(id === "all" ? null : id);
    setConfirmDeleteAll(id === "all");
    disarmTimer.current = window.setTimeout(disarmDelete, 4000);
  };

  const handleDeleteOne = async (id: string) => {
    if (confirmDeleteId !== id) {
      armDelete(id);
      return;
    }
    disarmDelete();
    setDeletingId(id);
    try {
      await api.freeSpace(username, [id]);
      await queryClient.invalidateQueries({ queryKey: ["history"] });
      if (activeExperimentId === id) setActiveExperimentId(undefined);
      toast.success("Folder deleted.");
    } catch (e) {
      toast.error(`Could not delete: ${(e as Error).message}`);
    } finally {
      setDeletingId(null);
    }
  };

  const handleDeleteAll = async () => {
    if (!confirmDeleteAll) {
      armDelete("all");
      return;
    }
    disarmDelete();
    const ids = userFolders.map((e) => e.id);
    if (ids.length === 0) return;
    setDeletingAll(true);
    try {
      const res = await api.freeSpace(username, ids);
      await queryClient.invalidateQueries({ queryKey: ["history"] });
      setActiveExperimentId(undefined);
      const skipped = ids.length - res.deletedIds.length;
      toast.success(
        skipped > 0
          ? `Deleted ${res.deletedIds.length} folder(s) — ${skipped} skipped (currently running).`
          : `Deleted ${res.deletedIds.length} folder(s).`,
      );
    } catch (e) {
      toast.error(`Could not delete all: ${(e as Error).message}`);
    } finally {
      setDeletingAll(false);
    }
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col bg-app-bg-primary">
      <div className="flex p-0.5 justify-between items-center self-stretch border-b border-app-border-primary bg-app-bg-secondary">
        <div className="flex items-center gap-1">
          <Link
            to={returnTo}
            className="flex min-w-[100px] px-3 py-1.5 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
          >
            <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
            <span className="text-white text-[13px] font-semibold">Close</span>
          </Link>
          <button
            type="button"
            onClick={() => setFolderPickerOpen(true)}
            className="flex min-w-[100px] px-3 py-1.5 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
          >
            <Folder className="w-[18px] h-[18px]" strokeWidth={1.5} />
            <span className="text-white text-[13px] font-semibold">Folders</span>
          </button>
          <button
            type="button"
            disabled={!canAnalyze}
            onClick={openGrowth}
            className="flex min-w-[110px] px-3 py-1.5 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors disabled:opacity-40 disabled:pointer-events-none"
          >
            <Activity className="w-[18px] h-[18px]" strokeWidth={1.5} />
            <span className="text-white text-[13px] font-semibold">Show growth</span>
          </button>
          <button
            type="button"
            disabled={!canAnalyze || plant.kind === "running"}
            onClick={() => void openPlantShape()}
            className="flex min-w-[110px] px-3 py-1.5 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors disabled:opacity-40 disabled:pointer-events-none"
          >
            <Sprout className="w-[18px] h-[18px]" strokeWidth={1.5} />
            <span className="text-white text-[13px] font-semibold">Plant shape</span>
          </button>
          <RunningExperimentButton />
        </div>
        <button
          type="button"
          onClick={() => setFolderPickerOpen(true)}
          title="Browse this user's experiment folders"
          className="max-w-[280px] truncate px-3 text-[13px] font-semibold text-app-text-secondary hover:text-white transition-colors"
        >
          {resolvedId ?? "No experiment"} · {captures.length} images
        </button>
      </div>

      {folderPickerOpen && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-app-bg-primary">
          <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
            <span className="text-[15px] font-bold uppercase tracking-wide text-white">
              Experiments · {username}
            </span>
            <div className="flex items-center gap-1.5">
              <a
                href={userFolders.length > 0 ? api.experimentDownloadAllUrl(username) : undefined}
                download
                title={userFolders.length === 0 ? "No folders to download" : "Download everything as one ZIP"}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-semibold text-white transition-colors ${
                  userFolders.length === 0
                    ? "pointer-events-none bg-app-bg-tertiary opacity-40"
                    : "bg-app-bg-tertiary hover:bg-app-border-primary"
                }`}
              >
                <Download className="h-[14px] w-[14px]" strokeWidth={1.5} />
                Download All
              </a>
              <button
                type="button"
                disabled={userFolders.length === 0 || deletingAll}
                onClick={() => void handleDeleteAll()}
                title={confirmDeleteAll ? "Tap again to permanently delete everything" : "Delete all folders"}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-semibold text-white transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                  confirmDeleteAll ? "bg-red-600 hover:bg-red-500" : "bg-app-bg-tertiary hover:bg-app-border-primary"
                }`}
              >
                <Trash2 className="h-[14px] w-[14px]" strokeWidth={1.5} />
                {deletingAll ? "Deleting…" : confirmDeleteAll ? "Confirm Delete?" : "Delete All"}
              </button>
              <button
                type="button"
                onClick={() => {
                  disarmDelete();
                  setFolderPickerOpen(false);
                }}
                className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
              >
                <X className="h-[18px] w-[18px]" strokeWidth={1.5} />
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {historyLoading ? (
              <div className="flex h-full items-center justify-center text-sm text-app-text-muted">
                Loading…
              </div>
            ) : userFolders.length === 0 ? (
              <p className="mt-12 text-center text-app-text-muted">
                No experiment folders for user “{username}”.
              </p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {userFolders.map((entry) => {
                  const active = entry.id === resolvedId;
                  const isRunning = entry.id === runningId;
                  const armed = confirmDeleteId === entry.id;
                  return (
                    <div
                      key={entry.id}
                      className={`flex items-center gap-2 rounded-[10px] border p-2.5 transition-colors ${
                        active
                          ? "border-app-green bg-app-bg-tertiary"
                          : "border-app-border-primary bg-app-bg-secondary hover:border-app-green"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => pickFolder(entry.id)}
                        className="min-w-0 flex-1 text-left"
                      >
                        <div className="truncate text-[13px] font-bold text-white">
                          {entry.name ?? entry.id}
                        </div>
                        <div className="truncate text-[10px] text-app-text-muted">
                          {formatWhen(entry.startedAt)} · {entry.imagesCaptured} images · {entry.id}
                        </div>
                      </button>
                      {active && (
                        <span className="flex-shrink-0 text-[10px] font-semibold text-app-green">
                          Open
                        </span>
                      )}
                      <a
                        href={api.experimentDownloadUrl(entry.id)}
                        download
                        title="Download this folder as a ZIP"
                        className="flex-shrink-0 rounded-md bg-app-bg-tertiary p-1.5 text-white transition-colors hover:bg-app-border-primary"
                      >
                        <Download className="h-[14px] w-[14px]" strokeWidth={1.5} />
                      </a>
                      <button
                        type="button"
                        disabled={isRunning || deletingId === entry.id}
                        onClick={() => void handleDeleteOne(entry.id)}
                        title={
                          isRunning
                            ? "Cannot delete the currently running experiment"
                            : armed
                              ? "Tap again to permanently delete"
                              : "Delete this folder"
                        }
                        className={`flex-shrink-0 rounded-md p-1.5 text-white transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                          armed ? "bg-red-600 hover:bg-red-500" : "bg-app-bg-tertiary hover:bg-app-border-primary"
                        }`}
                      >
                        <Trash2 className="h-[14px] w-[14px]" strokeWidth={1.5} />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-3">
        {isLoading ? (
          <p className="text-center text-app-text-muted">Loading…</p>
        ) : images.length === 0 ? (
          <p className="mt-12 text-center text-app-text-muted">No images captured yet.</p>
        ) : (
          <div className="grid grid-cols-5 gap-2">
            {images.map((img) => (
              <button
                key={img.id}
                onClick={() => setSelected(img)}
                className="overflow-hidden rounded-md border border-app-border-primary bg-app-bg-secondary hover:border-app-green"
              >
                <img src={img.thumbUrl} alt={img.id} className="aspect-[4/3] w-full object-cover" />
                <div className="truncate px-1 py-0.5 text-[9px] text-app-text-muted">{img.id}</div>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={() => setSelected(null)}
        >
          <img src={selected.url} alt={selected.id} className="max-h-[88%] max-w-[92%] rounded-lg object-contain" />
          <p className="mt-2 text-sm text-white">{selected.id}</p>
        </div>
      )}

      {growth.kind === "loading" && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={closeGrowth}
        >
          <p className="text-sm text-white">Computing growth…</p>
        </div>
      )}

      {growth.kind === "error" && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={closeGrowth}
        >
          <p className="text-sm text-white">{growth.message}</p>
        </div>
      )}

      {growth.kind === "ready" && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={closeGrowth}
        >
          <img
            src={growth.url}
            alt="Growth dynamics"
            className="max-h-[88%] max-w-[92%] rounded-lg object-contain"
          />
          <p className="mt-2 text-sm text-white">
            Growth dynamics · {growth.frameCount} frames
          </p>
          <p className="mt-0.5 text-[11px] text-white/70">cool = older change · hot = recent</p>
        </div>
      )}

      {plant.kind === "running" && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4">
          <p className="text-sm text-white mb-3">Extracting plant shape…</p>
          <div className="w-[320px] h-3 rounded bg-white/20 overflow-hidden">
            <div
              className="h-full bg-app-green transition-[width] duration-300"
              style={{ width: `${Math.min(100, plant.status.percent ?? 0)}%` }}
            />
          </div>
          <p className="mt-2 text-[12px] text-white/70">
            {plant.status.message || `${plant.status.current}/${plant.status.total}`}
          </p>
        </div>
      )}

      {plant.kind === "error" && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={closePlant}
        >
          <p className="text-sm text-white">{plant.message}</p>
        </div>
      )}

      {plant.kind === "ready" && (
        <div
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 p-4"
          onClick={closePlant}
        >
          <img
            src={plant.overlayUrl}
            alt="Plant shape overlay"
            className="max-h-[88%] max-w-[92%] rounded-lg object-contain"
          />
          <p className="mt-2 text-sm text-white">Plant shape · mask saved in gallery</p>
          <p className="mt-0.5 text-[11px] text-white/70">green = motion-traced plant region</p>
        </div>
      )}
    </div>
  );
}
