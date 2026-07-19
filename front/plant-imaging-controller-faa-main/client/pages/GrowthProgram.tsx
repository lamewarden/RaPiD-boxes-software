import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
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
import type { SavedExperimentConfig, Spectrum } from "@shared/api";

const DAY_COLOR = "#F0B100";
const EXPERIMENT_LENGTH_COLOR = "#2B7FFF";
const INTENSITY_COLOR = "#F0B100";
const INTERVAL_COLOR = "#51A2FF";

const DEFAULT_VALUES = {
  dayLengthHours: 16,
  experimentLengthDays: 14,
  selectedSpectra: new Set(["white"]),
  dayIntensity: 25,
  interval: 30,
};

export default function GrowthProgram() {
  const navigate = useNavigate();
  const location = useLocation();
  const [dayLengthHours, setDayLengthHours] = useState(DEFAULT_VALUES.dayLengthHours);
  const [experimentLengthDays, setExperimentLengthDays] = useState(
    DEFAULT_VALUES.experimentLengthDays
  );
  const [selectedSpectra, setSelectedSpectra] = useState<Set<string>>(
    new Set(DEFAULT_VALUES.selectedSpectra)
  );
  const [dayIntensity, setDayIntensity] = useState(DEFAULT_VALUES.dayIntensity);
  const [interval, setInterval] = useState(DEFAULT_VALUES.interval);
  const [starting, setStarting] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [experimentName, setExperimentNameState] = useState(getExperimentName());
  const [system, setSystem] = useSystemInfo();
  const cameraAvailable = system?.cameraAvailable ?? true;
  const [checkingCamera, setCheckingCamera] = useState(false);

  useEffect(() => {
    const loaded = location.state?.loadedConfig as SavedExperimentConfig | undefined;
    if (!loaded || loaded.protocol !== "growth") return;

    setDayLengthHours(loaded.dayLengthHours);
    setExperimentLengthDays(loaded.experimentLengthDays);
    setSelectedSpectra(new Set(loaded.spectra));
    setDayIntensity(loaded.dayIntensity);
    setInterval(loaded.intervalMinutes);

    api
      .settings()
      .then((current) =>
        api.saveSettings({
          ...current,
          camera: loaded.camera,
          leds: loaded.leds,
          ir: loaded.ir,
          photoIlluminationSource: loaded.photoIlluminationSource,
        })
      )
      .then(() => toast.success("Loaded previous experiment's settings, including camera and illumination."))
      .catch((e) =>
        toast.error(`Loaded phases/light, but could not apply device settings: ${(e as Error).message}`)
      );

    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

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
        dayLengthHours,
        experimentLengthDays,
        spectra: Array.from(selectedSpectra) as Spectrum[],
        dayIntensity,
        intervalMinutes: interval,
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
    setDayLengthHours(DEFAULT_VALUES.dayLengthHours);
    setExperimentLengthDays(DEFAULT_VALUES.experimentLengthDays);
    setSelectedSpectra(new Set(DEFAULT_VALUES.selectedSpectra));
    setDayIntensity(DEFAULT_VALUES.dayIntensity);
    setInterval(DEFAULT_VALUES.interval);
    toast.success("Growth settings reset to defaults.");
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto overflow-hidden">
      <TopNav />

      <div className="flex p-1.5 flex-col items-start gap-1.5 flex-1 self-stretch bg-app-bg-primary overflow-hidden">
        <ProgramTabs />

        <div className="flex flex-col items-start gap-1.5 self-stretch flex-1 min-h-0 overflow-y-auto pr-0.5">
          {/* Day Length / Experiment Length */}
          <div className="flex justify-center items-start gap-1.5 self-stretch flex-shrink-0">
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

          <SpectrumPanel
            label="Day Spectrum"
            selected={selectedSpectra}
            onToggle={handleSpectrumToggle}
          />

          {/* Day Intensity / Interval */}
          <div className="flex justify-center items-start gap-1.5 self-stretch flex-shrink-0">
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
            <ParameterControl
              label="Light Intensity"
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
          </div>

        </div>

        <div className="relative z-10 flex pb-1 items-start gap-1.5 self-stretch flex-shrink-0 bg-app-bg-primary">
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
