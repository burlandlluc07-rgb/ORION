import { useEffect } from "react";
import { ListChecks, Mic, MicOff, PanelRightClose, Square } from "lucide-react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { NARROW, useMedia } from "../lib/useMedia";
import { Orb } from "./Orb";
import { PlanList, StepList, TaskStatus } from "./TaskCard";

const VOICE_LABEL: Record<string, string> = {
  off: "Voice off", standby: "Standing by", listening: "Listening", thinking: "Thinking", working: "Working",
  speaking: "Speaking", connecting: "Connecting", error: "Voice error", unavailable: "No audio device",
};

export function RightPanel() {
  const s = useStore();
  const narrow = useMedia(NARROW);
  useEffect(() => {
    if (!narrow || !s.rightPeek) return;
    // Capture phase, so Esc closes the drawer without also stopping a running reply.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      useStore.getState().set({ rightPeek: false });
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [narrow, s.rightPeek]);
  if (s.view !== "chat" || !(narrow ? s.rightPeek : s.rightOpen)) return null;
  const tasks = Object.values(s.tasks)
    .filter((t) => t.conv_id === s.activeId || t.status === "running" || t.status === "waiting")
    .sort((a, b) => b.updated - a.updated);
  const current = tasks[0];
  const live = ["listening", "speaking", "thinking", "working", "connecting"].includes(s.voice.state);
  const close = () => {
    if (narrow) return s.set({ rightPeek: false });
    localStorage.setItem("orion.right", "0");
    s.set({ rightOpen: false });
  };

  return (
    <>
    {narrow && <div className="right-scrim" onClick={close} />}
    <aside className={`right ${narrow ? "drawer" : ""}`} aria-label="Side panel">
      <div className="right-head">
        <span className="t">Activity</span>
        <button className="icon-btn sm" onClick={close} title="Close panel" aria-label="Close panel"><PanelRightClose size={15} /></button>
      </div>

      <div className="panel-sec">
        <div className="panel-card voice-panel">
          <Orb size={112} state={s.voice.state} interactive onClick={() => api.post("/api/voice/toggle").catch(() => {})} />
          <div className="voice-state">{VOICE_LABEL[s.voice.state] ?? s.voice.state}</div>
          {s.voice.detail && <div className="panel-note" style={{ textAlign: "center" }}>{s.voice.detail}</div>}
          {s.transcript && <div className="transcript">“{s.transcript.text.slice(-160)}”</div>}
          <div className="row">
            <button className="btn sm" onClick={() => api.post("/api/voice/toggle")} disabled={s.voice.state === "unavailable"}>
              {live ? <><Square size={12} /> End</> : <><Mic size={13} /> Talk</>}
            </button>
            {live && (
              <button className="btn sm ghost" onClick={() => api.post("/api/voice/mute")}>
                {s.voice.muted ? <><MicOff size={13} /> Unmute</> : <><Mic size={13} /> Mute</>}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="panel-sec">
        <div className="panel-card">
          <div className="panel-title"><ListChecks size={14} /> Progress</div>
          {current ? (
            <>
              <div className="row" style={{ marginBottom: 8 }}>
                <span className="grow ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{current.title}</span>
                <TaskStatus task={current} />
              </div>
              <PlanList task={current} />
              <StepList steps={current.steps.slice(-8)} showResults={false} />
              {!current.steps.length && !current.plan.length && <div className="panel-note">No actions yet.</div>}
            </>
          ) : (
            <div className="panel-note">
              When ORION works on something, each step shows up here live, with approvals for anything risky.
            </div>
          )}
        </div>
      </div>

    </aside>
    </>
  );
}
