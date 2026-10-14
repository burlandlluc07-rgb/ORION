import {
  Blocks, Brain, CalendarClock, ListChecks, MessageSquare, PanelLeftClose, PanelLeftOpen, Pin, PinOff, Plus,
  Search, Settings as SettingsIcon, Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { quotaSummary, useStore, type View } from "../lib/store";
import { Orb } from "./Orb";

const NAV: { id: View; label: string; icon: typeof MessageSquare }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "routines", label: "Routines", icon: CalendarClock },
  { id: "ecosystem", label: "Ecosystem", icon: Blocks },
  { id: "memory", label: "Memory", icon: Brain },
];

export function Sidebar() {
  const s = useStore();
  const [q, setQ] = useState("");
  const open = s.sidebarOpen;
  const running = Object.values(s.tasks).filter((t) => t.status === "running" || t.status === "waiting").length;
  const quota = quotaSummary(s.engine?.quota);

  const convs = useMemo(() => {
    const f = q.trim().toLowerCase();
    return s.conversations.filter((c) => !f || c.title.toLowerCase().includes(f));
  }, [s.conversations, q]);

  const toggle = () => {
    localStorage.setItem("orion.sidebar", open ? "0" : "1");
    s.set({ sidebarOpen: !open });
  };

  return (
    <aside className={`sidebar ${open ? "" : "collapsed"}`}>
      <div className="brand">
        <Orb size={30} state={s.voice.state} />
        {open && (
          <div className="grow">
            <div className="brand-name">ORION</div>
            <div className="brand-sub">v{s.engine?.version ?? "2"} · {s.connected ? "online" : "connecting…"}</div>
          </div>
        )}
        <button className="icon-btn sm" onClick={toggle} aria-label="Toggle sidebar" title="Toggle sidebar (Ctrl+B)">
          {open ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
        </button>
      </div>

      <button className="new-chat" onClick={() => s.openConversation(null)} title="New chat (Ctrl+N)">
        <Plus size={16} /> {open && <>New chat <kbd>Ctrl N</kbd></>}
      </button>

      <nav className="nav">
        {NAV.map((n) => (
          <button key={n.id} className={`nav-item ${s.view === n.id ? "active" : ""}`} onClick={() => s.setView(n.id)} title={n.label}>
            <n.icon size={17} />
            {open && n.label}
            {open && n.id === "tasks" && running > 0 && <span className="count">{running}</span>}
          </button>
        ))}
      </nav>

      {open ? (
        <>
          <div className="side-section">Conversations</div>
          <div className="conv-search">
            <Search size={14} />
            <input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="conv-list">
            {convs.length === 0 && <div className="muted" style={{ padding: "8px 12px", fontSize: 12.5 }}>No conversations yet.</div>}
            {convs.map((c) => (
              <div key={c.id} className={`conv ${s.activeId === c.id && s.view === "chat" ? "active" : ""}`}
                onClick={() => s.openConversation(c.id)} title={c.title}>
                {c.pinned && <Pin size={12} className="muted" />}
                <span>{c.title}</span>
                <div className="conv-actions" onClick={(e) => e.stopPropagation()}>
                  <button className="icon-btn sm" title={c.pinned ? "Unpin" : "Pin"}
                    onClick={() => api.patch(`/api/conversations/${c.id}`, { pinned: !c.pinned })}>
                    {c.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                  </button>
                  <button className="icon-btn sm" title="Delete"
                    onClick={async () => {
                      await api.del(`/api/conversations/${c.id}`);
                      if (s.activeId === c.id) s.openConversation(null);
                    }}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div style={{ flex: 1 }} />
      )}

      <div className="side-foot">
        {open && quota && (
          <div className="meter" title="Gemini free-tier requests used today (resets at midnight Pacific)" style={{ margin: 0 }}>
            <div className="meter-top">
              <span className="muted" style={{ fontSize: 12 }}>Free quota today</span>
              <span className="v" style={{ fontSize: 12 }}>{quota.used} / {quota.total}</span>
            </div>
            <div className={`bar ${quota.pct > 0.9 ? "bad" : quota.pct > 0.75 ? "warn" : ""}`}><i style={{ width: `${quota.pct * 100}%` }} /></div>
          </div>
        )}
        <button className={`nav-item ${s.view === "settings" ? "active" : ""}`} onClick={() => s.setView("settings")} title="Settings">
          <SettingsIcon size={17} /> {open && "Settings"}
        </button>
      </div>
    </aside>
  );
}
