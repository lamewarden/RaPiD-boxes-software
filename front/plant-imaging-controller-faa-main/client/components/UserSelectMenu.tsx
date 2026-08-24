import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, User, X } from "lucide-react";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import { api } from "@/lib/api";

/**
 * Tapping the researcher name in the top nav opens this picker first, rather
 * than jumping straight to the keyboard -- most sessions are an existing
 * researcher picking themselves, not typing a name from scratch. Only "+ New
 * User" reaches the keyboard, and only for a genuinely new name.
 */
export default function UserSelectMenu({
  currentUsername,
  onSelect,
  onClose,
}: {
  currentUsername: string;
  onSelect: (username: string) => void;
  onClose: () => void;
}) {
  const [creatingNew, setCreatingNew] = useState(false);
  const { data: users, isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.users(),
  });

  if (creatingNew) {
    return (
      <OnScreenKeyboard
        title="New researcher name"
        initialValue=""
        onCancel={() => setCreatingNew(false)}
        onConfirm={(v) => onSelect(v)}
      />
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[80%] w-full max-w-[420px] flex-col rounded-xl border border-app-border-primary bg-app-bg-secondary shadow-2xl">
        <div className="flex items-center justify-between border-b border-app-border-primary px-3 py-2">
          <span className="text-[13px] font-bold uppercase tracking-wide text-white">
            Select Researcher
          </span>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-app-text-secondary transition-colors hover:bg-app-bg-tertiary hover:text-white"
          >
            <X className="h-[16px] w-[16px]" strokeWidth={1.5} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {isLoading ? (
            <div className="flex h-16 items-center justify-center text-sm text-app-text-muted">
              Loading…
            </div>
          ) : !users || users.length === 0 ? (
            <p className="p-3 text-center text-[12px] text-app-text-muted">
              No researchers on this device yet.
            </p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {users.map((name) => {
                const active = name.toLowerCase() === currentUsername.toLowerCase();
                return (
                  <button
                    key={name}
                    onClick={() => onSelect(name)}
                    className={`flex items-center gap-2 rounded-[10px] border px-3 py-2.5 text-left transition-colors ${
                      active
                        ? "border-app-green/60 bg-app-green/15 text-white"
                        : "border-app-border-primary bg-app-bg-tertiary text-white hover:bg-app-border-primary"
                    }`}
                  >
                    <User className="h-[16px] w-[16px] flex-shrink-0" strokeWidth={1.5} />
                    <span className="truncate text-[13px] font-semibold">{name}</span>
                    {active && (
                      <span className="ml-auto flex-shrink-0 text-[10px] font-bold uppercase text-app-green-light">
                        Current
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t border-app-border-primary p-2">
          <button
            onClick={() => setCreatingNew(true)}
            className="flex w-full items-center justify-center gap-2 rounded-[10px] bg-app-green px-4 py-2.5 text-white transition-colors hover:bg-app-green-light"
          >
            <Plus className="h-[16px] w-[16px]" strokeWidth={2} />
            <span className="text-[12px] font-black uppercase tracking-[1px]">New User</span>
          </button>
        </div>
      </div>
    </div>
  );
}
