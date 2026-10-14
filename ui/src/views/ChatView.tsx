import {
  Copy, FolderTree, PanelRightClose, PanelRightOpen, PenLine, Rocket, Stethoscope, Sunrise, Target, Telescope, Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer, type ComposerHandle } from "../components/Composer";
import { Markdown } from "../components/Markdown";
import { Orb } from "../components/Orb";
import { TaskCard } from "../components/TaskCard";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Message } from "../lib/types";

const STARTERS = [
  { icon: Sunrise, title: "Morning briefing", text: "/morning-briefing", desc: "Weather, headlines, reminders and PC health." },
  { icon: Telescope, title: "Deep research", text: "/deep-research ", desc: "Multi-source research with citations." },
  { icon: FolderTree, title: "Tidy my Downloads", text: "/file-organizer Organise my Downloads folder", desc: "Sort files into folders, safely." },
  { icon: Stethoscope, title: "Why is my PC slow?", text: "/pc-doctor", desc: "Diagnose and fix performance issues." },
  { icon: Target, title: "Focus mode", text: "/focus-mode 45 minutes", desc: "Close distractions and start a timer." },
  { icon: PenLine, title: "Write something", text: "/writer ", desc: "Emails, posts and docs in your voice." },
];

function greeting(name: string) {
  const h = new Date().getHours();
  const part = h < 5 ? "Working late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return name ? `${part}, ${name}.` : `${part}.`;
}

export function MessageView({ m }: { m: Message }) {
  const name = useStore((s) => s.engine?.settings.assistant_name ?? "ORION");
  const task = useStore((s) => (m.meta.task_id ? s.tasks[m.meta.task_id] : undefined));
  if (m.role === "user") {
    return (
      <div className="msg user">
        <div className="who">You</div>
        <div className="body">
          {!!m.meta.attachments?.length && (
            <div className="attach-chips">{m.meta.attachments.map((a, i) => <span key={i} className="file-chip">{a}</span>)}</div>
          )}
          {m.content}
        </div>
      </div>
    );
  }
  const model = m.meta.model;
  return (
    <div className="msg assistant">
      <div className="who">{name}</div>
      <div className="body">
        {task && <TaskCard task={task} />}
        {m.content ? <Markdown text={m.content} /> : m.streaming && !task?.steps.length ? <div className="typing"><i /><i /><i /></div> : null}
        {!m.streaming && (
          <div className="msg-meta">
            {model === "local" ? <span className="badge safe"><Zap size={11} /> instant · no API call</span>
              : model ? <span className="badge">{model}</span> : null}
            {m.meta.latency_ms != null && <span>{(m.meta.latency_ms / 1000).toFixed(m.meta.latency_ms < 1000 ? 2 : 1)}s</span>}
            {m.meta.source === "voice" && <span>· voice</span>}
            <button className="icon-btn sm" title="Copy" onClick={() => navigator.clipboard.writeText(m.content)}><Copy size={12} /></button>
          </div>
        )}
      </div>
    </div>
  );
}

export function ChatView() {
  const s = useStore();
  const composer = useRef<ComposerHandle>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const conv = s.conversations.find((c) => c.id === s.activeId);
  const [title, setTitle] = useState(conv?.title ?? "");
  useEffect(() => setTitle(conv?.title ?? ""), [conv?.title, s.activeId]);

  const busy = useMemo(() => s.messages.some((m) => m.streaming) ||
    Object.values(s.tasks).some((t) => t.conv_id === s.activeId && (t.status === "running" || t.status === "waiting")),
  [s.messages, s.tasks, s.activeId]);

  // Approvals from other conversations are shown by the ApprovalDock instead.
  const approvals = s.approvals.filter((a) => !a.task_id || !s.tasks[a.task_id]?.conv_id || s.tasks[a.task_id]?.conv_id === s.activeId);

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  useEffect(() => {
    composer.current?.focus();
    stick.current = true;
  }, [s.activeId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && busy) s.stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, s]);

  const toggleRight = () => {
    localStorage.setItem("orion.right", s.rightOpen ? "0" : "1");
    s.set({ rightOpen: !s.rightOpen });
  };

  const name = s.engine?.settings.user_name ?? "";
  const empty = s.messages.length === 0;

  return (
    <div className="main">
      <div className="topbar">
        {conv ? (
          <input className="title-input" value={title} onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title.trim() && title !== conv.title && api.patch(`/api/conversations/${conv.id}`, { title: title.trim() })}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} aria-label="Conversation title" />
        ) : <h1>New conversation</h1>}
        <span className="grow" />
        <div className="segmented" role="radiogroup" aria-label="Thinking mode">
          {(["auto", "fast", "smart"] as const).map((m) => (
            <button key={m} className={s.mode === m ? "on" : ""} onClick={() => s.set({ mode: m })}
              title={m === "auto" ? "ORION picks the model" : m === "fast" ? "Flash-Lite: quickest, biggest free quota" : "Flash: stronger reasoning"}>
              {m === "auto" ? <Rocket size={13} /> : m === "fast" ? <Zap size={13} /> : <Telescope size={13} />}
              {m === "auto" ? "Auto" : m === "fast" ? "Fast" : "Deep"}
            </button>
          ))}
        </div>
        <button className="icon-btn" onClick={toggleRight} title="Toggle side panel">
          {s.rightOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
        </button>
      </div>

      <div className="chat-scroll" ref={scroller}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {empty ? (
          <div className="home">
            <Orb size={150} state={s.voice.state} interactive onClick={() => api.post("/api/voice/toggle").catch(() => {})} />
            <h2>{greeting(name)}</h2>
            <p className="sub">What can I do for you? Type, drop a file, or press the orb to talk.</p>
            <div className="cards">
              {STARTERS.map((c) => (
                <button key={c.title} className="card" onClick={() => composer.current?.fill(c.text)}>
                  <span className="ct"><c.icon size={16} /> {c.title}</span>
                  <span className="cd">{c.desc}</span>
                </button>
              ))}
            </div>
            {s.transcript && <div className="transcript">“{s.transcript.text}”</div>}
          </div>
        ) : (
          <div className="chat-inner">
            {s.messages.map((m) => <MessageView key={m.id} m={m} />)}
            {s.transcript && (
              <div className={`msg ${s.transcript.role === "user" ? "user" : "assistant"}`} style={{ opacity: 0.6 }}>
                <div className="who">{s.transcript.role === "user" ? "You" : (s.engine?.settings.assistant_name ?? "ORION")}</div>
                <div className="body">{s.transcript.text}</div>
              </div>
            )}
            {approvals.map((a, i) => <ApprovalCard key={a.id} a={a} hotkey={i === approvals.length - 1} />)}
          </div>
        )}
        {empty && approvals.length > 0 && (
          <div className="chat-inner" style={{ paddingTop: 0 }}>{approvals.map((a, i) => <ApprovalCard key={a.id} a={a} hotkey={i === approvals.length - 1} />)}</div>
        )}
      </div>
      <Composer ref={composer} busy={busy} />
    </div>
  );
}
