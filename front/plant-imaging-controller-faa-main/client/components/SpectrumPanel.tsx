const SPECTRA = ["white", "red", "green", "blue"] as const;

const SPECTRUM_STYLES: Record<
  (typeof SPECTRA)[number],
  { bg: string; bgSelected: string; text: string; textSelected: string; border: string }
> = {
  white: {
    bg: "bg-white/20",
    bgSelected: "bg-white shadow-[0_0_12px_0_rgba(255,255,255,0.4)]",
    text: "text-white/60",
    textSelected: "text-[#101828]",
    border: "border-white/30",
  },
  red: {
    bg: "bg-[rgba(251,44,54,0.2)]",
    bgSelected: "bg-[rgba(251,44,54,0.4)]",
    text: "text-[rgba(255,162,162,0.6)]",
    textSelected: "text-[rgba(255,162,162,1)]",
    border: "border-[rgba(255,162,162,0.5)]",
  },
  green: {
    bg: "bg-[rgba(0,201,80,0.2)]",
    bgSelected: "bg-[rgba(0,201,80,0.4)]",
    text: "text-[rgba(123,241,168,0.6)]",
    textSelected: "text-[rgba(123,241,168,1)]",
    border: "border-[rgba(123,241,168,0.5)]",
  },
  blue: {
    bg: "bg-[rgba(43,127,255,0.2)]",
    bgSelected: "bg-[rgba(43,127,255,0.4)]",
    text: "text-[rgba(142,197,255,0.6)]",
    textSelected: "text-[rgba(142,197,255,1)]",
    border: "border-[rgba(142,197,255,0.5)]",
  },
};

interface SpectrumPanelProps {
  label?: string;
  selected: Set<string>;
  onToggle: (spectrum: string) => void;
}

export default function SpectrumPanel({
  label = "Spectrum (Optional)",
  selected,
  onToggle,
}: SpectrumPanelProps) {
  return (
    <div className="flex p-2 flex-col items-start gap-1.5 self-stretch rounded-[10px] border border-app-border-primary bg-app-bg-secondary flex-shrink-0">
      <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase">
        {label}
      </div>
      <div className="flex w-full items-start gap-2 flex-wrap">
        {SPECTRA.map((spectrum) => {
          const isSelected = selected.has(spectrum);
          const s = SPECTRUM_STYLES[spectrum];
          return (
            <button
              key={spectrum}
              onClick={() => onToggle(spectrum)}
              className={`flex py-2 px-5 flex-col justify-center items-center rounded border transition-all cursor-pointer ${
                isSelected ? s.border : "border-transparent"
              } ${isSelected ? s.bgSelected : s.bg} flex-1 min-w-[60px]`}
            >
              <div
                className={`text-center text-[10px] font-bold leading-[15px] uppercase ${
                  isSelected ? s.textSelected : s.text
                }`}
              >
                {spectrum}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
