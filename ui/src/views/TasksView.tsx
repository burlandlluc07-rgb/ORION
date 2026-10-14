import { ListChecks, MessageSquare, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { PlanList, StepList, TaskStatus } from "../components/TaskCard";
import { timeAgo } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Task } from "../lib/types";

export function TasksView() {
  const s = useStore();
  const [history, setHistory] = useState<Task[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ active: Task[]; history: Task[] }>("/api/tasks").then((r) => setHistory(r.history.map((t) => ({ ...t, plan: t.plan ?? [] }))));
  }, [s.tasks]);

  const active = Object.values(s.tasks).filter((t) => t.status === "running" || t.status === "waiting");
  const past = history.filter((h) => !active.some((a) => a.id === h.id));

  const card = (t: Task) => (
    <div key={t.id} className="item" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div className="row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === t.id ? null : t.id)}>
        <div className="grow">
          <div className="t ellipsis">{t.title}</div>
          <div className="d">{t.steps.length} action{t.steps.length === 1 ? "" : "s"} · {timeAgo(t.updated)}</div>
        </div>
        <TaskStatus task={t} />
        {t.conv_id && (
          <button className="icon-btn" title="Open conversation" onClick={(e) => { e.stopPropagation(); s.openConversation(t.conv_id); }}>
            <MessageSquare size={15} />
          </button>
        )}
      </div>
      {(open === t.id || t.status === "running" || t.status === "waiting") && (
        <div><PlanList task={t} /><StepList steps={t.steps} /></div>
      )}
    </div>
  );

  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Tasks</h2>
          <p>Everything ORION has done on your PC — every step, every result, every approval.</p>
        </div>
        {active.length > 0 && <button className="btn danger" onClick={() => s.stop()}><Square size={13} /> Stop all</button>}
      </div>
      {active.length > 0 && <><div className="side-section" style={{ padding: "0 0 8px" }}>Running now</div><div className="list" style={{ marginBottom: 22 }}>{active.map(card)}</div></>}
      <div className="side-section" style={{ padding: "0 0 8px" }}>History</div>
      {past.length ? <div className="list">{past.map(card)}</div> : (
        <div className="empty"><ListChecks size={28} /><div>No tasks yet. Ask ORION to do something on your PC.</div></div>
      )}
    </div></div>
  );
}
