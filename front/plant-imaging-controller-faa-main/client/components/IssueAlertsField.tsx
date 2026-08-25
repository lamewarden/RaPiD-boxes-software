import { useEffect, useState } from "react";
import { Send } from "lucide-react";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";

/**
 * Opt-in "Telegram me if an issue is detected" toggle, shared by
 * TropismProgram/GrowthProgram (Phase 4 of the assistant agent-brain work --
 * see MoldWatchService on the backend). Unlike an email-based design there's
 * no contact field to type here: the destination is resolved server-side
 * from a one-time Telegram link (Settings -> General -> Telegram Alerts).
 * The checkbox stays disabled, with a pointer to that screen, until this
 * user has actually linked -- the backend rejects reportOnIssueEnabled
 * without one anyway, so this just surfaces that up front instead of
 * failing at Start.
 */
export default function IssueAlertsField({
  enabled,
  onChange,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const [linked, setLinked] = useState<boolean | null>(null);
  const username = getUsername();

  useEffect(() => {
    let cancelled = false;
    api
      .telegramStatus(username)
      .then((s) => {
        if (!cancelled) setLinked(s.linked);
      })
      .catch(() => {
        if (!cancelled) setLinked(false);
      });
    return () => {
      cancelled = true;
    };
  }, [username]);

  const disabled = linked !== true;

  return (
    <div className="flex h-[38px] px-2.5 items-center gap-2 self-stretch flex-shrink-0 rounded-[10px] border border-app-border-primary bg-app-bg-secondary">
      <label
        className={`flex items-center gap-2 flex-shrink-0 ${disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}
      >
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="w-4 h-4 cursor-pointer disabled:cursor-not-allowed"
        />
        <span className="text-app-text-muted text-[10px] font-bold leading-[15px] tracking-[0.5px] uppercase">
          Telegram Me If An Issue Is Detected
        </span>
      </label>
      {disabled && (
        <span className="ml-auto flex items-center gap-1.5 truncate text-[10px] text-app-text-muted">
          <Send className="h-[12px] w-[12px] flex-shrink-0" strokeWidth={1.5} />
          Link Telegram in Settings → General first
        </span>
      )}
    </div>
  );
}
