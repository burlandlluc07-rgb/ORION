import {
  AlertTriangle, Ban, CheckCircle2, CircleCheck, CircleDashed, History, Loader2, MessageSquare, Monitor, Search, ShieldAlert,
  Square, XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { ActivityEntry, ActivityOutcome, ActivityPage } from "../lib/types";
import { timeAgo } from "./ui";

const OUTCOME: Record<ActivityOutcome, [string, string]> = {
  verified: ["Worked, checked", "safe"],
  done: ["Done (read only)", "safe"],
  unverified: ["Done, not checked", ""],
  attention: ["Needs a look", "danger"],
  failed: ["Failed", "danger"],
  declined: ["Declined", ""],
  stopped: ["Stopped", ""],
  running: ["Running", "accent"],
  waiting: ["Waiting for you", "confirm"],
};

const FILTERS = [["", "All"], ["ok", "Worked"], ["problem", "Problems"], ["declined", "Declined"], ["live", "Running"]] as const;

function OutcomeIcon({ o }: { o: ActivityOutcome }) {
  switch (o) {
    case "verified": return <CircleCheck size={16} className="s-done" />;
    case "done": return <CheckCircle2 size={16} className="s-done" />;
    case "unverified": return <CircleDashed size={16} className="s-done" />;
    case "attention": return <AlertTriangle size={16} className="s-failed" />;
    case "failed": return <XCircle size={16} className="s-failed" />;
    case "declined": return <Ban size={16} className="s-stopped" />;
    case "stopped": return <Square size={14} className="s-stopped" />;
    case "waiting": return <ShieldAlert size={16} className="s-waiting" />;
    default: return <Loader2 size={16} className="s-running spin" />;
  }
}

function when(ts: number) {
  return new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function dur(e: ActivityEntry) {
  if (!e.ended) return "";
  const d = e.ended - e.at;
  return d < 1 ? `${Math.round(d * 1000)}ms` : d < 120 ? `${d.toFixed(1)}s` : `${Math.round(d / 60)}m`;
}

function Row({ e, open, onToggle, compact }: { e: ActivityEntry; open: boolean; onToggle: () => void; compact?: boolean }) {
  const s = useStore();
  const [label, tone] = OUTCOME[e.outcome] ?? OUTCOME.running;
  return (
    <div className={`act-row ${open ? "open" : ""}`}>
      <button className="act-main" onClick={onToggle} aria-expanded={open}>
        <span className="act-ico"><OutcomeIcon o={e.outcome} /></span>
        <span className="grow act-text">
          <span className="act-sum">{e.summary || e.tool}</span>
          <span className="act-meta">
            <span title={when(e.at)}>{timeAgo(e.at)}</span>
            <span>{e.route_label}</span>
            {e.approved_by && <span>{e.approved_by}</span>}
          </span>
          {compact && <span className={`badge ${tone}`}>{label}</span>}
        </span>
        {!compact && e.desktop !== "none" && <span className="badge confirm" title="Used your screen, mouse or keyboard"><Monitor size={11} /> desktop</span>}
        {!compact && <span className={`badge ${tone}`}>{label}</span>}
      </button>
      {open && (
        <div className="act-detail">
          <dl>
            <dt>When</dt><dd>{when(e.at)}{dur(e) && ` · took ${dur(e)}`}</dd>
            <dt>Action</dt><dd><code>{e.tool}</code>{e.risk !== "safe" && ` · ${e.risk === "danger" ? "high risk" : "needs approval"}`}</dd>
            <dt>Route</dt><dd>{e.route_label}{e.desktop === "input" ? " · used mouse and keyboard" : e.desktop === "visible" ? " · opened a window" : ""}</dd>
            <dt>Approved</dt><dd>{e.approved_by || "Not recorded"}</dd>
            <dt>Part of</dt><dd>{e.task}</dd>
            {e.error && <><dt>{e.outcome === "attention" ? "Check found" : "Error"}</dt><dd className="err">{e.error}</dd></>}
            <dt>Evidence</dt>
            <dd>{e.evidence.length ? <ul>{e.evidence.map((x, i) => <li key={i}>{x}</li>)}</ul>
              : <span className="muted">{e.outcome === "failed" ? "It didn't work, so there is nothing to check." : "No independent check was recorded."}</span>}</dd>
          </dl>
          {!compact && e.conv_id && (
            <button className="btn sm" onClick={() => s.openConversation(e.conv_id)}><MessageSquare size={13} /> Open conversation</button>
          )}
        </div>
      )}
    </div>
  );
}

/** "What ORION did": every action, newest first, with route, approval and evidence. Shared by desktop and phone. */
export function ActivityLog({ compact }: { compact?: boolean }) {
  const tasks = useStore((st) => st.tasks);
  const [outcome, setOutcome] = useState("");
  const [route, setRoute] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState<ActivityPage | null>(null);
  const [older, setOlder] = useState<ActivityEntry[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const seq = useRef(0);
  const olderMore = useRef(false);

  const query = (before?: number) => {
    const p = new URLSearchParams({ limit: compact ? "40" : "80" });
    if (outcome) p.set("outcome", outcome);
    if (route) p.set("route", route);
    if (q.trim()) p.set("q", q.trim());
    if (before) p.set("before", String(before));
    return api.get<ActivityPage>(`/api/activity?${p}`);
  };

  useEffect(() => {
    const n = ++seq.current;
    const t = setTimeout(() => {
      query().then((r) => { if (n === seq.current) { setPage(r); setOlder([]); setErr(""); } })
        .catch((e) => n === seq.current && setErr((e as Error).message));
    }, q ? 250 : 120);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outcome, route, q, tasks]);

  const entries = [...(page?.entries ?? []), ...older];
  const more = older.length ? olderMore.current : page?.more;
  const loadOlder = () => {
    const last = entries[entries.length - 1];
    if (!last) return;
    query(last.at).then((r) => { olderMore.current = r.more; setOlder((o) => [...o, ...r.entries]); }).catch(() => {});
  };

  return (
    <div className={`act ${compact ? "compact" : ""}`}>
      <div className="act-filters">
        <div className="segmented" role="group" aria-label="Show">
          {FILTERS.map(([id, label]) => (
            <button key={id} className={outcome === id ? "on" : ""} onClick={() => setOutcome(id)} aria-pressed={outcome === id}>
              {label}{id && page?.counts[id] ? <span className="muted">{page.counts[id]}</span> : null}
            </button>
          ))}
        </div>
        <div className="act-search">
          <Search size={14} />
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search actions" aria-label="Search actions" />
        </div>
        {(page?.routes.length ?? 0) > 1 && (
          <select className="select" value={route} onChange={(e) => setRoute(e.target.value)} aria-label="Route">
            <option value="">Every route</option>
            {page!.routes.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        )}
      </div>
      {err && <div className="err">{err}</div>}
      {page && entries.length === 0 ? (
        <div className="empty"><History size={28} /><div>
          {page.total ? "Nothing matches these filters." : "Nothing yet. Every action ORION takes will be listed here."}
        </div></div>
      ) : (
        <div className="act-list">
          {entries.map((e) => <Row key={e.id} e={e} compact={compact} open={open === e.id} onToggle={() => setOpen(open === e.id ? null : e.id)} />)}
        </div>
      )}
      {more && <button className="btn act-more" onClick={loadOlder}>Show older</button>}
    </div>
  );
}
