import { useEffect, useState, type ReactNode } from "react";

/**
 * Screen-agnostic shell. The UI is designed at a fixed 800x452 "stage"; this
 * wrapper scales that stage proportionally to fill whatever display the kiosk
 * runs on (4.3" / 5" / 7", any resolution) and re-fits on resize/orientation.
 * Keeps the carefully-tuned layout intact instead of reflowing every control.
 */
export default function AutoScale({
  width = 800,
  height = 452,
  children,
}: {
  width?: number;
  height?: number;
  children: ReactNode;
}) {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const fit = () =>
      setScale(Math.min(window.innerWidth / width, window.innerHeight / height));
    fit();
    window.addEventListener("resize", fit);
    window.addEventListener("orientationchange", fit);
    return () => {
      window.removeEventListener("resize", fit);
      window.removeEventListener("orientationchange", fit);
    };
  }, [width, height]);

  return (
    <div className="fixed inset-0 flex items-center justify-center overflow-hidden bg-app-bg-primary">
      <div
        style={{
          width,
          height,
          transform: `scale(${scale})`,
          transformOrigin: "center center",
          flexShrink: 0,
        }}
      >
        {children}
      </div>
    </div>
  );
}
