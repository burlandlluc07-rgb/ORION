import { CalendarRange, ChevronLeft, ChevronRight, CircleCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { ActivityRecap } from "../lib/types";
import { ActivityRow } from "./ActivityLog";

function weekName(r: ActivityRecap) {
  if (r.weeks_ago === 0) return "This week";
  if (r.weeks_ago === 1) return "Last week";
  return `Week of ${new Date(r.start * 1000).toLocaleDateString([], { day: "numeric", month: "short" })}`;
}

function range(r: ActivityRecap) {
  const f = (ts: number) => new Date(ts * 1000).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  return `${f(r.start)} to ${f(r.end - 1)}`;
}

function versus(r: ActivityRecap) {
  const d = r.total - r.previous_total;
  if (!r.previous_total && !r.total) return "";
  if (!r.previous_total) return "Nothing the week before";
  if (d === 0) return "Same as the week before";
  return `${Math.abs(d)} ${d > 0 ? "more" : "fewer"} than the week before`;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function Stat({ value, label, note, bad }: { value: number; label: string; note: string; bad?: boolean }) {
  return (
    <div className={`recap-stat ${bad ? "bad" : ""}`}>
      <div className="recap-num">{value}</div>
      <div className="recap-label">{label}</div>
      <div className="recap-note">{note}</div>
    </div>
  );
}

/** "What ORION did this week": counts, proof and failures from the activity log. Read-only, shared by desktop and phone. */
export function WeeklyRecap({ compact }: { compact?: boolean }) {
  const tasks = useStore((st) => st.tasks);
  const [weeksAgo, setWeeksAgo] = useState(0);
  const [r, setR] = useState<ActivityRecap | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const seq = useRef(0);

  useEffect(() => {
    const n = ++seq.current;
    const t = setTimeout(() => {
      api.get<ActivityRecap>(`/api/activity/recap?weeks_ago=${weeksAgo}`)
        .then((x) => { if (n === seq.current) { setR(x); setErr(""); } })
        .catch((e) => n === seq.current && setErr((e as Error).message));
    }, 150);
    return () => clearTimeout(t);
  }, [weeksAgo, tasks]);

  if (!r) return err ? <div className="err">{err}</div> : null;
  const a = r.approvals;
  const peak = Math.max(1, ...r.days.map((d) => d.count));
  const topPeak = Math.max(1, ...r.top.map((t) => t.count));

  return (
    <section className={`recap ${compact ? "compact" : ""}`} aria-label="Weekly recap">
      <div className="recap-head">
        <CalendarRange size={16} />
        <div className="grow">
          <div className="recap-title">{weekName(r)}</div>
          <div className="recap-range">{range(r)}{versus(r) && <span className="recap-vs">{versus(r)}</span>}</div>
        </div>
        <button className="icon-btn" onClick={() => setWeeksAgo((w) => w + 1)} disabled={!r.has_older || weeksAgo >= 52}
          aria-label="Week before" title="Week before"><ChevronLeft size={16} /></button>
        <button className="icon-btn" onClick={() => setWeeksAgo((w) => Math.max(0, w - 1))} disabled={weeksAgo === 0}
          aria-label="Week after" title="Week after"><ChevronRight size={16} /></button>
      </div>
      {err && <div className="err">{err}</div>}

      {r.total === 0 ? (
        <div className="recap-empty">{weeksAgo === 0 ? "Nothing yet this week. Every action ORION takes will be counted here." : "ORION did nothing that week."}</div>
      ) : (
        <>
          <div className="recap-stats">
            <Stat value={r.total} label="Actions" note={`in ${plural(r.tasks, "task")}`} />
            <Stat value={r.worked} label="Worked" note={`${r.checked} checked with proof`} />
            <Stat value={r.problems} label="Need a look" note={r.problems ? "failed or flagged" : "nothing failed"} bad={r.problems > 0} />
            <Stat value={a.you_approved + a.you_declined} label="Your decisions"
              note={`${a.you_approved} approved · ${a.you_declined} declined`} />
          </div>

          <div className="recap-days" role="img" aria-label={r.days.map((d) => `${d.date}: ${d.count}`).join(", ")}>
            {r.days.map((d) => (
              <div key={d.date} className={`recap-day ${d.future ? "future" : ""}`} title={`${plural(d.count, "action")}${d.problems ? `, ${d.problems} with problems` : ""}`}>
                <div className="recap-bar">
                  <i style={{ height: `${(d.count / peak) * 100}%` }}>
                    {d.problems > 0 && <b style={{ height: `${(d.problems / d.count) * 100}%` }} />}
                  </i>
                </div>
                <span className="recap-dn">{new Date(`${d.date}T12:00`).toLocaleDateString([], { weekday: "narrow" })}</span>
                <span className="recap-dc">{d.future ? "" : d.count}</span>
              </div>
            ))}
          </div>

          <div className="recap-cols">
            <div>
              <div className="recap-sub">Did most</div>
              <ul className="recap-top">
                {r.top.slice(0, compact ? 4 : 6).map((t) => (
                  <li key={t.tool}>
                    <span className="grow ellipsis">{t.label}</span>
                    {t.problems > 0 && <span className="recap-fail">{t.problems} failed</span>}
                    <span className="recap-count">{t.count}</span>
                    <i style={{ width: `${(t.count / topPeak) * 100}%` }} />
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <div className="recap-sub">How it ran</div>
              <ul className="recap-top plain">
                {r.routes.slice(0, compact ? 3 : 5).map((x) => (
                  <li key={x.id}><span className="grow ellipsis">{x.label}</span><span className="recap-count">{x.count}</span></li>
                ))}
                <li><span className="grow">Used your screen</span><span className="recap-count">{r.desktop}</span></li>
                <li><span className="grow">Allowed by your settings</span><span className="recap-count">{a.automatic}</span></li>
              </ul>
            </div>
          </div>

          {r.proof.length > 0 && (
            <div className="recap-block">
              <div className="recap-sub">Proof it worked</div>
              <ul className="recap-proof">
                {r.proof.map((e) => (
                  <li key={e.id}><CircleCheck size={14} />
                    <span className="grow"><span className="t">{e.summary || e.tool}</span><span className="muted">{e.evidence[0]}</span></span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="recap-block">
            <div className="recap-sub">Needs a look{r.problems > r.problem_entries.length && <span className="muted"> · newest {r.problem_entries.length} of {r.problems}</span>}</div>
            {r.problem_entries.length === 0 ? <div className="recap-ok">Nothing failed this week.</div> : (
              <div className="act-list">
                {r.problem_entries.map((e) => (
                  <ActivityRow key={e.id} e={e} compact={compact} open={open === e.id} onToggle={() => setOpen(open === e.id ? null : e.id)} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
