import { useState } from "react";
import { Link } from "react-router-dom";
import { X, User, Folder, Radio } from "lucide-react";
import OnScreenKeyboard from "@/components/OnScreenKeyboard";
import { getUsername, setUsername } from "@/lib/session";

export default function TopNav() {
  const [editingUser, setEditingUser] = useState(false);
  const [username, setUser] = useState(getUsername());

  const btn =
    "flex w-[199.25px] py-1.5 px-0 justify-center items-center gap-2 rounded-md border-r border-app-border-secondary bg-app-bg-tertiary hover:bg-app-border-primary transition-colors";

  return (
    <div className="flex p-0.5 justify-center items-start self-stretch border-b border-app-border-primary bg-app-bg-secondary">
      <Link to="/" className={btn}>
        <X className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Close</span>
      </Link>

      <button className={btn} onClick={() => setEditingUser(true)}>
        <User className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">
          {username}
        </span>
      </button>

      <Link to="/gallery" className={btn}>
        <Folder className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Folder</span>
      </Link>

      <Link
        to="/live"
        state={{ from: window.location.pathname }}
        className="flex w-[198.25px] py-1.5 px-0 justify-center items-center gap-2 rounded-md bg-app-bg-tertiary hover:bg-app-border-primary transition-colors"
      >
        <Radio className="w-[18px] h-[18px]" strokeWidth={1.5} />
        <span className="text-white text-center text-[13px] font-semibold leading-5">Live</span>
      </Link>

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
