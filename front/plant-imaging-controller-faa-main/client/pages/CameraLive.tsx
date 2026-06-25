import { useLocation, useNavigate } from "react-router-dom";
import { X } from "lucide-react";

export default function CameraLive() {
  const navigate = useNavigate();
  const location = useLocation();
  const previousPage = location.state?.from || "/";

  const handleClose = () => {
    navigate(previousPage);
  };

  return (
    <div className="flex w-[800px] h-[452px] flex-col justify-start items-start mx-auto bg-app-bg-primary">
      {/* Top nav with close button */}
      <div className="flex p-0.5 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary w-full">
        <button
          onClick={handleClose}
          className="flex w-[199.25px] py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
        >
          <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
          <span className="text-white text-center text-[13px] font-semibold leading-5">
            Close
          </span>
        </button>
      </div>

      {/* Full-screen camera feed (MJPEG stream from the backend) */}
      <div className="flex-1 flex items-center justify-center w-full p-2">
        <img
          src="/api/preview"
          alt="Live camera feed"
          className="h-full w-full rounded-lg border-2 border-app-border-primary bg-black object-contain"
        />
      </div>
    </div>
  );
}
