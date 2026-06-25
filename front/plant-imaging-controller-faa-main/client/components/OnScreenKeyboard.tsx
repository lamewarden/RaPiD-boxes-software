import { useState } from "react";
import { Check, Delete, X } from "lucide-react";

/**
 * Minimal touch keyboard for the kiosk (no physical keyboard). Replaces the
 * legacy custom Tkinter keyboard. Themed to the app; no extra dependency.
 */
const ROWS = [
  "1234567890".split(""),
  "qwertyuiop".split(""),
  "asdfghjkl".split(""),
  "zxcvbnm-_".split(""),
];

export default function OnScreenKeyboard({
  title,
  initialValue,
  onConfirm,
  onCancel,
}: {
  title: string;
  initialValue: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  const [shift, setShift] = useState(false);

  const press = (k: string) => setValue((v) => v + (shift ? k.toUpperCase() : k));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-[640px] rounded-xl border border-app-border-primary bg-app-bg-secondary p-4 shadow-2xl">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-bold uppercase tracking-wide text-app-text-muted">
            {title}
          </span>
          <button onClick={onCancel} className="rounded p-1 text-app-text-secondary hover:bg-app-bg-tertiary">
            <X className="h-5 w-5" />
          </button>
        </div>

        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="mb-3 w-full rounded-lg border border-app-border-primary bg-app-bg-primary px-3 py-2 text-lg text-white outline-none focus:border-app-green"
          autoFocus
        />

        <div className="flex flex-col gap-1.5">
          {ROWS.map((row, i) => (
            <div key={i} className="flex justify-center gap-1.5">
              {row.map((k) => (
                <button
                  key={k}
                  onClick={() => press(k)}
                  className="min-h-[44px] flex-1 rounded-md bg-app-bg-tertiary text-base font-semibold text-white hover:bg-app-border-primary"
                >
                  {shift ? k.toUpperCase() : k}
                </button>
              ))}
            </div>
          ))}
          <div className="flex justify-center gap-1.5">
            <button
              onClick={() => setShift((s) => !s)}
              className={`min-h-[44px] flex-1 rounded-md text-sm font-bold ${
                shift ? "bg-app-orange text-white" : "bg-app-bg-tertiary text-white hover:bg-app-border-primary"
              }`}
            >
              Shift
            </button>
            <button
              onClick={() => press(" ")}
              className="min-h-[44px] flex-[3] rounded-md bg-app-bg-tertiary text-white hover:bg-app-border-primary"
            >
              Space
            </button>
            <button
              onClick={() => setValue((v) => v.slice(0, -1))}
              className="flex min-h-[44px] flex-1 items-center justify-center rounded-md bg-app-bg-tertiary text-white hover:bg-app-border-primary"
            >
              <Delete className="h-5 w-5" />
            </button>
            <button
              onClick={() => onConfirm(value)}
              className="flex min-h-[44px] flex-1 items-center justify-center rounded-md bg-app-green font-bold text-white hover:bg-app-green-light"
            >
              <Check className="h-5 w-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
