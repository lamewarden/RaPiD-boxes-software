import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Link2, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";
import type { TelegramStatus } from "@shared/api";

/**
 * Telegram issue-alert linking card for Settings -> General. Delivers the
 * mold/anomaly alerts from MoldWatchService (see rapidboxes/telegram_link.py)
 * as a private DM, per researcher -- not a shared group -- so linking is a
 * one-time, per-username step: tap Link, get a short code, send it to the
 * bot from your own phone. The 3s poll below is what notices the moment
 * that code lands and flips `linked` to true, without a manual refresh.
 */
export default function TelegramLinkPanel() {
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const username = getUsername();

  const refresh = useCallback(async () => {
    try {
      const next = await api.telegramStatus(username);
      setStatus(next);
      if (next.linked && code) {
        setCode(null);
        toast.success("Telegram linked — you'll get a DM here if an issue is detected.");
      }
    } catch {
      /* best-effort: keep whatever we last had */
    }
  }, [username, code]);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 3000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [username]);

  const handleLink = async () => {
    if (requesting) return;
    setRequesting(true);
    try {
      const result = await api.requestTelegramLinkCode(username);
      setCode(result.code);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRequesting(false);
    }
  };

  if (status != null && !status.configured) {
    return (
      <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
        <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
          Telegram Alerts
        </div>
        <p className="mt-1.5 text-[10px] text-app-text-muted">
          Not set up on this device yet — an admin needs to configure a bot first.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-[10px] border border-app-border-primary bg-app-bg-secondary p-3">
      <div className="flex items-center justify-between">
        <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
          Telegram Alerts
        </div>
        {status?.linked && (
          <span className="flex items-center gap-1 text-[10px] font-semibold text-app-green">
            <CheckCircle2 className="h-[12px] w-[12px]" strokeWidth={2} />
            Linked
          </span>
        )}
      </div>
      <p className="mt-1.5 text-[10px] leading-[14px] text-app-text-muted">
        Link your own Telegram to get a private message here if an issue (e.g. mold) is
        detected during an experiment you opt into — see the "Telegram Me If An Issue Is
        Detected" checkbox on the setup screen.
      </p>

      {code && (
        <div className="mt-2 rounded-md border border-app-violet/40 bg-app-violet/10 p-2.5">
          <p className="text-[10px] text-app-text-secondary">
            Open Telegram, message{" "}
            <span className="font-mono font-semibold text-white">@{status?.botUsername}</span>,
            and send this code:
          </p>
          <p className="mt-1 text-center font-mono text-[20px] font-black tracking-[4px] text-white">
            {code}
          </p>
          <p className="mt-1 flex items-center gap-1 text-[9px] text-app-text-muted">
            <Loader2 className="h-[10px] w-[10px] animate-spin" strokeWidth={2} />
            Waiting for it to arrive…
          </p>
        </div>
      )}

      {!code && (
        <button
          onClick={handleLink}
          disabled={requesting}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md bg-app-bg-tertiary py-2 text-[11px] font-semibold text-white transition-colors hover:bg-app-border-primary disabled:opacity-40"
        >
          {requesting ? (
            <Loader2 className="h-[13px] w-[13px] animate-spin" strokeWidth={1.75} />
          ) : status?.linked ? (
            <Send className="h-[13px] w-[13px]" strokeWidth={1.75} />
          ) : (
            <Link2 className="h-[13px] w-[13px]" strokeWidth={1.75} />
          )}
          {status?.linked ? "Re-link (e.g. new phone)" : "Link Telegram"}
        </button>
      )}
    </div>
  );
}
