import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import RunningExperimentButton from "@/components/RunningExperimentButton";
import { api } from "@/lib/api";
import type { ImageInfo } from "@shared/api";

type GalleryNavState = {
  experimentId?: string | null;
  returnTo?: string;
};

export default function Gallery() {
  const location = useLocation();
  const navState = (location.state as GalleryNavState | null) ?? null;
  const experimentId = navState?.experimentId ?? undefined;
  const returnTo = navState?.returnTo ?? "/";

  const { data, isLoading } = useQuery({
    queryKey: ["images", experimentId ?? "current"],
    queryFn: () => api.images(experimentId),
    refetchInterval: 5000,
  });
  const [selected, setSelected] = useState<ImageInfo | null>(null);
  const images = data?.images ?? [];

  return (
    <div className="flex w-[800px] h-[452px] flex-col bg-app-bg-primary">
      <div className="flex p-0.5 justify-between items-center self-stretch border-b border-app-border-primary bg-app-bg-secondary">
        <div className="flex items-center gap-1">
          <Link
            to={returnTo}
            className="flex min-w-[160px] px-3 py-1.5 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
          >
            <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
            <span className="text-white text-[13px] font-semibold">Close gallery</span>
          </Link>
          <RunningExperimentButton />
        </div>
        <span className="px-3 text-[13px] font-semibold text-app-text-secondary">
          {data?.experimentId ?? experimentId ?? "No experiment"} · {images.length} images
        </span>
      </div>

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
    </div>
  );
}
