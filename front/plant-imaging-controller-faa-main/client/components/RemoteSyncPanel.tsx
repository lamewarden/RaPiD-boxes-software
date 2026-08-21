import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, FolderSync, KeyRound, Loader2, Plug, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import { api } from "@/lib/api";
import { getUsername } from "@/lib/session";
import type { RemoteSyncStatus } from "@shared/api";

/**
 * Remote CIFS/SMB sync card for Settings -> General.
 *
 * Two things here are deliberate and easy to break by accident:
 *
 *  1. **The password's length is never revealed.** The backend does not return
 *     the password at all (there is no field for it on RemoteSyncStatus), so
 *     when one is already set the field renders EMPTY with a fixed-width
 *     placeholder — never a string sized to the real password.
 *
 *  2. **"Credentials needed after restart" is a loud state, not a footnote.**
 *     The password is session-only by design, so any restart (a reboot, a
 *     power blip, or the monthly OTA update) leaves sync switched on but
 *     unable to copy anything. That must never look like a working sync, so
 *     it takes over the card with an orange banner.
 *
 * The same tradeoff is stated twice on purpose: as static helper text next to
 * the fields (so the expectation is set *while* someone types the password and
 * before they walk away trusting a long unattended run), and as a confirmation
 * toast the moment credentials are accepted.
 */

// Fixed width, always 8 — NOT the real password's length.
const MASKED_PLACEHOLDER = "••••••••";

const CREDENTIALS_NOTICE =
  "For better security, credentials are not stored on disk and must be entered again after every system restart.";

