import { Hand, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Avatar } from "./Avatar";

type GhostWin = { id: number; title: string; app: string; opened: number; last_action: string };

/** ORION's ghost windows, each with Take over (it becomes a normal window, yours) and Close. Hidden when none. */
export function GhostDock() {
  const bump = useStore((s) => s.bump["ghost.changed"]);
  const [items, setItems] = useState<GhostWin[]>([]);
  const load = () => api.get<{ windows: GhostWin[] }>("/api/ghost").then((r) => setItems(r.windows)).catch(() => {});
  useEffect(() => { load(); }, [bump]);
  if (!items.length) return null;
  const act = async (id: number, action: "take-over" | "close") => {
    await api.post(`/api/ghost/${id}/${action}`).catch(() => {});
    load();
  };
  return (
    <div className="ghost-dock">
      <div className="ghost-dock-head"><Avatar size={20} mood="working" /> ORION is working in {items.length} ghost window{items.length > 1 ? "s" : ""}</div>
      {items.map((g) => (
        <div key={g.id} className="ghost-row">
          <div className="grow">
            <div className="ghost-title">{g.title}</div>
            {g.last_action && <div className="muted ghost-last">{g.last_action}</div>}
          </div>
          <button className="btn sm" onClick={() => act(g.id, "take-over")} title="Make it a normal window you can use"><Hand size={12} /> Take over</button>
          <button className="icon-btn" onClick={() => act(g.id, "close")} aria-label={`Close ${g.title}`}><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}
