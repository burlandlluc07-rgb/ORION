import { Bell, CalendarClock, Pencil, Play, Plus, Repeat, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Modal, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Routine } from "../lib/types";

type Freq = "daily" | "weekdays" | "weekly" | "every" | "once";
const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const TEMPLATES = [
  { name: "Morning briefing", prompt: "/morning-briefing", freq: "weekdays" as Freq, time: "08:00" },
  { name: "Evening wrap-up", prompt: "Summarise what we did today from our conversations and list anything left open.", freq: "daily" as Freq, time: "18:30" },
  { name: "Weekly Downloads clean-up", prompt: "/file-organizer Organise my Downloads folder. Just do it without asking.", freq: "weekly" as Freq, time: "10:00", day: "sun" },
  { name: "PC health check", prompt: "/pc-doctor Only report problems; skip fixes.", freq: "weekly" as Freq, time: "12:00", day: "fri" },
];

function buildSpec(freq: Freq, time: string, day: string, every: string, once: string) {
  switch (freq) {
    case "daily": return `daily ${time}`;
    case "weekdays": return `weekdays ${time}`;
    case "weekly": return `weekly ${day} ${time}`;
    case "every": return `every ${every}`;
    case "once": return `once ${once}`;
  }
}

function parseSpec(spec: string) {
  const p = spec.split(" ");
  const base = { freq: "daily" as Freq, time: "08:00", day: "mon", every: "1h", once: "" };
  if (p[0] === "weekly") return { ...base, freq: "weekly" as Freq, day: p[1], time: p[2] };
  if (p[0] === "every") return { ...base, freq: "every" as Freq, every: p.slice(1).join("") };
  if (p[0] === "once") return { ...base, freq: "once" as Freq, once: p[1]?.slice(0, 16) ?? "" };
  if (p[0] === "daily" || p[0] === "weekdays") return { ...base, freq: p[0] as Freq, time: p[1] };
  return base;
}

function Editor({ initial, onClose }: { initial?: Partial<Routine> & { spec?: ReturnType<typeof parseSpec> }; onClose: () => void }) {
  const toast = useStore((s) => s.toast);
  const sp = initial?.spec ?? parseSpec(initial?.schedule ?? "daily 08:00");
  const [name, setName] = useState(initial?.name ?? "");
  const [prompt, setPrompt] = useState(initial?.prompt ?? "");
  const [freq, setFreq] = useState<Freq>(sp.freq);
  const [time, setTime] = useState(sp.time);
  const [day, setDay] = useState(sp.day);
  const [every, setEvery] = useState(sp.every);
  const [once, setOnce] = useState(sp.once);
  const [err, setErr] = useState("");

  const save = async () => {
    const schedule = buildSpec(freq, time, day, every, once);
    try {
      if (initial?.id) await api.patch(`/api/routines/${initial.id}`, { name, prompt, schedule });
      else await api.post("/api/routines", { name, prompt, schedule });
      toast({ title: "Routine saved", body: name, tone: "success" });
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <Modal title={initial?.id ? "Edit routine" : "New routine"} onClose={onClose}
      footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!name.trim() || !prompt.trim()} onClick={save}>Save routine</button></>}>
      <div><label className="lbl">Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning briefing" autoFocus /></div>
      <div>
        <label className="lbl">What should ORION do?</label>
        <textarea className="textarea" style={{ fontFamily: "var(--font)", fontSize: 14 }} value={prompt} onChange={(e) => setPrompt(e.target.value)}
          placeholder="Exactly as you'd type it — you can use /skills too." />
      </div>
      <div>
        <label className="lbl">When</label>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <select className="select" style={{ width: 170 }} value={freq} onChange={(e) => setFreq(e.target.value as Freq)}>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Once a week</option>
            <option value="every">Repeat every…</option>
            <option value="once">Once</option>
          </select>
          {freq === "weekly" && (
            <select className="select" style={{ width: 110 }} value={day} onChange={(e) => setDay(e.target.value)}>
              {DAYS.map((d) => <option key={d} value={d}>{d[0].toUpperCase() + d.slice(1)}</option>)}
            </select>
          )}
          {["daily", "weekdays", "weekly"].includes(freq) && <input type="time" className="input" style={{ width: 130 }} value={time} onChange={(e) => setTime(e.target.value)} />}
          {freq === "every" && (
            <select className="select" style={{ width: 150 }} value={every} onChange={(e) => setEvery(e.target.value)}>
              {["15m", "30m", "1h", "2h", "4h", "8h"].map((x) => <option key={x} value={x}>{x.replace("m", " minutes").replace("h", " hours")}</option>)}
            </select>
          )}
          {freq === "once" && <input type="datetime-local" className="input" style={{ width: 230 }} value={once} onChange={(e) => setOnce(e.target.value)} />}
        </div>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="muted" style={{ fontSize: 12.5 }}>Routines run while ORION is open (it can live in the tray). Missed runs from the last 6 hours catch up on launch.</div>
    </Modal>
  );
}

