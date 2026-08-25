import { useEffect, useState } from "react";
import { Bookmark, Camera, Check, Info, Lightbulb, RotateCcw, Settings, X } from "lucide-react";
import { toast } from "sonner";
import CameraSettingsMenu from "@/components/CameraSettingsMenu";
import GeneralSettingsMenu from "@/components/GeneralSettingsMenu";
import IlluminationSettingsMenu from "@/components/IlluminationSettingsMenu";
import InfoSettingsMenu from "@/components/InfoSettingsMenu";
import { api } from "@/lib/api";
import { useExperimentStatus } from "@/hooks/useExperimentStatus";
import { getUsername } from "@/lib/session";
import { DEFAULT_DEVICE_SETTINGS } from "@/lib/deviceDefaults";
import type { CameraSettings, DeviceSettings, LedSettings, PhotoIlluminationSource } from "@shared/api";

type SettingsSection = "camera" | "illumination" | "general" | "info";

export default function SettingsMenu({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState<SettingsSection>("camera");
  const [deviceSettings, setDeviceSettings] = useState<DeviceSettings | null>(null);
  // This user's own saved baseline ("Mine") -- null until they save one.
  const [myDefaults, setMyDefaults] = useState<DeviceSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingMine, setSavingMine] = useState(false);
  const { status } = useExperimentStatus();
  const locked = status?.state === "running" || status?.state === "paused";
  const username = getUsername();

  useEffect(() => {
    api
      .settings()
      .then(setDeviceSettings)
      .catch((e) => toast.error(`Could not load settings: ${(e as Error).message}`))
      .finally(() => setLoading(false));
    api.myDefaults(username).then(setMyDefaults).catch(() => setMyDefaults(null));
  }, [username]);

  const patchCamera = (p: Partial<CameraSettings>) =>
    setDeviceSettings((d) => (d ? { ...d, camera: { ...d.camera, ...p } } : d));
  const patchLeds = (p: Partial<LedSettings>) =>
    setDeviceSettings((d) => (d ? { ...d, leds: { ...d.leds, ...p } } : d));
  const setIrPins = (pins: [number, number]) =>
    setDeviceSettings((d) => (d ? { ...d, ir: { pins } } : d));
  const setSource = (photoIlluminationSource: PhotoIlluminationSource) =>
    setDeviceSettings((d) => (d ? { ...d, photoIlluminationSource } : d));

  const handleSave = async () => {
    if (!deviceSettings) return;
    setSaving(true);
    try {
      const saved = await api.saveSettings(deviceSettings);
      setDeviceSettings(saved);
      toast.success("Settings saved.");
    } catch (e) {
      toast.error(`Could not save: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  /** Applies the current settings to this session (like Save) and also
   *  remembers them as this user's personal baseline, so "Mine" has
   *  something to reset to next time -- including after a reboot, unlike
   *  the session settings above. */
  const handleSaveMine = async () => {
    if (!deviceSettings) return;
    setSavingMine(true);
    try {
      const [saved, mine] = await Promise.all([
        api.saveSettings(deviceSettings),
        api.saveMyDefaults(username, deviceSettings),
      ]);
      setDeviceSettings(saved);
      setMyDefaults(mine);
      toast.success("Saved as your settings.");
    } catch (e) {
      toast.error(`Could not save: ${(e as Error).message}`);
    } finally {
      setSavingMine(false);
    }
  };

  const tabClass = (active: boolean) =>
    `flex items-center gap-2 rounded-md px-3 py-1.5 text-[12px] font-bold uppercase tracking-[1px] transition-colors ${
      active
        ? "bg-app-green text-white"
        : "bg-app-bg-tertiary text-app-text-secondary hover:bg-app-border-primary hover:text-white"
    }`;

  // Default/Mine/Save Mine/Save act on camera + illumination together (one
  // DeviceSettings bundle); General and Info are separate concerns (update/
  // remote sync/SSH, and static credits, respectively), so neither gets the
  // shared row or the read-only banner.
  const showSharedActions = section === "camera" || section === "illumination";

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-app-bg-primary">
      <div className="flex items-center justify-between border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
        <span className="text-[15px] font-bold uppercase tracking-wide text-white">Settings</span>
        <button
          onClick={onClose}
          className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
        >
          <X className="h-[18px] w-[18px]" strokeWidth={1.5} />
        </button>
      </div>

      <div className="flex items-center gap-2 border-b border-app-border-primary bg-app-bg-secondary px-3 py-2">
        <button onClick={() => setSection("camera")} className={tabClass(section === "camera")}>
          <Camera className="h-[14px] w-[14px]" strokeWidth={1.75} />
          <span>Camera</span>
        </button>
        <button
          onClick={() => setSection("illumination")}
          className={tabClass(section === "illumination")}
        >
          <Lightbulb className="h-[14px] w-[14px]" strokeWidth={1.75} />
          <span>Illumination</span>
        </button>
        <button onClick={() => setSection("general")} className={tabClass(section === "general")}>
          <Settings className="h-[14px] w-[14px]" strokeWidth={1.75} />
          <span>General</span>
        </button>
        <button onClick={() => setSection("info")} className={tabClass(section === "info")}>
          <Info className="h-[14px] w-[14px]" strokeWidth={1.75} />
          <span>Info</span>
        </button>
      </div>

      {locked && showSharedActions && (
        <div className="border-b border-app-border-primary bg-app-orange/20 px-3 py-1.5 text-[11px] font-semibold text-app-orange-light">
          An experiment is running — settings are read-only until it finishes.
        </div>
      )}

      <div className="flex-1 overflow-hidden">
        {loading || !deviceSettings ? (
          <div className="flex h-full items-center justify-center text-sm text-app-text-muted">
            Loading…
          </div>
        ) : (
          <>
            {section === "camera" && (
              <CameraSettingsMenu
                camera={deviceSettings.camera}
                source={deviceSettings.photoIlluminationSource}
                onChange={patchCamera}
                locked={locked}
              />
            )}
            {section === "illumination" && (
              <IlluminationSettingsMenu
                leds={deviceSettings.leds}
                ir={deviceSettings.ir}
                source={deviceSettings.photoIlluminationSource}
                onChangeLeds={patchLeds}
                onChangeIrPins={setIrPins}
                onChangeSource={setSource}
                locked={locked}
              />
            )}
            {section === "general" && <GeneralSettingsMenu />}
            {section === "info" && <InfoSettingsMenu />}
          </>
        )}
      </div>

      {showSharedActions && !loading && deviceSettings && (
        <div className="flex items-center gap-2 border-t border-app-border-primary bg-app-bg-secondary p-2">
          <button
            onClick={() => setDeviceSettings(DEFAULT_DEVICE_SETTINGS)}
            disabled={locked}
            title="Reset to the fixed system default"
            className="flex items-center gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RotateCcw className="h-[16px] w-[16px]" strokeWidth={1.5} />
            <span className="text-[12px] font-bold uppercase tracking-[1px]">Default</span>
          </button>

          <button
            onClick={() => myDefaults && setDeviceSettings(myDefaults)}
            disabled={locked || !myDefaults}
            title={myDefaults ? `Reset to ${username}'s saved settings` : "No saved settings yet — use Save Mine first"}
            className="flex items-center gap-2 rounded-[10px] border border-app-border-primary bg-app-bg-tertiary px-4 py-2 text-white transition-colors hover:bg-app-border-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Bookmark className="h-[16px] w-[16px]" strokeWidth={1.5} />
            <span className="text-[12px] font-bold uppercase tracking-[1px]">Mine</span>
          </button>

          <div className="flex-1" />

          <button
            onClick={handleSaveMine}
            disabled={savingMine || locked}
            title={
              locked
                ? "Cannot change settings while an experiment is running"
                : `Save camera + illumination as ${username}'s personal baseline (survives a reboot)`
            }
            className="flex items-center gap-2 rounded-[10px] border border-app-green/60 bg-app-green/10 px-4 py-2 text-white transition-colors hover:bg-app-green/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Bookmark className="h-[16px] w-[16px]" strokeWidth={1.5} />
            <span className="text-[12px] font-bold uppercase tracking-[1px]">
              {savingMine ? "Saving…" : "Save Mine"}
            </span>
          </button>

          <button
            onClick={handleSave}
            disabled={saving || locked}
            title={locked ? "Cannot change settings while an experiment is running" : undefined}
            className="flex items-center gap-2 rounded-[10px] bg-app-green px-6 py-2 text-white transition-colors hover:bg-app-green-light disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check className="h-[16px] w-[16px]" strokeWidth={1.5} />
            <span className="text-[12px] font-black uppercase tracking-[1.4px]">
              {saving ? "Saving…" : "Save"}
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
