import { Brain, Clock, Download, ExternalLink, FolderOpen, Plus, RefreshCw, Trash2, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { timeAgo } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Memory, VaultStatus } from "../lib/types";

const NOTE_ORDER = ["About me.md", "People/", "Projects/", "Plans & dates.md", "Facts.md"];

function noteLabel(note: string | null): string {
  return (note ?? "Other").replace(/\.md$/, "");
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], { weekday: "long", hour: "2-digit", minute: "2-digit" });
}

export function MemoryView() {
  const bump = useStore((s) => s.bump["memory.changed"]);
  const toast = useStore((s) => s.toast);
  const [items, setItems] = useState<Memory[]>([]);
  const [vault, setVault] = useState<VaultStatus | null>(null);
  const [text, setText] = useState("");
  const [permanent, setPermanent] = useState(true);
  const [tab, setTab] = useState<"permanent" | "short">("permanent");
  const [q, setQ] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const load = () => {
    api.get<Memory[]>("/api/memories").then(setItems);
    api.get<VaultStatus>("/api/memory/vault").then(setVault);
  };
  useEffect(() => { load(); }, [bump]);

  const add = async () => {
    if (!text.trim()) return;
    await api.post("/api/memories", { text, permanent });
    setText("");
    setTab(permanent ? "permanent" : "short");
    load();
  };
  const review = async () => {
    setReviewing(true);
    try {
      const r = await api.post<{ kept: string[]; carried: string[]; discarded: string[]; note?: string }>("/api/memory/review");
      toast({ title: "Memory review done", tone: "info",
        body: r.note ?? `Kept ${r.kept.length}, carried over ${r.carried.length}, deleted ${r.discarded.length}.` });
      load();
    } catch (e) {
      toast({ title: "Review didn't run", body: (e as Error).message, tone: "error" });
    } finally {
      setReviewing(false);
    }
  };
  const open = (obsidian: boolean, note?: string) => api.post("/api/memory/open", { obsidian, note });
  const installObsidian = async () => {
    const r = await api.post<{ ok: boolean; result: string }>("/api/tools/install_app/run", { args: { app: "Obsidian.Obsidian" }, timeout: 900 });
    toast({ title: r.ok ? "Obsidian installed" : "Install didn't finish", body: r.result, tone: r.ok ? "info" : "error" });
    load();
  };

  const shown = items.filter((m) => m.tier === tab && (!q || m.text.toLowerCase().includes(q.toLowerCase())));
  const groups = useMemo(() => {
    const g = new Map<string, Memory[]>();
    for (const m of shown) {
      const k = tab === "short" ? "This week" : noteLabel(m.note);
      g.set(k, [...(g.get(k) ?? []), m]);
    }
    const rank = (k: string) => {
      const i = NOTE_ORDER.findIndex((p) => (k + ".md").startsWith(p) || k.startsWith(p));
      return i < 0 ? 99 : i;
    };
    return [...g.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
  }, [shown, tab]);

  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Memory</h2>
          <p>ORION's second brain is an Obsidian vault of plain notes on this PC{vault ? <>: <code>{vault.path}</code></> : null}.
            Lasting things go into permanent notes. Passing details go to <b>This week</b>, which ORION reviews and clears every week.</p>
        </div>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          {vault?.obsidian
            ? <button className="btn" onClick={() => open(true)}><ExternalLink size={14} /> Open in Obsidian</button>
            : <button className="btn" onClick={installObsidian} title="Free app for browsing notes as a linked graph"><Download size={14} /> Get Obsidian</button>}
          <button className="btn" onClick={() => open(false)}><FolderOpen size={14} /> Folder</button>
        </div>
      </div>

      <div className="item" style={{ marginBottom: 14 }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()}
          placeholder={permanent ? "Teach ORION something lasting, e.g. “I prefer Celsius and 24-hour time”" : "Something for this week, e.g. “Parents visiting on Saturday”"} />
        <div className="segmented">
          <button className={permanent ? "on" : ""} onClick={() => setPermanent(true)}><Brain size={13} /> Permanent</button>
          <button className={!permanent ? "on" : ""} onClick={() => setPermanent(false)}><Clock size={13} /> This week</button>
        </div>
        <button className="btn primary" onClick={add} disabled={!text.trim()}><Plus size={14} /> Add</button>
      </div>

      <div className="tabs">
        <button className={`tab ${tab === "permanent" ? "on" : ""}`} onClick={() => setTab("permanent")}>
          <Brain size={14} /> Permanent <span className="muted">{vault?.permanent ?? ""}</span></button>
        <button className={`tab ${tab === "short" ? "on" : ""}`} onClick={() => setTab("short")}>
          <Clock size={14} /> This week <span className="muted">{vault?.short ?? ""}</span></button>
        <div className="grow" />
        {items.length > 8 && <input className="input" style={{ maxWidth: 220, marginBottom: 6 }} placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} />}
      </div>

      {tab === "short" && vault && (
        <div className="item" style={{ marginBottom: 14, padding: "10px 14px" }}>
          <RefreshCw size={15} className="muted" />
          <div className="grow d">Next review {when(vault.next_review)}{vault.last_review ? ` · last ${timeAgo(vault.last_review)}` : ""}.
            Worth keeping → moved to permanent notes; still coming up → carried over; the rest is deleted. Decisions are logged in <i>ORION/Review log</i>.</div>
          <button className="btn" onClick={review} disabled={reviewing || !vault.short}>{reviewing ? "Reviewing…" : "Review now"}</button>
        </div>
      )}

      {groups.length ? groups.map(([group, mems]) => (
        <div key={group} style={{ marginBottom: 16 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <div className="muted" style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: ".06em" }}>{group}</div>
            {vault?.obsidian && mems[0].note && (
              <button className="icon-btn" title="Open this note in Obsidian" onClick={() => open(true, mems[0].note ?? undefined)}><ExternalLink size={13} /></button>)}
          </div>
          <div className="list">
            {mems.map((m) => (
              <div key={m.id} className="item" style={{ padding: "9px 14px" }}>
                {m.kind === "profile" || m.kind === "preference" ? <UserRound size={15} className="muted" /> : <Brain size={15} className="muted" />}
                <div className="grow">
                  <div>{m.text}</div>
                  <div className="d">{m.kind} · {timeAgo(m.created)}</div>
                </div>
                <button className="icon-btn" title="Forget" onClick={() => api.del(`/api/memories/${m.id}`).then(load)}><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        </div>
      )) : <div className="empty"><Brain size={28} /><div>{tab === "short" ? "Nothing in short-term memory this week." : "Nothing remembered yet. Tell ORION about yourself and it will remember."}</div></div>}
    </div></div>
  );
}
