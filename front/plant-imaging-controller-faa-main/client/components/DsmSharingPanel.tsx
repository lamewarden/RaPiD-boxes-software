import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, KeyRound, Link2, Loader2, Plug } from "lucide-react";
import { toast } from "sonner";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import { api } from "@/lib/api";
import type { DsmSharingStatus } from "@shared/api";

/**
 * Synology DSM sharing-link card for Settings -> General.
 *
 * A DIFFERENT NAS/account than the Remote Sync card above it -- confirmed by
 * DNS, not assumed (ds.asuch.cas.cz and ds-ueb-if.asuch.cas.cz resolve to two
 * different IPs) -- so this has its own credentials, not a reuse of Remote
 * Sync's. Same session-only password precedent though: never stored on
 * disk, never pre-filled, "credentials needed" after every restart. See
 * RemoteSyncPanel.tsx for the fuller explanation of why that tradeoff is
 * stated both as static help text and as a confirmation toast.
 */

const MASKED_PLACEHOLDER = "••••••••";

const CREDENTIALS_NOTICE =
  "For better security, credentials are not stored on disk and must be entered again after every system restart.";

type Field = "host" | "username" | "password" | "shareRoot";

export default function DsmSharingPanel() {
  const [status, setStatus] = useState<DsmSharingStatus | null>(null);
  const [host, setHost] = useState("");
  const [port, setPort] = useState(5001);
  const [username, setUsername] = useState("");
  const [shareRoot, setShareRoot] = useState("");
  // Only ever holds a password the operator is entering right now.
  const [password, setPassword] = useState("");
  const [editing, setEditing] = useState<Field | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const dirtyRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await api.dsmSharing();
      setStatus(next);
      if (!dirtyRef.current) {
        setHost(next.host);
        setPort(next.port);
        setUsername(next.username);
        setShareRoot(next.shareRoot);
      }
    } catch {
      /* best-effort: keep whatever we last had */
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const hasPassword = password.length > 0 || (status?.passwordSet ?? false);
  const canCheck = host.trim().length > 0 && username.trim().length > 0 && hasPassword;

  const saveCredentials = async (extra: { enabled?: boolean } = {}) => {
    const sentPassword = password.length > 0;
    const next = await api.saveDsmSharing({
      host: host.trim() || undefined,
      port,
      username: username.trim() || undefined,
      shareRoot: shareRoot.trim() || undefined,
      ...(sentPassword ? { password } : {}),
      ...extra,
    });
    setStatus(next);
    setPassword("");
    dirtyRef.current = false;
    if (sentPassword) {
      toast.success(CREDENTIALS_NOTICE, { duration: 15000 });
    }
    return next;
  };

  const handleToggle = async () => {
    if (!status || saving) return;
    setSaving(true);
    try {
      if (status.enabled) {
        const next = await api.saveDsmSharing({ enabled: false });
        setStatus(next);
        setPassword("");
        toast.success("Sharing links switched off.");
      } else {
        await saveCredentials({ enabled: true });
        toast.success(`Sharing links on — connected to ${host.trim()}`);
      }
    } catch (e) {
      toast.error((e as Error).message);
      await refresh();
    } finally {
      setSaving(false);
    }
  };

  const handleCheck = async () => {
    if (!canCheck || checking) return;
    setChecking(true);
    try {
      await saveCredentials();
      const result = await api.checkDsmSharing();
      setStatus(result.status);
      if (result.ok) {
        toast.success(result.message);
      } else {
        toast.error(result.message, { duration: 12000 });
      }
    } catch (e) {
      toast.error((e as Error).message, { duration: 12000 });
    } finally {
      setChecking(false);
    }
  };

  const openEditor = (field: Field) => setEditing(field);

  const editorTitle =
    editing === "host"
      ? "DSM host"
      : editing === "username"
        ? "DSM username"
        : editing === "shareRoot"
          ? "DSM share root"
          : "DSM password";

  const editorValue =
    editing === "host" ? host : editing === "username" ? username : editing === "shareRoot" ? shareRoot : "";

  const applyEditor = (value: string) => {
    dirtyRef.current = true;
    if (editing === "host") setHost(value);
    else if (editing === "username") setUsername(value);
    else if (editing === "shareRoot") setShareRoot(value);
    else if (editing === "password") setPassword(value);
    setEditing(null);
  };

  const credentialsRequired = status?.credentialsRequired ?? false;

  return (
    <>
      <div
        className={`rounded-[10px] border p-3 ${
          credentialsRequired
            ? "border-app-orange bg-app-orange/10"
            : "border-app-border-primary bg-app-bg-secondary"
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="text-[10px] font-bold uppercase tracking-[0.5px] text-app-text-muted">
            Sharing Links
          </div>
          <button
            onClick={handleToggle}
            disabled={saving || status == null}
            aria-label="Toggle DSM sharing links"
            className={`relative h-[20px] w-[36px] rounded-full transition-colors disabled:opacity-60 ${
              status?.enabled ? "bg-app-green" : "bg-app-bg-tertiary"
            }`}
          >
            <span
              className={`absolute top-[2px] h-[16px] w-[16px] rounded-full bg-white transition-all ${
                status?.enabled ? "left-[18px]" : "left-[2px]"
              }`}
            />
          </button>
        </div>

        <p className="mt-1.5 text-[10px] leading-snug text-app-text-muted">
          Lets PidiBot hand out a real, clickable link (like DSM's own File Station "Share"
          button) when someone asks it to upload an experiment. A different NAS account from
          Remote Sync above — this one only creates links for files Remote Sync already copied
          there.
        </p>

        {credentialsRequired && (
          <div className="mt-2 flex gap-2 rounded-md border border-app-orange/50 bg-app-orange/15 p-2.5">
            <AlertTriangle
              className="h-[16px] w-[16px] flex-shrink-0 text-app-orange"
              strokeWidth={2}
            />
            <div>
              <p className="text-[11px] font-bold uppercase tracking-wide text-app-orange-light">
                Inactive — credentials needed after restart
              </p>
              <p className="mt-1 text-[10px] text-app-text-secondary">
                Re-enter the password below and press Check Connection to resume.
              </p>
            </div>
          </div>
        )}

        {status?.lastResult === "error" && status.lastError && !credentialsRequired && (
          <p className="mt-2 rounded-md bg-app-bg-tertiary p-2 text-[10px] text-app-orange-light">
            {status.lastError}
          </p>
        )}

        <div className="mt-3 flex flex-col gap-1.5">
          <label className="text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            Host
          </label>
          <input
            type="text"
            value={host}
            onChange={(e) => {
              dirtyRef.current = true;
              setHost(e.target.value);
            }}
            onClick={() => openEditor("host")}
            placeholder="ds-ueb-if.asuch.cas.cz"
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 font-mono text-[11px] text-white outline-none focus:border-app-green"
          />

          <label className="mt-1 text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            Port
          </label>
          <input
            type="number"
            value={port}
            onChange={(e) => {
              dirtyRef.current = true;
              setPort(Number(e.target.value) || 5001);
            }}
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 font-mono text-[11px] text-white outline-none focus:border-app-green"
          />

          <label className="mt-1 text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            Username
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => {
              dirtyRef.current = true;
              setUsername(e.target.value);
            }}
            onClick={() => openEditor("username")}
            placeholder="DSM account"
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-app-green"
          />

          <label className="mt-1 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            <span>Password</span>
            {status?.passwordSet && password.length === 0 && (
              <span className="flex items-center gap-1 text-app-green">
                <KeyRound className="h-[10px] w-[10px]" strokeWidth={2.5} />
                Password is set
              </span>
            )}
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => {
              dirtyRef.current = true;
              setPassword(e.target.value);
            }}
            onClick={() => openEditor("password")}
            placeholder={status?.passwordSet ? MASKED_PLACEHOLDER : "DSM password"}
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-app-green"
          />

          <label className="mt-1 text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            Share root
          </label>
          <input
            type="text"
            value={shareRoot}
            onChange={(e) => {
              dirtyRef.current = true;
              setShareRoot(e.target.value);
            }}
            onClick={() => openEditor("shareRoot")}
            placeholder="/volume1/ueb-if"
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 font-mono text-[11px] text-white outline-none focus:border-app-green"
          />
          <p className="text-[10px] leading-snug text-app-text-muted">
            DSM's own internal path to where Remote Sync's files land — not the //server/share
            path above, a File Station path like /volume1/ueb-if. Find it by browsing to that
            folder in DSM's File Station and checking the address bar.
          </p>

          <p className="mt-1 text-[10px] leading-snug text-app-text-muted">{CREDENTIALS_NOTICE}</p>
        </div>

        <div className="mt-2.5 flex gap-2">
          <button
            onClick={handleCheck}
            disabled={!canCheck || checking}
            title={canCheck ? undefined : "Enter a host, username and password first"}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-app-bg-tertiary py-2 text-[11px] font-semibold text-white transition-colors hover:bg-app-border-primary disabled:opacity-40"
          >
            {checking ? (
              <Loader2 className="h-[13px] w-[13px] animate-spin" strokeWidth={1.75} />
            ) : (
              <Plug className="h-[13px] w-[13px]" strokeWidth={1.75} />
            )}
            {checking ? "Checking…" : "Check Connection"}
          </button>
        </div>

        {status?.enabled && !credentialsRequired && status?.lastResult === "ok" && (
          <p className="mt-2 flex items-center gap-1.5 text-[10px] text-app-green">
            <Link2 className="h-[11px] w-[11px]" strokeWidth={2} />
            Connected — PidiBot can hand out real links now.
          </p>
        )}
      </div>

      {editing && (
        <OnScreenKeyboard
          title={editorTitle}
          initialValue={editorValue}
          masked={editing === "password"}
          onCancel={() => setEditing(null)}
          onConfirm={applyEditor}
        />
      )}
    </>
  );
}
