import { useState } from "react";
import { Camera, Lightbulb, Settings, X } from "lucide-react";
import CameraSettingsMenu from "@/components/CameraSettingsMenu";
import GeneralSettingsMenu from "@/components/GeneralSettingsMenu";
import IlluminationSettingsMenu from "@/components/IlluminationSettingsMenu";

type SettingsSection = "camera" | "illumination" | "general";

export default function SettingsMenu({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState<SettingsSection>("camera");

  const tabClass = (active: boolean) =>
    `flex items-center gap-2 rounded-md px-3 py-1.5 text-[12px] font-bold uppercase tracking-[1px] transition-colors ${
      active
        ? "bg-app-green text-white"
        : "bg-app-bg-tertiary text-app-text-secondary hover:bg-app-border-primary hover:text-white"
    }`;

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
      </div>

      <div className="flex-1 overflow-hidden">
        {section === "camera" && <CameraSettingsMenu embedded />}
        {section === "illumination" && <IlluminationSettingsMenu />}
        {section === "general" && <GeneralSettingsMenu />}
      </div>
    </div>
  );
}
