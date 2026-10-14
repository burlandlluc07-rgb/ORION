import { Brain, Plus, Trash2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { timeAgo } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Memory } from "../lib/types";

export function MemoryView() {
  const bump = useStore((s) => s.bump["memory.changed"]);
  const [items, setItems] = useState<Memory[]>([]);
  const [text, setText] = useState("");
  const [profile, setProfile] = useState(false);
  const [q, setQ] = useState("");
  const load = () => api.get<Memory[]>("/api/memories").then(setItems);
  useEffect(() => { load(); }, [bump]);

  const add = async () => {
    if (!text.trim()) return;
    await api.post("/api/memories", { text, profile });
    setText("");
    load();
  };
  const shown = items.filter((m) => !q || m.text.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Memory</h2>
          <p>What ORION knows about you. Only the relevant pieces are sent with each request. Stored locally on this PC.</p>
        </div>
      </div>
      <div className="item" style={{ marginBottom: 18 }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder="Teach ORION something — e.g. “I prefer Celsius and 24-hour time”" />
        <button className={`btn ${profile ? "primary" : ""}`} title="Core facts are always included" onClick={() => setProfile(!profile)}>
          <UserRound size={14} /> Core
        </button>
        <button className="btn primary" onClick={add} disabled={!text.trim()}><Plus size={14} /> Add</button>
      </div>
      {items.length > 6 && <input className="input" style={{ maxWidth: 320, marginBottom: 12 }} placeholder="Search memories" value={q} onChange={(e) => setQ(e.target.value)} />}
      {shown.length ? (
        <div className="list">
          {shown.map((m) => (
            <div key={m.id} className="item" style={{ padding: "10px 14px" }}>
              {m.kind === "profile" ? <UserRound size={16} style={{ color: "var(--accent)" }} /> : <Brain size={16} className="muted" />}
              <div className="grow">
                <div>{m.text}</div>
                <div className="d">{m.kind === "profile" ? "core" : m.kind === "fact" ? "fact" : m.kind} · {timeAgo(m.created)}</div>
              </div>
              <button className="icon-btn" title="Forget" onClick={() => api.del(`/api/memories/${m.id}`).then(load)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      ) : <div className="empty"><Brain size={28} /><div>Nothing remembered yet. Tell ORION about yourself and it will remember.</div></div>}
    </div></div>
  );
}
