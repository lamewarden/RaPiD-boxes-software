import { useCallback, useRef, useState } from "react";
import LowSpaceDialog from "@/components/LowSpaceDialog";
import { api } from "@/lib/api";
import type { ExperimentConfig, StartResponse } from "@shared/api";

interface Pending {
  estimatedBytes: number | null;
  availableBytes: number | null;
  suggestion: StartResponse["suggestion"];
}

/** Wraps api.startExperiment() with the "low_space" retry flow: if the
 *  estimated footprint doesn't fit, blocks on a confirm dialog offering to
 *  delete the user's own oldest experiments, then retries once.
 *
 *  Returns null if the user cancelled the dialog (caller should just stop,
 *  same as any other "stay on this screen" outcome); otherwise the final
 *  StartResponse, which may still be "low_space" if nothing was deletable. */
export function useLowSpaceGuard() {
  const [pending, setPending] = useState<Pending | null>(null);
  const resolverRef = useRef<((confirmed: boolean) => void) | null>(null);

  const resolve = (confirmed: boolean) => {
    resolverRef.current?.(confirmed);
    resolverRef.current = null;
  };

  const guardedStart = useCallback(
    async (config: ExperimentConfig): Promise<StartResponse | null> => {
      let res = await api.startExperiment(config);
      if (res.status !== "low_space") return res;

      setPending({
        estimatedBytes: res.estimatedBytes ?? null,
        availableBytes: res.availableBytes ?? null,
        suggestion: res.suggestion ?? null,
      });
      const confirmed = await new Promise<boolean>((r) => {
        resolverRef.current = r;
      });
      setPending(null);

      if (!confirmed || !res.suggestion) return res;

      await api.freeSpace(config.username, res.suggestion.experimentIds);
      res = await api.startExperiment(config);
      return res;
    },
    []
  );

  const dialog = pending ? (
    <LowSpaceDialog
      estimatedBytes={pending.estimatedBytes}
      availableBytes={pending.availableBytes}
      suggestion={pending.suggestion ?? null}
      onCancel={() => resolve(false)}
      onConfirm={() => resolve(true)}
    />
  ) : null;

  return { guardedStart, dialog };
}
