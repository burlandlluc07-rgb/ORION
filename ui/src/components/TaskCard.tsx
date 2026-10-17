import {
  AlertTriangle, Ban, CheckCircle2, ChevronDown, ChevronRight, Circle, CircleDot, Clock, Loader2, Monitor, ShieldAlert,
  Square, XCircle,
} from "lucide-react";
import { useState } from "react";
import type { Step, Task } from "../lib/types";

export function StepIcon({ status }: { status: Step["status"] }) {
  switch (status) {
    case "done": return <CheckCircle2 size={16} className="s-done" />;
    case "failed": return <XCircle size={16} className="s-failed" />;
    case "denied": return <Ban size={16} className="s-denied" />;
    case "waiting": return <ShieldAlert size={16} className="s-waiting" />;
    case "stopped": return <Square size={14} className="s-stopped" />;
    case "attention": return <AlertTriangle size={16} className="s-waiting" />;
    default: return <Loader2 size={16} className="s-running spin" />;
  }
}

function dur(s: Step) {
  const end = s.ended ?? Date.now() / 1000;
  const d = end - s.started;
  return d < 1 ? `${Math.round(d * 1000)}ms` : `${d.toFixed(1)}s`;
}

function Verified({ v }: { v: NonNullable<NonNullable<Step["lifecycle"]>["verification"]> }) {
  if (v.status === "passed") return <span className="badge safe" title={v.evidence.join("; ")}>verified</span>;
  if (v.status === "failed") return <span className="badge danger" title={v.reason}>check failed</span>;
  if (v.status === "unverified") return <span className="badge" title={v.reason}>not verified</span>;
  return null;
}

export function TaskStatus({ task }: { task: Task }) {
  const map = {
    running: ["badge accent", "Working"], waiting: ["badge confirm", "Needs approval"], done: ["badge safe", "Done"],
    failed: ["badge danger", "Failed"], stopped: ["badge", "Stopped"],
  } as const;
  const [cls, label] = map[task.status] ?? map.running;
  return <span className={cls}>{task.status === "running" && <Loader2 size={11} className="spin" />}{label}</span>;
}

export function PlanList({ task }: { task: Task }) {
  if (!task.plan.length) return null;
  return (
    <div className="plan">
      {task.plan.map((p, i) => (
        <div key={i} className={`plan-item ${p.status}`}>
          {p.status === "done" ? <CheckCircle2 size={15} className="s-done" /> : p.status === "active"
            ? <CircleDot size={15} className="s-running" /> : <Circle size={15} className="muted" />}
          <span>{p.text}</span>
        </div>
      ))}
    </div>
  );
}

export function StepList({ steps, showResults = true }: { steps: Step[]; showResults?: boolean }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <>
      {steps.map((s) => (
        <div key={s.id} className="step" onClick={() => setOpenId(openId === s.id ? null : s.id)} style={{ cursor: s.result ? "pointer" : "default" }}>
          <span className="icon"><StepIcon status={s.status} /></span>
          <div className="label">
            <div className="row" style={{ gap: 8 }}>
              <span className="grow ellipsis">{s.label}</span>
              {s.risk !== "safe" && <span className={`badge ${s.risk}`}>{s.risk === "danger" ? "high risk" : "approval"}</span>}
              {s.lifecycle && s.lifecycle.capabilities.desktop !== "none" && (
                <span className="badge confirm" title="Uses your screen, mouse or keyboard"><Monitor size={11} /> desktop</span>
              )}
              {s.lifecycle?.verification && <Verified v={s.lifecycle.verification} />}
              <span className="dur">{dur(s)}</span>
            </div>
            {showResults && openId === s.id && s.result && <div className="result">{s.result}</div>}
            {showResults && openId === s.id && s.lifecycle && (
              <div className="result muted">
                {s.lifecycle.history.map((h) => h.stage.replace("_", " ")).join(" → ")}
                {s.lifecycle.verification?.evidence.length ? `\nChecked: ${s.lifecycle.verification.evidence.join("; ")}` : ""}
                {s.lifecycle.verification?.reason ? `\nCheck: ${s.lifecycle.verification.reason}` : ""}
                {s.lifecycle.logs.length ? `\n${s.lifecycle.logs.join("\n")}` : ""}
              </div>
            )}
          </div>
        </div>
      ))}
    </>
  );
}

/** Inline card under an assistant message showing what ORION did. */
export function TaskCard({ task, defaultOpen }: { task: Task; defaultOpen?: boolean }) {
  const live = task.status === "running" || task.status === "waiting";
  const [open, setOpen] = useState(defaultOpen ?? live);
  if (!task.steps.length && !task.plan.length) return null;
  if (!live && !task.plan.length && task.steps.length === 1) {
    const st = task.steps[0];
    return (
      <div className="step-pill" title={st.result ?? undefined}>
        <StepIcon status={st.status} /> <span className="ellipsis">{st.label}</span> <span className="dur">{dur(st)}</span>
      </div>
    );
  }
  const done = task.steps.filter((s) => s.status === "done").length;
  return (
    <div className="task-card">
      <div className="task-head" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown size={15} className="muted" /> : <ChevronRight size={15} className="muted" />}
        <span className="title">{task.plan.length ? task.title : `${task.steps.length} action${task.steps.length === 1 ? "" : "s"}`}</span>
        <span className="muted" style={{ fontSize: 12 }}><Clock size={11} /> {done}/{task.steps.length}</span>
        <TaskStatus task={task} />
      </div>
      {(open || live) && (
        <div className="task-body">
          <PlanList task={task} />
          <StepList steps={task.steps} />
        </div>
      )}
    </div>
  );
}
