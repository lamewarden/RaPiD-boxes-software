import { useEffect, useState, type CSSProperties } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Play, RotateCcw, Tag } from "lucide-react";
import { toast } from "sonner";
import TopNav from "@/components/TopNav";
import ProgramTabs from "@/components/ProgramTabs";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import SpectrumPanel from "@/components/SpectrumPanel";
import { api } from "@/lib/api";
import { getExperimentName, getUsername, setExperimentName } from "@/lib/session";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import type { SavedExperimentConfig, Spectrum } from "@shared/api";

const DEFAULT_VALUES = {
  darkPhaseEnabled: false,
  darkPhase: 90,
  lateralIllumination: 4,
  selectedSpectra: new Set(["white"]),
  interval: 3,
  intensity: 25,
};

export default function TropismProgram() {
  const DARK_PHASE_COLOR = "#C27AFF";
  const navigate = useNavigate();
  const location = useLocation();
  const [darkPhaseEnabled, setDarkPhaseEnabled] = useState(DEFAULT_VALUES.darkPhaseEnabled);
  const [darkPhase, setDarkPhase] = useState(DEFAULT_VALUES.darkPhase);
  const [lateralIllumination, setLateralIllumination] = useState(
    DEFAULT_VALUES.lateralIllumination
  );
  const [selectedSpectra, setSelectedSpectra] = useState<Set<string>>(
    new Set(DEFAULT_VALUES.selectedSpectra)
  );
  const [interval, setInterval] = useState(DEFAULT_VALUES.interval);
  const [intensity, setIntensity] = useState(DEFAULT_VALUES.intensity);
  const [starting, setStarting] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [experimentName, setExperimentNameState] = useState(getExperimentName());
  const [system, setSystem] = useSystemInfo();
  const cameraAvailable = system?.cameraAvailable ?? true;
  const [checkingCamera, setCheckingCamera] = useState(false);

  useEffect(() => {
    const loaded = location.state?.loadedConfig as SavedExperimentConfig | undefined;
    if (!loaded) return;

    setDarkPhaseEnabled(loaded.darkPhaseEnabled);
    setDarkPhase(loaded.darkPhaseHours);
    setLateralIllumination(loaded.lateralIlluminationHours);
    setSelectedSpectra(new Set(loaded.spectra));
    setInterval(loaded.intervalMinutes);
    setIntensity(loaded.intensity);

    api
      .settings()
      .then((current) => api.saveSettings({ ...current, camera: loaded.camera }))
      .then(() => toast.success("Loaded previous experiment's settings, including camera."))
      .catch((e) =>
        toast.error(`Loaded phases/light, but could not apply camera settings: ${(e as Error).message}`)
      );

    // Clear the router state so a later remount (e.g. browser back) doesn't replay it.
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
        protocol: "tropism",
        experimentName,
        username: getUsername(),
        darkPhaseEnabled,
        darkPhaseHours: darkPhase,
        lateralIlluminationHours: lateralIllumination,
        spectra: Array.from(selectedSpectra) as Spectrum[],
        intervalMinutes: interval,
        intensity,
      });
      if (res.status === "busy") {
        toast.error("An experiment is already running.");
        return;
      }
      if (res.status === "no_camera") {
        toast.error("No camera connected.");
        return;
      }
      navigate("/progress-tropism");
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
    setDarkPhaseEnabled(DEFAULT_VALUES.darkPhaseEnabled);
    setDarkPhase(DEFAULT_VALUES.darkPhase);
    setLateralIllumination(DEFAULT_VALUES.lateralIllumination);
    setSelectedSpectra(new Set(DEFAULT_VALUES.selectedSpectra));
    setInterval(DEFAULT_VALUES.interval);
    setIntensity(DEFAULT_VALUES.intensity);
  };

  const getColorForValue = (value: number, max: number, colorScheme: string) => {
    const percentage = value / max;

    switch (colorScheme) {
      case "orange":
        return percentage > 0.3 ? "#FF8904" : "#FF6900";
      case "blue":
        return percentage > 0.3 ? "#51A2FF" : "#2B7FFF";
      case "yellow":
        return percentage > 0.5 ? "#FDC700" : "#F0B100";
      default:
        return "#FF6900";
    }
  };

  const getSliderStyle = (
    value: number,
    min: number,
    max: number,
    color: string
  ): CSSProperties => {
    const percent = ((value - min) / (max - min)) * 100;
    return {
      background: `linear-gradient(to right, ${color} 0%, ${color} ${percent}%, #364153 ${percent}%, #364153 100%)`,
      "--slider-thumb-color": color,
    } as CSSProperties;
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto overflow-hidden">
      <TopNav />

      <div className="flex p-2 flex-col items-start gap-2 flex-1 self-stretch bg-app-bg-primary overflow-hidden">
        <ProgramTabs />

        <div className="flex flex-col items-start gap-2 self-stretch flex-1 overflow-y-auto pr-1">

        {/* Lateral Illumination and Dark Phase Row */}
        <div className="flex justify-center items-start gap-2 self-stretch flex-shrink-0">
          {/* Dark Phase Toggle */}
          <div className="flex-1 h-[74px] flex flex-col p-2 items-start gap-1.5 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
            <label className="flex items-center gap-2 cursor-pointer w-full">
              <input
                type="checkbox"
                checked={darkPhaseEnabled}
                onChange={(e) => setDarkPhaseEnabled(e.target.checked)}
                className="w-4 h-4 cursor-pointer"
              />
              <div className="flex-1">
                <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase">
                  Dark Phase
                </div>
              </div>
            </label>
            {darkPhaseEnabled && (
              <div className="w-full flex items-center gap-2">
                <div className="text-[17px] font-black leading-5 min-w-[50px] text-right" style={{ color: DARK_PHASE_COLOR }}>
                  {darkPhase}h
                </div>
                <input
                  type="range"
                  min="0"
                  max="200"
                  value={darkPhase}
                  onChange={(e) => setDarkPhase(Number(e.target.value))}
                  className="app-range-slider flex-1 bg-app-bg-tertiary rounded-lg appearance-none cursor-pointer"
                  style={getSliderStyle(darkPhase, 0, 200, DARK_PHASE_COLOR)}
                />
              </div>
            )}
          </div>

          {/* Lateral Illumination */}
          <div className="flex-1 h-[74px] flex flex-col p-2 items-start gap-1.5 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
            <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase w-full">
              Lateral Illumination
            </div>
            <div className="w-full flex items-center gap-2">
              <div
                className="text-[17px] font-black leading-5 min-w-[50px] text-right"
                style={{ color: getColorForValue(lateralIllumination, 24, "orange") }}
              >
                {lateralIllumination}h
              </div>
              <input
                type="range"
                min="0"
                max="24"
                value={lateralIllumination}
                onChange={(e) => setLateralIllumination(Number(e.target.value))}
                className="app-range-slider flex-1 bg-app-bg-tertiary rounded-lg appearance-none cursor-pointer"
                style={getSliderStyle(lateralIllumination, 0, 24, "#FF6900")}
              />
            </div>
          </div>
        </div>

        <SpectrumPanel label="Day Spectrum" selected={selectedSpectra} onToggle={handleSpectrumToggle} />

        {/* Interval Between Images and Intensity - Row */}
        <div className="flex justify-center items-start gap-2 self-stretch flex-shrink-0">
          {/* Interval Between Images - Left */}
          <div className="flex-1 h-[74px] flex flex-col p-2 items-start gap-1.5 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
            <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase w-full">
              Interval Between Images (MIN)
            </div>
            <div className="w-full flex items-center gap-2">
              <div
                className="text-[17px] font-black leading-5 min-w-[50px] text-right"
                style={{ color: getColorForValue(interval, 120, "blue") }}
              >
                {interval}m
              </div>
              <input
                type="range"
                min="3"
                max="120"
                value={interval}
                onChange={(e) => setInterval(Number(e.target.value))}
                className="app-range-slider flex-1 bg-app-bg-tertiary rounded-lg appearance-none cursor-pointer"
                style={getSliderStyle(interval, 3, 120, "#2B7FFF")}
              />
            </div>
          </div>

          {/* Intensity - Right */}
          <div className="flex-1 h-[74px] flex flex-col p-2 items-start gap-1.5 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
            <div className="text-app-text-muted text-[9px] font-bold leading-[15px] tracking-[0.5px] uppercase w-full">
              Intensity
            </div>
            <div className="w-full flex items-center gap-2">
              <div
                className="text-[17px] font-black leading-5 min-w-[50px] text-right"
                style={{ color: getColorForValue(intensity, 100, "yellow") }}
              >
                {intensity}%
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={intensity}
                onChange={(e) => setIntensity(Number(e.target.value))}
                className="app-range-slider flex-1 bg-app-bg-tertiary rounded-lg appearance-none cursor-pointer"
                style={getSliderStyle(intensity, 0, 100, "#F0B100")}
              />
            </div>
          </div>
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
