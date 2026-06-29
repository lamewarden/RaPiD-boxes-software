import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { X, User, Folder, Radio, Camera, Download } from "lucide-react";
import { toast } from "sonner";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import CameraSettingsMenu from "@/components/CameraSettingsMenu";
import ImportConfigMenu from "@/components/ImportConfigMenu";
import { getUsername, setUsername } from "@/lib/session";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import { api } from "@/lib/api";
import type { SavedExperimentConfig } from "@shared/api";

export default function TopNav() {
  const navigate = useNavigate();
  const [editingUser, setEditingUser] = useState(false);
  const [cameraSettingsOpen, setCameraSettingsOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [username, setUser] = useState(getUsername());
  const [system, setSystem] = useSystemInfo();
  const cameraAvailable = system?.cameraAvailable ?? true;
  const [checkingCamera, setCheckingCamera] = useState(false);

  const handleLiveClick = async () => {
    if (checkingCamera) return;
    setCheckingCamera(true);
    try {
      const result = await api.recheckCamera();
      setSystem(result);
      if (result.cameraAvailable) {
        navigate("/live", { state: { from: window.location.pathname } });
      } else {
        toast.error("No camera detected");
      }
    } catch (e) {
      toast.error(`Could not check camera: ${(e as Error).message}`);
    } finally {
      setCheckingCamera(false);
    }
  };

  const btn =
    "flex flex-1 py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary hover:bg-app-border-primary transition-colors";

  const handleImportLoad = (config: SavedExperimentConfig) => {
    navigate("/tropism", { state: { loadedConfig: config } });
  };

  return (
    <div className="flex p-0.5 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary">
      <Link to="/" className={btn}>
        <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Close</span>
      </Link>

      <button className={btn} onClick={() => setImportOpen(true)}>
        <Download className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Import</span>
      </button>

      <button className={btn} onClick={() => setEditingUser(true)}>
        <User className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">
          {username}
        </span>
      </button>

      <Link to="/gallery" className={btn}>
        <Folder className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Folder</span>
      </Link>

      {cameraAvailable ? (
        <Link
          to="/live"
          state={{ from: window.location.pathname }}
          className={btn}
        >
          <Radio className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">Live</span>
        </Link>
      ) : (
        <button
          onClick={handleLiveClick}
          title="No camera connected — tap to check again"
          className="flex flex-1 py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary opacity-50 hover:opacity-70 transition-opacity"
        >
          <Radio className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">
            {checkingCamera ? "Checking…" : "Live"}
          </span>
        </button>
      )}

      <button
        className="flex flex-1 py-1.5 px-0 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        onClick={() => setCameraSettingsOpen(true)}
      >
        <Camera className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Camera</span>
      </button>

      {cameraSettingsOpen && (
        <CameraSettingsMenu onClose={() => setCameraSettingsOpen(false)} />
      )}

      {importOpen && (
        <ImportConfigMenu onClose={() => setImportOpen(false)} onLoad={handleImportLoad} />
      )}

      {editingUser && (
        <OnScreenKeyboard
          title="Researcher name"
          initialValue={username}
          onCancel={() => setEditingUser(false)}
          onConfirm={(v) => {
            setUsername(v);
            setUser(getUsername());
            setEditingUser(false);
          }}
        />
      )}
    </div>
  );
}
