import type { CSSProperties } from "react";

interface ParameterControlProps {
  label: string;
  value: string;
  valueColor: string;
  sliderColor: string;
  sliderValue: number;
  sliderMin: number;
  sliderMax: number;
  sliderStep?: number;
  disabled?: boolean;
  onSliderChange: (value: number) => void;
  onIncrement?: () => void;
  onDecrement?: () => void;
}

export default function ParameterControl({
  label,
  value,
  valueColor,
  sliderColor,
  sliderValue,
  sliderMin,
  sliderMax,
  sliderStep = 1,
  disabled = false,
  onSliderChange,
}: ParameterControlProps) {
  const sliderPercent = ((sliderValue - sliderMin) / (sliderMax - sliderMin)) * 100;
  const sliderStyle = {
    background: `linear-gradient(to right, ${sliderColor} 0%, ${sliderColor} ${sliderPercent}%, #364153 ${sliderPercent}%, #364153 100%)`,
    "--slider-thumb-color": sliderColor,
  } as CSSProperties;

  return (
    <div
      className={`flex h-[74px] flex-1 flex-col items-start justify-between rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-2 ${
        disabled ? "opacity-60" : ""
      }`}
    >
      <div className="flex w-full pb-0.5 flex-col items-start">
        <div className="flex flex-col items-start self-stretch">
          <div className="self-stretch text-app-text-muted text-[10px] font-bold leading-[15px] tracking-[0.5px] uppercase">
            {label}
          </div>
        </div>
      </div>
      <div className="flex w-full pt-0.5 items-center gap-2">
        <div
          className="font-black text-[17px] leading-5 min-w-[50px] text-right"
          style={{ color: valueColor }}
        >
          {value}
        </div>
        <input
          type="range"
          min={sliderMin}
          max={sliderMax}
          step={sliderStep}
          value={sliderValue}
          disabled={disabled}
          onChange={(e) => onSliderChange(Number(e.target.value))}
          className={`app-range-slider flex-1 appearance-none rounded-lg bg-app-bg-tertiary ${
            disabled ? "cursor-not-allowed" : "cursor-pointer"
          }`}
          style={sliderStyle}
        />
      </div>
    </div>
  );
}
