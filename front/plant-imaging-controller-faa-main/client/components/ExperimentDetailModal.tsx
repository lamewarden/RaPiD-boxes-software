import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { formatDurationLong } from "@/lib/progress";
import type { ExperimentPhase, ExperimentStatus } from "@shared/api";

const PHASE_LABEL: Partial<Record<ExperimentPhase, string>> = {
  baseline: "Baseline photo",
  day: "Day (lit)",
  night: "Night (dark)",
  dark: "Dark (apical hook)",
  bending: "Bending (lateral light)",
};

/** Not full-screen -- a centered card, since this is reference detail the
 *  user opens alongside the still-live progress screen behind it, not a
 *  destination in its own right. Content can run long (a multi-day growth
 *  plan is one row per day/night phase), so it scrolls internally rather
 *  than trying to force everything above the fold. */
export default function ExperimentDetailModal({
  status,
  onClose,
}: {
  status: ExperimentStatus;
  onClose: () => void;
}) {
  const experimentId = status.experimentId;
  const { data: saved } = useQuery({
    queryKey: ["experimentConfig", experimentId],
    queryFn: () => api.experimentConfig(experimentId as string),
    enabled: !!experimentId,
  });
  const { data: sync } = useQuery({
    queryKey: ["remoteSync"],
    queryFn: () => api.remoteSync(),
  });

  const isGrowth = status.config?.protocol === "growth";
  const intensity = saved
    ? isGrowth
      ? saved.dayIntensity
      : saved.intensity
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85%] w-full max-w-[560px] flex-col rounded-xl border border-app-border-primary bg-app-bg-secondary shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-app-border-primary px-3 py-2">
          <span className="text-[13px] font-bold uppercase tracking-wide text-white">
            Experiment Details
          </span>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
          >
            <X className="h-[16px] w-[16px]" strokeWidth={1.5} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-3">
          <section>
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Overview
            </div>
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-app-text-secondary">
              <div>
                Protocol: <span className="text-white">{isGrowth ? "Growth" : "Tropism"}</span>
              </div>
              <div>
                State: <span className="text-white">{status.state}</span>
              </div>
              <div>
                Total time:{" "}
                <span className="text-white">{formatDurationLong(status.totalSeconds)}</span>
              </div>
              <div>
                Images: <span className="text-white">{status.imagesCaptured} / {status.imagesPlanned}</span>
              </div>
            </div>
          </section>

          <section>
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Light Conditions
            </div>
            {saved ? (
              <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-app-text-secondary">
                <div>
                  Photo source:{" "}
                  <span className="text-white">{saved.photoIlluminationSource.toUpperCase()}</span>
                </div>
                <div>
                  Intensity: <span className="text-white">{intensity}%</span>
                </div>
                <div className="col-span-2">
                  Spectra: <span className="text-white">{saved.spectra.join(", ") || "—"}</span>
                </div>
              </div>
            ) : (
              <p className="mt-1 text-[11px] text-app-text-muted">Loading…</p>
            )}
          </section>

          <section>
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Phases ({status.phases.length})
            </div>
            <div className="mt-1 flex flex-col gap-1">
              {status.phases.length === 0 ? (
                <p className="text-[11px] text-app-text-muted">No phase plan available.</p>
              ) : (
                status.phases.map((p, i) => {
                  const current = i === status.currentPhaseIndex;
                  return (
                    <div
                      key={i}
                      className={`flex items-center justify-between gap-2 rounded-md border px-2 py-1.5 text-[11px] ${
                        current
                          ? "border-app-green/60 bg-app-green/10"
                          : "border-app-border-primary bg-app-bg-tertiary"
                      }`}
                    >
                      <span className={current ? "font-bold text-white" : "text-app-text-secondary"}>
                        {PHASE_LABEL[p.name] ?? p.name}
                        {p.dayIndex != null ? ` · Day ${p.dayIndex}` : ""}
                      </span>
                      <span className="flex-shrink-0 text-app-text-muted">
                        {formatDurationLong(p.durationSeconds)}
                        {p.capture ? ` · ${p.imagesPlanned} img` : " · no capture"}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </section>

          <section>
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Storage
            </div>
            <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-app-text-secondary">
              <div>
                Used so far: <span className="text-white">{formatBytes(status.bytesUsed)}</span>
              </div>
              <div>
                Estimated total:{" "}
                <span className="text-white">
                  {status.estimatedTotalBytes != null ? formatBytes(status.estimatedTotalBytes) : "—"}
                </span>
              </div>
            </div>
          </section>

          <section>
            <div className="text-[9px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Remote Sync
            </div>
            {sync ? (
              <p className="mt-1 text-[11px] text-app-text-secondary">
                {sync.enabled ? (
                  sync.credentialsRequired ? (
                    <span className="text-app-orange-light">On, but needs the password re-entered.</span>
                  ) : (
                    <>
                      <span className="text-white">On</span> · {sync.mounted ? "mounted" : "not mounted"} ·{" "}
                      {sync.pendingCount} pending
                    </>
                  )
                ) : (
                  <span className="text-app-text-muted">Off — images stay local only.</span>
                )}
              </p>
            ) : (
              <p className="mt-1 text-[11px] text-app-text-muted">Loading…</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
