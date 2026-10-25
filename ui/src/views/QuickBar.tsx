import { CornerDownLeft, Maximize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Markdown } from "../components/Markdown";
import { Logo } from "../components/Logo";
import { TaskCard } from "../components/TaskCard";
import { api } from "../lib/api";
import { useStore } from "../lib/store";

/** Ask box plus the latest reply, task and approvals. Shared by the floating Quick Bar and the open notch. */
export function QuickPanel({ onHide, onOpenMain, logoSize = 34 }: { onHide: () => void; onOpenMain: () => void; logoSize?: number }) {
  const s = useStore();
  const [text, setText] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    (window as any).__orionQuickFocus = () => { input.current?.focus(); input.current?.select(); };
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onHide(); };
    window.addEventListener("keydown", onKey);
    const quickId = localStorage.getItem("orion.quickConv");
    if (quickId && quickId !== s.activeId) s.openConversation(quickId).catch(() => localStorage.removeItem("orion.quickConv"));
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (s.activeId) localStorage.setItem("orion.quickConv", s.activeId);
  }, [s.activeId]);

  const submit = async () => {
    const t = text.trim();
    if (!t) return;
    setText("");
    await s.send(t);
  };

  const lastUser = [...s.messages].reverse().find((m) => m.role === "user");
  const last = [...s.messages].reverse().find((m) => m.role === "assistant");
  const show = last && lastUser && last.ts >= lastUser.ts ? last : undefined;
  const task = show?.meta.task_id ? s.tasks[show.meta.task_id] : undefined;

  return (
    <>
      <div className="quick-input">
        <Logo size={logoSize} state={show?.streaming ? "thinking" : s.voice.state} />
        <input ref={input} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Ask ORION or tell it what to do…" aria-label="Quick command" />
        <CornerDownLeft size={16} className="muted" />
      </div>
      <div className="quick-out">
        {s.approvals.map((a, i) => <ApprovalCard key={a.id} a={a} hotkey={i === s.approvals.length - 1} />)}
        {lastUser && <div className="muted" style={{ fontSize: 12.5, marginBottom: 8 }}>› {lastUser.content}</div>}
        {task && <TaskCard task={task} />}
        {show ? <Markdown text={show.content || "…"} /> : lastUser ? <div className="typing"><i /><i /><i /></div>
          : <div className="muted">Try: “open Discord”, “timer 10 minutes”, “summarise my clipboard”, “what's on my screen?”</div>}
      </div>
      <div className="quick-foot">
        <span><kbd>Enter</kbd> run</span><span><kbd>Esc</kbd> hide</span>
        <span className="grow" />
        <button className="btn sm ghost" onClick={() => { s.openConversation(null); localStorage.removeItem("orion.quickConv"); }}>New</button>
        <button className="btn sm ghost" onClick={onOpenMain}><Maximize2 size={12} /> Open ORION</button>
      </div>
    </>
  );
}

/** Spotlight-style command window summoned with Alt+Space over any app (used when the notch is turned off). */
export function QuickBar() {
  return (
    <div className="quick">
      <QuickPanel onHide={() => api.post("/api/window/hide-quick")} onOpenMain={() => api.post("/api/window/open-main-from-quick")} />
    </div>
  );
}