type Field = "server" | "username" | "password";

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function RemoteSyncPanel() {
  const [status, setStatus] = useState<RemoteSyncStatus | null>(null);
  const [server, setServer] = useState("");
  const [username, setUsername] = useState("");
  // Only ever holds a password the operator is entering right now. It is
  // cleared as soon as it has been sent, and is never populated from the API.
  const [password, setPassword] = useState("");
  const [editing, setEditing] = useState<Field | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncingAll, setSyncingAll] = useState(false);
  const dirtyRef = useRef(false);

  const researcher = getUsername();

  const refresh = useCallback(async () => {
    try {
      const next = await api.remoteSync();
      setStatus(next);
      // Don't clobber half-typed edits with polled values.
      if (!dirtyRef.current) {
        setServer(next.server);
        setUsername(next.username);
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
  const canCheck = username.trim().length > 0 && hasPassword;

  /** PUT the current field values. Fires the credentials notice when a
   *  password was actually part of what got accepted. */
  const saveCredentials = async (extra: { enabled?: boolean } = {}) => {
    const sentPassword = password.length > 0;
    const next = await api.saveRemoteSync({
      server: server.trim() || undefined,
      username: username.trim() || undefined,
      researcher,
      ...(sentPassword ? { password } : {}),
      ...extra,
    });
    setStatus(next);
    setPassword("");
    dirtyRef.current = false;
    if (sentPassword) {
      // Informational, not a warning: this is a deliberate design property.
      // Longer than the default because it is a sentence worth reading (same
      // precedent as the important toast in UpdatePanel.tsx).
      toast.success(CREDENTIALS_NOTICE, { duration: 15000 });
    }
    return next;
  };

  const handleToggle = async () => {
    if (!status || saving) return;
    setSaving(true);
    try {
      if (status.enabled) {
        const next = await api.saveRemoteSync({ enabled: false });
        setStatus(next);
        setPassword("");
        toast.success("Remote sync switched off.");
      } else {
        await saveCredentials({ enabled: true });
        toast.success(`Remote sync on — copying to ${server.trim()}/${researcher}`);
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
      const result = await api.checkRemoteSync();
      setStatus(result.status);
      if (result.ok) {
        toast.success(result.message);
      } else {
        // Report the real error — "wrong password" and "host unreachable"
        // need very different fixes.
        toast.error(result.message, { duration: 12000 });
      }
    } catch (e) {
      toast.error((e as Error).message, { duration: 12000 });
    } finally {
      setChecking(false);
    }
  };

  const handleSyncAll = async () => {
    if (syncingAll) return;
    setSyncingAll(true);
    try {
      const next = await api.syncAllRemote(researcher);
      setStatus(next);
      toast.success(`Copying all of ${researcher}'s experiments in the background…`);
    } catch (e) {
      toast.error((e as Error).message, { duration: 12000 });
    } finally {
      setSyncingAll(false);
    }
  };

  const openEditor = (field: Field) => setEditing(field);

  const editorTitle =
    editing === "server"
      ? "Server / share path"
      : editing === "username"
        ? "Share username"
        : "Share password";

  const editorValue = editing === "server" ? server : editing === "username" ? username : "";

  const applyEditor = (value: string) => {
    dirtyRef.current = true;
    if (editing === "server") setServer(value);
    else if (editing === "username") setUsername(value);
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
            Remote Sync
          </div>
          <button
            onClick={handleToggle}
            disabled={saving || status == null}
            aria-label="Toggle remote sync"
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

        {/* The loud post-restart state: on, but unable to do anything. */}
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
                Sync is switched on but nothing is being copied. The password is never saved
                to disk, so it was lost when the box restarted. Re-enter it below and press
                Check Connection to resume.
              </p>
            </div>
          </div>
        )}

        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
          <dt className="text-app-text-muted">Status</dt>
          <dd
            className={`font-semibold ${
              credentialsRequired
                ? "text-app-orange"
                : status?.mounted
                  ? "text-app-green"
                  : "text-app-text-secondary"
            }`}
          >
            {status == null
              ? "—"
              : credentialsRequired
                ? "Credentials needed"
                : status.mounted
                  ? "Mounted"
                  : status.enabled
                    ? "On — not mounted yet"
                    : "Off"}
          </dd>

          <dt className="text-app-text-muted">Destination</dt>
          <dd className="truncate font-semibold text-white" title={status?.remotePath ?? undefined}>
            {status?.remotePath ?? `${server || "—"}/${researcher}`}
          </dd>

          <dt className="text-app-text-muted">Last sync</dt>
          <dd className="truncate font-semibold text-white">
            {status?.lastSyncAt ? formatTime(status.lastSyncAt) : "Never"}
          </dd>

          {status != null && status.pendingCount > 0 && (
            <>
              <dt className="text-app-text-muted">Pending</dt>
              <dd className="font-semibold text-app-orange">
                {status.pendingCount} file{status.pendingCount === 1 ? "" : "s"} not yet copied
              </dd>
            </>
          )}
        </dl>

        {status?.lastResult === "error" && status.lastError && !credentialsRequired && (
          <p className="mt-2 rounded-md bg-app-bg-tertiary p-2 text-[10px] text-app-orange-light">
            {status.lastError}
          </p>
        )}

        {status?.simulation && (
          <p className="mt-2 text-[10px] text-app-text-muted">
            Simulation mode: no real network share is mounted; copies go to a local folder.
          </p>
        )}

        {/* --- credentials ------------------------------------------------ */}
        <div className="mt-3 flex flex-col gap-1.5">
          <label className="text-[10px] font-semibold uppercase tracking-[0.5px] text-app-text-muted">
            Server / share
          </label>
          <input
            type="text"
            value={server}
            onChange={(e) => {
              dirtyRef.current = true;
              setServer(e.target.value);
            }}
            onClick={() => openEditor("server")}
            placeholder="//server/share/folder"
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
            placeholder="share account"
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
            // Fixed-width placeholder. This is NOT the stored password's
            // length — the backend never tells us that, and it must not.
            placeholder={status?.passwordSet ? MASKED_PLACEHOLDER : "share password"}
            className="w-full rounded-md border border-app-border-primary bg-app-bg-primary px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-app-green"
          />

          {/* Always visible, whatever the current state: sets the expectation
              while the password is being typed, not after sync has broken. */}
          <p className="mt-1 text-[10px] leading-snug text-app-text-muted">{CREDENTIALS_NOTICE}</p>
        </div>

        <div className="mt-2.5 flex gap-2">
          <button
            onClick={handleCheck}
            disabled={!canCheck || checking}
            title={
              canCheck ? undefined : "Enter a username and password first"
            }
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-app-bg-tertiary py-2 text-[11px] font-semibold text-white transition-colors hover:bg-app-border-primary disabled:opacity-40"
          >
            {checking ? (
              <Loader2 className="h-[13px] w-[13px] animate-spin" strokeWidth={1.75} />
            ) : (
              <Plug className="h-[13px] w-[13px]" strokeWidth={1.75} />
            )}
            {checking ? "Checking…" : "Check Connection"}
          </button>

          <button
            onClick={handleSyncAll}
            disabled={
              syncingAll || !status?.enabled || !status?.passwordSet || status.bulkInProgress
            }
            title={
              status?.enabled && status?.passwordSet
                ? `Copy every local experiment of ${researcher} to the share`
                : "Switch sync on and enter the password first"
            }
            className="flex flex-1 items-center justify-center gap-1.5 rounded-md bg-app-bg-tertiary py-2 text-[11px] font-semibold text-white transition-colors hover:bg-app-border-primary disabled:opacity-40"
          >
            {status?.bulkInProgress ? (
              <RefreshCw className="h-[13px] w-[13px] animate-spin" strokeWidth={1.75} />
            ) : (
              <FolderSync className="h-[13px] w-[13px]" strokeWidth={1.75} />
            )}
            Sync Entire Folder
          </button>
        </div>

        {status?.bulkMessage && (
          <p className="mt-2 text-[10px] text-app-text-secondary">{status.bulkMessage}</p>
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
