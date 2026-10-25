import {
  Check, Copy, FolderTree, PanelRightClose, PanelRightOpen, PenLine, Stethoscope, Sunrise, Target, Telescope, Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer, type ComposerHandle } from "../components/Composer";
import { Markdown } from "../components/Markdown";
import { Logo } from "../components/Logo";
import { TaskCard } from "../components/TaskCard";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Message } from "../lib/types";
import { NARROW, useMedia } from "../lib/useMedia";

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

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="icon-btn sm" title="Copy" aria-label="Copy reply"
      onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1400); })}>
      {done ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

export function MessageView({ m }: { m: Message }) {
  const name = useStore((s) => s.engine?.settings.assistant_name ?? "ORION");
  const task = useStore((s) => (m.meta.task_id ? s.tasks[m.meta.task_id] : undefined));
  if (m.role === "user") {
    return (
      <div className="msg user">
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
    <div className="msg assistant" aria-label={`${name} said`}>
      <div className="body">
        {task && <TaskCard task={task} />}
        {m.content && <Markdown text={m.content} />}
        {m.streaming && <div className="msg-thinking" aria-label="ORION is working"><Logo size={26} state="thinking" label="ORION is working" /></div>}
        {!m.streaming && (
          <div className="msg-meta">
            <CopyButton text={m.content} />
            {model === "local" ? <span className="badge safe"><Zap size={11} /> instant · no API call</span>
              : model ? <span className="badge">{model}</span> : null}
            {m.meta.latency_ms != null && <span>{(m.meta.latency_ms / 1000).toFixed(m.meta.latency_ms < 1000 ? 2 : 1)}s</span>}
            {m.meta.source === "voice" && <span>· voice</span>}
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
  // Parked ones and the one the approvals hotkey brought up are in the dock's queue too, so they're left out here.
  const approvals = s.approvals.filter((a) => !a.parked && a.id !== s.approvalFocus
    && (!a.task_id || !s.tasks[a.task_id]?.conv_id || s.tasks[a.task_id]?.conv_id === s.activeId));

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

  const narrow = useMedia(NARROW);
  const panelOn = narrow ? s.rightPeek : s.rightOpen;
  const toggleRight = () => {
    if (narrow) return s.set({ rightPeek: !s.rightPeek });
    localStorage.setItem("orion.right", s.rightOpen ? "0" : "1");
    s.set({ rightOpen: !s.rightOpen });
  };

  const name = s.engine?.settings.user_name ?? "";
  const empty = s.messages.length === 0;
  const assistant = s.engine?.settings.assistant_name ?? "ORION";
  const approvalCards = approvals.map((a) => <ApprovalCard key={a.id} a={a} />);

  return (
    <div className="main">
      <div className="topbar">
        {conv ? (
          <input className="title-input" value={title} onChange={(e) => setTitle(e.target.value)}
            onBlur={() => title.trim() && title !== conv.title && api.patch(`/api/conversations/${conv.id}`, { title: title.trim() })}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} aria-label="Conversation title" size={Math.min(60, Math.max(12, title.length + 1))} />
        ) : !empty ? <h1>New conversation</h1> : null}
        <span className="grow" />
        <button className={`icon-btn ${panelOn ? "on" : ""}`} onClick={toggleRight} title="Voice, progress and system panel"
          aria-label="Toggle side panel" aria-pressed={panelOn}>
          {panelOn ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
        </button>
      </div>

      <div className="chat-scroll" ref={scroller}
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
        {empty ? (
          <div className="home">
            <div className="home-hello">
              <Logo size={72} state={busy ? "thinking" : s.voice.state} interactive label="Talk to ORION"
                onClick={() => api.post("/api/voice/toggle").catch(() => {})} />
              <h2>{greeting(name)}</h2>
            </div>
            <Composer ref={composer} busy={busy} />
            <div className="starters">
              {STARTERS.map((c) => (
                <button key={c.title} className="starter" onClick={() => composer.current?.fill(c.text)} title={c.desc}>
                  <c.icon size={15} /> {c.title}
                </button>
              ))}
            </div>
            <p className="sub">Type <kbd>/</kbd> for skills, drop in a file, or press the orb to talk.</p>
            {s.transcript && <div className="transcript">“{s.transcript.text}”</div>}
            {approvals.length > 0 && <div style={{ width: "100%", maxWidth: "var(--chat-w)", display: "flex", flexDirection: "column", gap: 12 }}>{approvalCards}</div>}
          </div>
        ) : (
          <div className="chat-inner">
            {s.messages.map((m) => <MessageView key={m.id} m={m} />)}
            {s.transcript && (
              <div className={`msg ghost ${s.transcript.role === "user" ? "user" : "assistant"}`} aria-label={s.transcript.role === "user" ? "You (speaking)" : assistant}>
                <div className="body">{s.transcript.text}{s.transcript.tentative ? " …" : ""}</div>
              </div>
            )}
            {approvalCards}
          </div>
        )}
      </div>
      {!empty && <Composer ref={composer} busy={busy} />}
    </div>
  );
}
