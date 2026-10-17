import {
  Blocks, Brain, CalendarClock, Download, ListChecks, MessageSquare, PanelLeftClose, PanelLeftOpen, Pin, PinOff, Plus,
  Search, Settings as SettingsIcon, Target, Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { quotaSummary, useStore, type View } from "../lib/store";
import type { Conversation } from "../lib/types";
import { Orb } from "./Orb";

const NAV: { id: View; label: string; icon: typeof MessageSquare }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "missions", label: "Missions", icon: Target },
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "routines", label: "Routines", icon: CalendarClock },
  { id: "ecosystem", label: "Ecosystem", icon: Blocks },
  { id: "memory", label: "Memory", icon: Brain },
];

/** Pinned first, then Today / Yesterday / Previous 7 days / Older, like a chat app's recents. */
function groupConversations(list: Conversation[]) {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const today = start.getTime() / 1000;
  const buckets: [string, Conversation[]][] = [["Pinned", []], ["Today", []], ["Yesterday", []], ["Previous 7 days", []], ["Older", []]];
  for (const c of list) {
    const i = c.pinned ? 0 : c.updated >= today ? 1 : c.updated >= today - 86400 ? 2 : c.updated >= today - 7 * 86400 ? 3 : 4;
    buckets[i][1].push(c);
  }
  return buckets.filter(([, items]) => items.length);
}

export function Sidebar() {
  const s = useStore();
  const [q, setQ] = useState("");
  const open = s.sidebarOpen;
  const running = Object.values(s.tasks).filter((t) => t.status === "running" || t.status === "waiting").length;
  const quota = quotaSummary(s.engine?.quota);
  const userName = s.engine?.settings.user_name || "You";

  const groups = useMemo(() => {
    const f = q.trim().toLowerCase();
    return groupConversations(s.conversations.filter((c) => !f || c.title.toLowerCase().includes(f)));
  }, [s.conversations, q]);

  const toggle = () => {
    localStorage.setItem("orion.sidebar", open ? "0" : "1");
    s.set({ sidebarOpen: !open });
  };

  return (
    <aside className={`sidebar ${open ? "" : "collapsed"}`}>
      <div className="brand">
        <button className="brand-mark" onClick={() => s.openConversation(null)} title="ORION home" aria-label="ORION home">
          <Orb size={30} state={s.voice.state} />
        </button>
        {open && (
          <div className="grow">
            <div className="brand-name">ORION</div>
            <div className="brand-sub">
              <span className={`dot ${s.connected ? "ok" : "warn pulse"}`} />
              {s.connected ? `v${s.engine?.version ?? "2"}` : "connecting…"}
            </div>
          </div>
        )}
        <button className="icon-btn sm" onClick={toggle} aria-label="Toggle sidebar" title="Toggle sidebar (Ctrl+B)">
          {open ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
        </button>
      </div>

      <div className="side-actions">
        <button className="nav-item new-chat" onClick={() => s.openConversation(null)} title="New chat (Ctrl+N)">
          <span className="plus"><Plus size={14} strokeWidth={2.5} /></span>
          {open && <>New chat <kbd>Ctrl N</kbd></>}
        </button>
      </div>

      <nav className="nav">
        {NAV.map((n) => (
          <button key={n.id} className={`nav-item ${s.view === n.id ? "active" : ""}`} onClick={() => s.setView(n.id)} title={n.label}
            aria-current={s.view === n.id ? "page" : undefined}>
            <n.icon size={17} />
            {open && n.label}
            {n.id === "tasks" && running > 0 && <span className="count">{running}</span>}
          </button>
        ))}
      </nav>

      {open ? (
        <>
          <div className="side-section">Recents</div>
          <div className="conv-search">
            <Search size={14} />
            <input placeholder="Search chats" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search chats" />
          </div>
          <div className="conv-list">
            {groups.length === 0 && <div className="muted" style={{ padding: "8px 10px", fontSize: 12.5 }}>{q ? "No matching chats." : "No conversations yet."}</div>}
            {groups.map(([label, items]) => (
              <div key={label}>
                <div className="conv-group">{label}</div>
                {items.map((c) => (
                  <div key={c.id} className={`conv ${s.activeId === c.id && s.view === "chat" ? "active" : ""}`}
                    onClick={() => s.openConversation(c.id)} title={c.title} role="button" tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && s.openConversation(c.id)}>
                    <span>{c.title}</span>
                    <div className="conv-actions" onClick={(e) => e.stopPropagation()}>
                      <button className="icon-btn sm" title={c.pinned ? "Unpin" : "Pin"} aria-label={c.pinned ? "Unpin" : "Pin"}
                        onClick={() => api.patch(`/api/conversations/${c.id}`, { pinned: !c.pinned })}>
                        {c.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                      </button>
                      <button className="icon-btn sm" title="Delete" aria-label="Delete"
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
            ))}
          </div>
        </>
      ) : (
        <div style={{ flex: 1 }} />
      )}

      <div className="side-foot">
        {open && s.engine?.update && s.engine.update.version !== s.engine.version && (
          <a className="side-update" href={s.engine.update.url} target="_blank" rel="noreferrer" title={s.engine.update.notes}>
            <Download size={14} /> ORION {s.engine.update.version} is available
          </a>
        )}
        <button className={`user-chip ${s.view === "settings" ? "active" : ""}`} onClick={() => s.setView("settings")}
          title={quota ? `Settings · Gemini free quota today: ${quota.used} / ${quota.total}` : "Settings"} aria-label="Settings">
          <span className="avatar">{userName.slice(0, 1).toUpperCase()}</span>
          {open && (
            <>
              <span className="grow">
                <div className="nm ellipsis">{userName}</div>
                {quota ? (
                  <>
                    <div className="sub">Free quota {quota.used} / {quota.total} today</div>
                    <div className={`mini-bar ${quota.pct > 0.9 ? "bad" : quota.pct > 0.75 ? "warn" : ""}`}><i style={{ width: `${quota.pct * 100}%` }} /></div>
                  </>
                ) : <div className="sub">Settings</div>}
              </span>
              <SettingsIcon size={16} className="gear" />
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
