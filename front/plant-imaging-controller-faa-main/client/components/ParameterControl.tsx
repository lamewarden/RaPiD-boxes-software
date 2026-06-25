import type { CSSProperties } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface ParameterControlProps {
  label: string;
  value: string;
  valueColor: string;
  sliderColor: string;
  sliderValue: number;
  sliderMin: number;
  sliderMax: number;
  sliderStep?: number;
  onSliderChange: (value: number) => void;
  onIncrement: () => void;
  onDecrement: () => void;
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
  onSliderChange,
  onIncrement,
  onDecrement,
}: ParameterControlProps) {
  const sliderPercent = ((sliderValue - sliderMin) / (sliderMax - sliderMin)) * 100;
  const sliderStyle = {
    background: `linear-gradient(to right, ${sliderColor} 0%, ${sliderColor} ${sliderPercent}%, #364153 ${sliderPercent}%, #364153 100%)`,
    "--slider-thumb-color": sliderColor,
  } as CSSProperties;

  return (
    <div className="flex h-[88px] p-2 flex-col justify-between items-start flex-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
      <div className="flex w-full pb-0.5 flex-col items-start">
        <div className="flex flex-col items-start self-stretch">
          <div className="self-stretch text-app-text-muted text-[10px] font-bold leading-[15px] tracking-[0.5px] uppercase">
            {label}
          </div>
        </div>
      </div>
      
      <div className="flex w-full justify-between items-center">
        <button
          onClick={onDecrement}
          className="flex p-1 flex-col items-start rounded bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <ChevronDown className="w-3.5 h-3.5 text-white" strokeWidth={1.17} />
        </button>
        
        <div className="flex flex-col items-start">
          <div
            className={`font-black text-[19px] leading-5`}
            style={{ color: valueColor }}
          >
            {value}
          </div>
        </div>
        
        <button
          onClick={onIncrement}
          className="flex p-1 flex-col items-start rounded bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <ChevronUp className="w-3.5 h-3.5 text-white" strokeWidth={1.17} />
        </button>
      </div>

      <div className="flex w-full pt-1 flex-col items-start">
        <input
          type="range"
          min={sliderMin}
          max={sliderMax}
          step={sliderStep}
          value={sliderValue}
          onChange={(e) => onSliderChange(Number(e.target.value))}
          className="app-range-slider w-full h-2 bg-app-bg-tertiary rounded-lg appearance-none cursor-pointer"
          style={sliderStyle}
        />
      </div>
    </div>
  );
}
