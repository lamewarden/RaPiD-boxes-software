import { Copy, ExternalLink } from "lucide-react";
import { toast } from "sonner";

const PAPER_TITLE =
  "RaPiD-chamber: Easy to self-assemble live-imaging chamber with adjustable LEDs allows to track small differences in dynamic plant movement adaptation on tissue level";
const PAPER_DOI = "https://doi.org/10.1101/2022.08.13.503848";

/** Static credits/citation tab -- nothing here reads or writes any setting,
 *  so (like General) it gets neither the shared Default/Mine/Save row nor
 *  the read-only-while-running banner in SettingsMenu.tsx. */
export default function InfoSettingsMenu() {
  const handleCopyDoi = async () => {
    try {
      await navigator.clipboard.writeText(PAPER_DOI);
      toast.success("DOI link copied");
    } catch {
      toast.error("Could not copy — select the text and copy it manually");
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto p-2">
        <div className="flex flex-col gap-2">
          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              About
            </div>
            <p className="mt-1.5 text-[11px] leading-[16px] text-white">
              RaPiD-boxes was developed by the team of the IEB Prague Imaging Core Facility.
            </p>
          </div>

          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Credits
            </div>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-[11px]">
              <dt className="text-app-text-muted">Original prototype &amp; Backend</dt>
              <dd className="font-semibold text-white">Ivan Kashkan</dd>
              <dt className="text-app-text-muted">UI</dt>
              <dd className="font-semibold text-white">Judith Garcia Gonzalez</dd>
              <dt className="text-app-text-muted">Hardware prototyping</dt>
              <dd className="font-semibold text-white">Vojtěch Knirsch, Matěj Drs</dd>
              <dt className="text-app-text-muted">Head of Core Facility</dt>
              <dd className="font-semibold text-white">Malínská Kateřina</dd>
            </dl>
          </div>

          <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
              Original Publication
            </div>
            <p className="mt-1.5 text-[11px] leading-[16px] text-white">{PAPER_TITLE}</p>
            <button
              onClick={handleCopyDoi}
              title="Tap to copy"
              className="mt-2 flex w-full items-center justify-between gap-2 rounded-md bg-app-bg-tertiary px-3 py-2 transition-colors hover:bg-app-border-primary"
            >
              <span className="flex items-center gap-1.5 truncate font-mono text-[11px] text-white">
                <ExternalLink className="h-[12px] w-[12px] flex-shrink-0 text-app-text-secondary" strokeWidth={1.75} />
                {PAPER_DOI}
              </span>
              <Copy className="h-[14px] w-[14px] flex-shrink-0 text-app-text-secondary" strokeWidth={1.75} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
