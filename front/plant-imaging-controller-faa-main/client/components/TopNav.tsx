import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { X, User, Folder, Radio, Settings, Download } from "lucide-react";
import { toast } from "sonner";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import SettingsMenu from "@/components/SettingsMenu";
import ImportConfigMenu from "@/components/ImportConfigMenu";
import { getUsername, setUsername } from "@/lib/session";
import { useSystemInfo } from "@/hooks/useSystemInfo";
import { api } from "@/lib/api";
import type { SavedExperimentConfig } from "@shared/api";

export default function TopNav() {
  const navigate = useNavigate();
  const [editingUser, setEditingUser] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [username, setUser] = useState(getUsername());
  const [system, setSystem] = useSystemInfo();
  const cameraAvailable = system?.cameraAvailable ?? true;
  const [checkingCamera, setCheckingCamera] = useState(false);
  const [closingKiosk, setClosingKiosk] = useState(false);

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
    "flex flex-1 py-2 px-0 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors";

  const handleImportLoad = (config: SavedExperimentConfig) => {
    navigate(config.protocol === "growth" ? "/growth" : "/tropism", {
      state: { loadedConfig: config },
    });
  };

  const handleClose = async () => {
    if (closingKiosk) return;
    setClosingKiosk(true);
    try {
      await api.closeKiosk();
      toast.success("Closing kiosk...");
    } catch (e) {
      toast.error(`Could not close kiosk: ${(e as Error).message}`);
    } finally {
      setClosingKiosk(false);
    }
  };

  return (
    <div className="flex p-0.5 gap-1 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary">
      <button className={btn} onClick={handleClose} disabled={closingKiosk}>
        <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">
          {closingKiosk ? "Closing..." : "Close"}
        </span>
      </button>

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
        <span className="text-white text-center text-[13px] font-semibold leading-5">Gallery</span>
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
          className="flex flex-1 py-2 px-0 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary opacity-50 hover:opacity-70 transition-opacity"
        >
          <Radio className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">
            {checkingCamera ? "Checking…" : "Live"}
          </span>
        </button>
      )}

      <button
        className="flex flex-1 py-2 px-0 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        onClick={() => setSettingsOpen(true)}
      >
        <Settings className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Settings</span>
      </button>

      {settingsOpen && (
        <SettingsMenu onClose={() => setSettingsOpen(false)} />
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
