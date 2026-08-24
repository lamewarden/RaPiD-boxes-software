import { useState } from "react";
import { Mail } from "lucide-react";
import { toast } from "sonner";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Opt-in "email me if an issue is detected" toggle, shared by
 * TropismProgram/GrowthProgram (Phase 4 of the assistant agent-brain work --
 * see MoldWatchService on the backend). Ticking the box with no email on
 * file opens the on-screen keyboard immediately, since the backend rejects
 * reportOnIssueEnabled without a notifyEmail.
 */
export default function IssueAlertsField({
  enabled,
  email,
  onChange,
}: {
  enabled: boolean;
  email: string;
  onChange: (enabled: boolean, email: string) => void;
}) {
  const [editingEmail, setEditingEmail] = useState(false);

  const toggle = () => {
    if (enabled) {
      onChange(false, email);
      return;
    }
    if (email && EMAIL_RE.test(email)) {
      onChange(true, email);
    } else {
      setEditingEmail(true);
    }
  };

  return (
    <>
      <div className="flex h-[38px] px-2.5 items-center gap-2 self-stretch flex-shrink-0 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
        <label className="flex items-center gap-2 cursor-pointer flex-shrink-0">
          <input
            type="checkbox"
            checked={enabled}
            onChange={toggle}
            className="w-4 h-4 cursor-pointer"
          />
          <span className="text-app-text-muted text-[10px] font-bold leading-[15px] tracking-[0.5px] uppercase">
            Email Me If An Issue Is Detected
          </span>
        </label>
        <button
          type="button"
          onClick={() => setEditingEmail(true)}
          className="ml-auto flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-app-text-secondary hover:bg-app-bg-tertiary transition-colors max-w-[220px] min-w-0"
        >
          <Mail className="h-[13px] w-[13px] flex-shrink-0" strokeWidth={1.5} />
          <span className="truncate">{email || "Set email…"}</span>
        </button>
      </div>

      {editingEmail && (
        <OnScreenKeyboard
          title="Notify email (issue alerts)"
          initialValue={email}
          onCancel={() => setEditingEmail(false)}
          onConfirm={(v) => {
            const trimmed = v.trim();
            if (!trimmed) {
              setEditingEmail(false);
              onChange(false, "");
              return;
            }
            if (!EMAIL_RE.test(trimmed)) {
              toast.error("Enter a valid email address.");
              return;
            }
            setEditingEmail(false);
            onChange(true, trimmed);
          }}
        />
      )}
    </>
  );
}
