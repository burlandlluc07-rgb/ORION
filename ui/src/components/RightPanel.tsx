import { Activity, Gauge, ListChecks, Mic, MicOff, PanelRightClose, Square } from "lucide-react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Orb } from "./Orb";
import { PlanList, StepList, TaskStatus } from "./TaskCard";
import { Meter } from "./ui";

const VOICE_LABEL: Record<string, string> = {
  off: "Voice off", standby: "Standing by", listening: "Listening", thinking: "Thinking", working: "Working",
  speaking: "Speaking", connecting: "Connecting", error: "Voice error", unavailable: "No audio device",
};

export function RightPanel() {
  const s = useStore();
  if (!s.rightOpen || s.view !== "chat") return null;
  const tasks = Object.values(s.tasks)
    .filter((t) => t.conv_id === s.activeId || t.status === "running" || t.status === "waiting")
    .sort((a, b) => b.updated - a.updated);
  const current = tasks[0];
  const v = s.vitals;
  const live = ["listening", "speaking", "thinking", "working", "connecting"].includes(s.voice.state);
  const close = () => {
    localStorage.setItem("orion.right", "0");
    s.set({ rightOpen: false });
  };

  return (
    <aside className="right" aria-label="Side panel">
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

      <div className="panel-sec">
        <div className="panel-card">
          <div className="panel-title"><Activity size={14} /> System</div>
          {v ? (
            <>
              <Meter label="CPU" value={v.cpu} />
              <Meter label="Memory" value={v.ram} text={`${v.ram_used_gb} / ${v.ram_total_gb} GB`} />
              <Meter label="Disk" value={v.disk} text={`${v.disk_free_gb} GB free`} />
              {v.battery != null && <Meter label={`Battery${v.plugged ? " · charging" : ""}`} value={v.battery} />}
            </>
          ) : <div className="panel-note">Reading sensors…</div>}
        </div>
      </div>

      <div className="panel-sec">
        <div className="panel-card">
          <div className="panel-title"><Gauge size={14} /> Gemini free tier</div>
          {s.engine?.quota.map((q) => (
            <div key={q.model} title={`${q.rpm_used}/${q.rpm} requests this minute`}>
              <Meter label={q.model.replace("gemini-", "")} value={q.rpd_used} max={q.rpd} text={`${q.rpd_used} / ${q.rpd} today`} />
              {q.cooldown > 0 && <div className="panel-note" style={{ marginTop: -6, marginBottom: 10 }}>Cooling down {q.cooldown}s</div>}
            </div>
          ))}
          <div className="panel-note" style={{ marginTop: 10 }}>Simple commands run locally and don't count. Resets midnight Pacific.</div>
        </div>
      </div>
    </aside>
  );
}
