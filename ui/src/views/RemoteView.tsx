import { Lock, MonitorUp, Pause, Plus, SkipForward, Square, Volume1, Activity } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer, type ComposerHandle } from "../components/Composer";
import { Orb } from "../components/Orb";
import { useStore } from "../lib/store";
import { MessageView } from "./ChatView";

const QUICK = [
  { icon: Pause, label: "Play/pause", text: "pause the music" },
  { icon: SkipForward, label: "Next", text: "next song" },
  { icon: Volume1, label: "Quieter", text: "volume down" },
  { icon: Activity, label: "PC status", text: "how's my pc doing" },
  { icon: MonitorUp, label: "Screen", text: "What's on my screen right now? One sentence." },
  { icon: Lock, label: "Lock PC", text: "lock my pc" },
];

/** Mobile companion served to your phone by the phone remote (Settings → Phone remote). */
export function RemoteView() {
  const s = useStore();
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const last = localStorage.getItem("orion.remoteConv");
    if (last) s.openConversation(last).catch(() => localStorage.removeItem("orion.remoteConv"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (s.activeId) localStorage.setItem("orion.remoteConv", s.activeId); }, [s.activeId]);
  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; });

  const busy = useMemo(() => s.messages.some((m) => m.streaming) ||
    Object.values(s.tasks).some((t) => t.conv_id === s.activeId && (t.status === "running" || t.status === "waiting")),
  [s.messages, s.tasks, s.activeId]);

  return (
    <div className="remote">
      <header className="remote-head">
        <Orb size={30} state={busy ? "working" : s.voice.state} />
        <div className="grow">
          <div className="brand-name">ORION</div>
          <div className="brand-sub"><span className={`dot ${s.connected ? "ok" : "warn pulse"}`} /> {s.connected ? "Connected" : "Reconnecting…"}</div>
        </div>
        <select className="select" style={{ width: 150 }} value={s.activeId ?? ""} onChange={(e) => s.openConversation(e.target.value || null)} aria-label="Conversation">
          <option value="">New conversation</option>
          {s.conversations.slice(0, 30).map((c) => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
        <button className="icon-btn" onClick={() => s.openConversation(null)} aria-label="New conversation"><Plus size={18} /></button>
      </header>

      <div className="remote-scroll" ref={scroller}>
        <div className="chat-inner" style={{ padding: "16px 14px 16px" }}>
          {s.messages.length === 0 && <div className="muted" style={{ textAlign: "center", padding: "40px 10px" }}>Control your PC from here. Try a quick action below or type anything.</div>}
          {s.messages.map((m) => <MessageView key={m.id} m={m} />)}
          {s.approvals.map((a) => <ApprovalCard key={a.id} a={a} />)}
        </div>
      </div>

      <div className="remote-quick">
        {busy && <button className="btn sm" onClick={() => s.stop()}><Square size={12} /> Stop</button>}
        {QUICK.map((q) => (
          <button key={q.label} className="btn sm" disabled={busy} onClick={() => s.send(q.text)}><q.icon size={13} /> {q.label}</button>
        ))}
      </div>
      <div className="remote-composer"><Composer ref={composer} busy={busy} remote /></div>
    </div>
  );
}
