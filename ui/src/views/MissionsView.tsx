import { MessageSquare, Rocket, Square, Target } from "lucide-react";
import { useEffect, useState } from "react";
import { Markdown } from "../components/Markdown";
import { PlanList, StepList } from "../components/TaskCard";
import { timeAgo } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";

export interface Mission {
  id: string; goal: string; title: string; status: "queued" | "running" | "done" | "failed" | "stopped";
  conv_id: string; task_id: string | null; created: number; started: number | null; finished: number | null; summary: string | null;
}

const IDEAS = [
  "Research the best budget gaming laptops under €1000, compare the top 5 in a table with pros/cons and prices, and save a report.",
  "Go through my Downloads folder, organise everything into sensible sub-folders and tell me what you moved.",
  "Find the 5 most important tech news stories this week and write me a one-page briefing.",
  "Plan a 3-day trip to Barcelona on a student budget: where to stay, what to see each day, and rough costs.",
];

export function useMissions() {
  const bump = useStore((s) => s.bump["missions.changed"]);
  const [items, setItems] = useState<Mission[]>([]);
  useEffect(() => { api.get<Mission[]>("/api/missions").then(setItems).catch(() => {}); }, [bump]);
  return items;
}

export function MissionCard({ m, compact = false }: { m: Mission; compact?: boolean }) {
  const s = useStore();
  const task = m.task_id ? s.tasks[m.task_id] : undefined;
  const [open, setOpen] = useState(m.status === "running");
  const label = { queued: "Queued", running: "Working", done: "Done", failed: "Failed", stopped: "Stopped" }[m.status];
  return (
    <div className="item" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div className="row" style={{ cursor: "pointer" }} onClick={() => setOpen(!open)}>
        <Target size={16} className={m.status === "running" ? "" : "muted"} />
        <div className="grow">
          <div className="t ellipsis">{m.title}</div>
          <div className="d">{label} · {timeAgo(m.finished ?? m.started ?? m.created)}</div>
        </div>
        <span className={`badge ${m.status === "done" ? "safe" : m.status === "failed" ? "danger" : m.status === "running" ? "accent" : ""}`}>{label}</span>
      </div>
      {open && (
        <>
          {task && <><PlanList task={task} />{!compact && <StepList steps={task.steps.slice(-8)} />}</>}
          {m.summary && <div className="md-box"><Markdown text={m.summary} /></div>}
          <div className="row">
            <button className="btn sm" onClick={() => s.openConversation(m.conv_id)}><MessageSquare size={12} /> Open</button>
            {(m.status === "running" || m.status === "queued") && (
              <button className="btn sm danger" onClick={() => api.post(`/api/missions/${m.id}/cancel`)}><Square size={11} /> Stop</button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export function MissionsView() {
  const items = useMissions();
  const toast = useStore((s) => s.toast);
  const [goal, setGoal] = useState("");
  const start = async (g = goal) => {
    try {
      await api.post("/api/missions", { goal: g });
      setGoal("");
      toast({ title: "Mission started", body: "I'll let you know when it's done.", tone: "success" });
    } catch (e) { toast({ title: "Couldn't start", body: (e as Error).message, tone: "error" }); }
  };
  return (
    <div className="page"><div className="page-inner">
      <div className="page-head"><div className="grow">
        <h2>Missions</h2>
        <p>Hand ORION a bigger job and get on with your day — it plans, works through it on its own and notifies you (and your phone) when it's done.</p>
      </div></div>
      <div className="composer page-composer">
        <textarea value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Describe the mission and what you want delivered…"
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && goal.trim().length >= 8) start(); }} aria-label="Mission" />
        <div className="composer-bar">
          <span className="composer-note">Ctrl+Enter to start</span>
          <span className="grow" />
          <button className="btn primary" disabled={goal.trim().length < 8} onClick={() => start()}><Rocket size={14} /> Start</button>
        </div>
      </div>
      {items.length === 0 && (
        <div className="grid2" style={{ marginBottom: 20 }}>
          {IDEAS.map((i) => <button key={i} className="card" onClick={() => setGoal(i)}><span className="cd">{i}</span></button>)}
        </div>
      )}
      <div className="list">{items.map((m) => <MissionCard key={m.id} m={m} />)}</div>
    </div></div>
  );
}