export function RoutinesView() {
  const bump = useStore((s) => s.bump["routines.changed"]);
  const [items, setItems] = useState<Routine[]>([]);
  const [editing, setEditing] = useState<(Partial<Routine> & { spec?: ReturnType<typeof parseSpec> }) | null>(null);
  const load = () => api.get<Routine[]>("/api/routines").then(setItems);
  useEffect(() => { load(); }, [bump]);

  const routines = items.filter((r) => r.kind === "routine");
  const reminders = items.filter((r) => r.kind === "reminder");

  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Routines</h2>
          <p>Scheduled jobs ORION runs on its own — plus your timers and reminders.</p>
        </div>
        <button className="btn primary" onClick={() => setEditing({})}><Plus size={15} /> New routine</button>
      </div>

      {routines.length === 0 ? (
        <>
          <div className="side-section" style={{ padding: "0 0 10px" }}>Start from a template</div>
          <div className="grid2" style={{ marginBottom: 26 }}>
            {TEMPLATES.map((t) => (
              <div key={t.name} className="tile">
                <div className="head"><div className="ic"><Repeat size={17} /></div><div className="nm">{t.name}</div></div>
                <div className="ds">{t.prompt}</div>
                <div className="ft">
                  <span className="badge">{t.freq === "weekly" ? `${t.day} ${t.time}` : `${t.freq} ${t.time}`}</span>
                  <span className="grow" />
                  <button className="btn sm" onClick={() => setEditing({ name: t.name, prompt: t.prompt,
                    spec: { freq: t.freq, time: t.time, day: t.day ?? "mon", every: "1h", once: "" } })}>Use</button>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="list" style={{ marginBottom: 26 }}>
          {routines.map((r) => (
            <div key={r.id} className="item">
              <div className="tile ic" style={{ padding: 0, border: 0, background: "none" }}><CalendarClock size={18} style={{ color: "var(--accent)" }} /></div>
              <div className="grow">
                <div className="t">{r.name}</div>
                <div className="d ellipsis">{r.schedule_text} · {r.next_run ? `next ${new Date(r.next_run * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}` : "not scheduled"}</div>
                {r.last_result && <div className="d ellipsis" title={r.last_result}>Last: {r.last_result}</div>}
              </div>
              <button className="icon-btn" title="Run now" onClick={() => api.post(`/api/routines/${r.id}/run`)}><Play size={15} /></button>
              <button className="icon-btn" title="Edit" onClick={() => setEditing(r)}><Pencil size={15} /></button>
              <button className="icon-btn" title="Delete" onClick={() => api.del(`/api/routines/${r.id}`).then(load)}><Trash2 size={15} /></button>
              <Toggle on={r.enabled} onChange={(v) => api.patch(`/api/routines/${r.id}`, { enabled: v }).then(load)} label="Enabled" />
            </div>
          ))}
        </div>
      )}

      <div className="side-section" style={{ padding: "0 0 10px" }}>Timers & reminders</div>
      {reminders.length ? (
        <div className="list">
          {reminders.map((r) => (
            <div key={r.id} className="item">
              <Bell size={17} style={{ color: "var(--warn)" }} />
              <div className="grow">
                <div className="t">{r.prompt}</div>
                <div className="d">{r.next_run ? new Date(r.next_run * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}</div>
              </div>
              <button className="icon-btn" title="Cancel" onClick={() => api.del(`/api/routines/${r.id}`).then(load)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      ) : <div className="empty">Say “remind me to call mum at 6pm” or “timer 10 minutes”.</div>}

      {editing && <Editor initial={editing} onClose={() => { setEditing(null); load(); }} />}
    </div></div>
  );
}
