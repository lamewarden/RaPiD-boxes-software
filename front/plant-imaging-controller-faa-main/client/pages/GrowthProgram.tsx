import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Play, RotateCcw, Tag } from "lucide-react";
import { toast } from "sonner";
import TopNav from "@/components/TopNav";
import ProgramTabs from "@/components/ProgramTabs";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import ParameterControl from "@/components/ParameterControl";
import SpectrumPanel from "@/components/SpectrumPanel";
import { api } from "@/lib/api";
import { getExperimentName, getUsername, setExperimentName } from "@/lib/session";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import type { Spectrum } from "@shared/api";

const DAY_COLOR = "#F0B100";
const EXPERIMENT_LENGTH_COLOR = "#2B7FFF";
const INTENSITY_COLOR = "#FF6900";
const INTERVAL_COLOR = "#51A2FF";

const DEFAULT_VALUES = {
  preIlluminationEnabled: false,
  dayLengthHours: 16,
  experimentLengthDays: 14,
  selectedSpectra: new Set(["white"]),
  dayIntensity: 25,
  interval: 30,
  photoIlluminationSource: "ir" as "ir" | "rgbw",
};

export default function GrowthProgram() {
  const navigate = useNavigate();
  const [preIlluminationEnabled, setPreIlluminationEnabled] = useState(
    DEFAULT_VALUES.preIlluminationEnabled
  );
  const [dayLengthHours, setDayLengthHours] = useState(DEFAULT_VALUES.dayLengthHours);
  const [experimentLengthDays, setExperimentLengthDays] = useState(
    DEFAULT_VALUES.experimentLengthDays
  );
  const [selectedSpectra, setSelectedSpectra] = useState<Set<string>>(
    new Set(DEFAULT_VALUES.selectedSpectra)
  );
  const [dayIntensity, setDayIntensity] = useState(DEFAULT_VALUES.dayIntensity);
  const [interval, setInterval] = useState(DEFAULT_VALUES.interval);
  const [photoIlluminationSource, setPhotoIlluminationSource] = useState(
    DEFAULT_VALUES.photoIlluminationSource
  );
  const [starting, setStarting] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [experimentName, setExperimentNameState] = useState(getExperimentName());
  const [system, setSystem] = useSystemInfo();
  const cameraAvailable = system?.cameraAvailable ?? true;
  const [checkingCamera, setCheckingCamera] = useState(false);

  const handleStart = async () => {
    if (starting) return;
    if (!cameraAvailable) {
      setCheckingCamera(true);
      let result;
      try {
        result = await api.recheckCamera();
      } catch (e) {
        setCheckingCamera(false);
        toast.error(`Could not check camera: ${(e as Error).message}`);
        return;
      }
      setSystem(result);
      setCheckingCamera(false);
      if (!result.cameraAvailable) {
        toast.error("No camera detected");
        return;
      }
    }
    setStarting(true);
    try {
      const res = await api.startExperiment({
        protocol: "growth",
        experimentName,
        username: getUsername(),
        preIlluminationEnabled,
        dayLengthHours,
        experimentLengthDays,
        spectra: Array.from(selectedSpectra) as Spectrum[],
        dayIntensity,
        intervalMinutes: interval,
        photoIlluminationSource,
      });
      if (res.status === "busy") {
        toast.error("An experiment is already running.");
        return;
      }
      if (res.status === "no_camera") {
        toast.error("No camera connected.");
        return;
      }
      navigate("/progress-growth");
    } catch (e) {
      toast.error(`Could not start: ${(e as Error).message}`);
    } finally {
      setStarting(false);
    }
  };

  const handleSpectrumToggle = (spectrum: string) => {
    const newSpectra = new Set(selectedSpectra);
    if (newSpectra.has(spectrum)) {
      newSpectra.delete(spectrum);
    } else {
      newSpectra.add(spectrum);
    }
    setSelectedSpectra(newSpectra);
  };

  const handleReset = () => {
    setPreIlluminationEnabled(DEFAULT_VALUES.preIlluminationEnabled);
    setDayLengthHours(DEFAULT_VALUES.dayLengthHours);
    setExperimentLengthDays(DEFAULT_VALUES.experimentLengthDays);
    setSelectedSpectra(new Set(DEFAULT_VALUES.selectedSpectra));
    setDayIntensity(DEFAULT_VALUES.dayIntensity);
    setInterval(DEFAULT_VALUES.interval);
    setPhotoIlluminationSource(DEFAULT_VALUES.photoIlluminationSource);
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto overflow-hidden">
      <TopNav />

      <div className="flex p-2 flex-col items-start gap-2 flex-1 self-stretch bg-app-bg-primary overflow-hidden">
        <ProgramTabs />

        <div className="flex flex-col items-start gap-2 self-stretch flex-1 overflow-y-auto pr-1">
          {/* Pre-illumination toggle */}
          <div className="flex p-2 items-center gap-2 self-stretch rounded-[10px] border border-app-border-primary bg-app-bg-secondary flex-shrink-0">
            <label className="flex items-center gap-2 cursor-pointer w-full">
              <input
                type="checkbox"
                checked={preIlluminationEnabled}
                onChange={(e) => setPreIlluminationEnabled(e.target.checked)}
                className="w-4 h-4 cursor-pointer"
              />
              <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase">
                Pre-Illumination (Fixed 6h @ 50% White, Top-Down)
              </div>
            </label>
          </div>

          {/* Day Length / Experiment Length */}
          <div className="flex justify-center items-start gap-2 self-stretch flex-shrink-0">
            <ParameterControl
              label="Day Length"
              value={`${dayLengthHours}h`}
              valueColor={DAY_COLOR}
              sliderColor={DAY_COLOR}
              sliderValue={dayLengthHours}
              sliderMin={0}
              sliderMax={24}
              onSliderChange={setDayLengthHours}
              onIncrement={() => setDayLengthHours((v) => Math.min(24, v + 1))}
              onDecrement={() => setDayLengthHours((v) => Math.max(0, v - 1))}
            />
            <ParameterControl
              label="Experiment Length (Days)"
              value={`${experimentLengthDays}d`}
              valueColor={EXPERIMENT_LENGTH_COLOR}
              sliderColor={EXPERIMENT_LENGTH_COLOR}
              sliderValue={experimentLengthDays}
              sliderMin={1}
              sliderMax={30}
              onSliderChange={setExperimentLengthDays}
              onIncrement={() => setExperimentLengthDays((v) => Math.min(30, v + 1))}
              onDecrement={() => setExperimentLengthDays((v) => Math.max(1, v - 1))}
            />
          </div>

          <div className="flex justify-center items-stretch gap-2 self-stretch flex-shrink-0">
            <SpectrumPanel
              label="Day Spectrum"
              selected={selectedSpectra}
              onToggle={handleSpectrumToggle}
              compact
              className="flex-1"
            />

            <div className="flex p-1.5 flex-col items-start gap-1 self-stretch flex-1 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
              <div className="text-app-text-muted text-[8px] font-bold leading-[12px] tracking-[0.5px] uppercase">
                Photo Illumination
              </div>
              <div className="flex w-full items-start gap-1.5">
                {(["ir", "rgbw"] as const).map((source) => {
                  const isSelected = photoIlluminationSource === source;
                  return (
                    <button
                      key={source}
                      onClick={() => setPhotoIlluminationSource(source)}
                      className={`flex py-1 px-3 flex-col justify-center items-center rounded border transition-all cursor-pointer flex-1 ${
                        isSelected
                          ? "bg-[rgba(194,122,255,0.4)] border-[rgba(194,122,255,0.5)] text-white"
                          : "bg-[rgba(194,122,255,0.15)] border-transparent text-white/60"
                      }`}
                    >
                      <div className="text-center text-[9px] font-bold leading-[13px] uppercase">
                        {source === "ir" ? "IR (Dark)" : "RGBW (White @10%, Top)"}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Day Intensity / Interval */}
          <div className="flex justify-center items-start gap-2 self-stretch flex-shrink-0">
            <ParameterControl
              label="Day Intensity"
              value={`${dayIntensity}%`}
              valueColor={INTENSITY_COLOR}
              sliderColor={INTENSITY_COLOR}
              sliderValue={dayIntensity}
              sliderMin={0}
              sliderMax={100}
              sliderStep={5}
              onSliderChange={setDayIntensity}
              onIncrement={() => setDayIntensity((v) => Math.min(100, v + 5))}
              onDecrement={() => setDayIntensity((v) => Math.max(0, v - 5))}
            />
            <ParameterControl
              label="Interval Between Images (MIN)"
              value={`${interval}m`}
              valueColor={INTERVAL_COLOR}
              sliderColor={INTERVAL_COLOR}
              sliderValue={interval}
              sliderMin={1}
              sliderMax={240}
              onSliderChange={setInterval}
              onIncrement={() => setInterval((v) => Math.min(240, v + 1))}
              onDecrement={() => setInterval((v) => Math.max(1, v - 1))}
            />
          </div>

        </div>

        <div className="flex pb-2 items-start gap-2 self-stretch flex-shrink-0">
          <button
            onClick={handleStart}
            disabled={starting || checkingCamera}
            title={cameraAvailable ? undefined : "No camera connected — tap to check again"}
            className={`flex py-2 px-0 justify-center items-center gap-3 flex-1 rounded-[10px] bg-app-green shadow-[0_10px_15px_-3px_rgba(13,84,43,0.2),0_4px_6px_-4px_rgba(13,84,43,0.2)] overflow-hidden hover:bg-app-green-light transition-colors disabled:cursor-not-allowed ${
              starting || checkingCamera || !cameraAvailable ? "opacity-60" : ""
            }`}
          >
            <Play className="w-[18px] h-[18px] text-white fill-white" strokeWidth={1.5} />
            <span className="text-white text-center text-[14px] font-black leading-5 tracking-[1.4px] uppercase">
              {starting
                ? "Starting…"
                : checkingCamera
                  ? "Checking…"
                  : !cameraAvailable
                    ? "No Camera"
                    : "Start Experiment"}
            </span>
          </button>

          <button
            onClick={() => setEditingName(true)}
            title={`Experiment: ${experimentName}`}
            className="flex py-2 px-4 justify-center items-center gap-1.5 rounded-[10px] border border-app-border-primary bg-app-bg-secondary hover:bg-app-bg-tertiary transition-colors max-w-[160px]"
          >
            <Tag className="w-[18px] h-[18px] text-white flex-shrink-0" strokeWidth={1.5} />
            <span className="truncate text-[12px] font-semibold text-app-text-secondary">
              {experimentName}
            </span>
          </button>

          <button
            onClick={handleReset}
            className="flex py-2 px-6 justify-center items-center rounded-[10px] border border-app-border-primary bg-app-bg-secondary hover:bg-app-bg-tertiary transition-colors"
          >
            <RotateCcw className="w-[18px] h-[18px] text-white" strokeWidth={1.5} />
          </button>
        </div>
      </div>

      {editingName && (
        <OnScreenKeyboard
          title="Experiment name"
          initialValue={experimentName}
          onCancel={() => setEditingName(false)}
          onConfirm={(v) => {
            setExperimentName(v);
            setExperimentNameState(getExperimentName());
            setEditingName(false);
          }}
        />
      )}
    </div>
  );
}
