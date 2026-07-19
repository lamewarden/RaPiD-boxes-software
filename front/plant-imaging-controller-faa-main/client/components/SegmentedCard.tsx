export default function SegmentedCard({
  label,
  options,
  footer,
}: {
  label: string;
  options: { key: string; label: string; active: boolean; onClick: () => void }[];
  footer?: string;
}) {
  return (
    <div className="flex h-[74px] flex-col justify-between gap-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2">
      <div className="text-app-text-muted text-[10px] font-bold leading-[15px] tracking-[0.5px] uppercase">
        {label}
      </div>
      <div className="flex h-8 gap-1.5">
        {options.map((o) => (
          <button
            key={o.key}
            onClick={o.onClick}
            className={`flex h-8 flex-1 items-center justify-center rounded-md px-2 text-[11px] font-bold transition-colors ${
              o.active
                ? "bg-app-green text-white"
                : "bg-app-bg-tertiary text-app-text-secondary hover:bg-app-border-primary"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <div className="h-3 text-[10px] leading-3 text-app-text-muted">{footer ?? ""}</div>
    </div>
  );
}
