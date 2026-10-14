import {
  Blocks, Cable, FolderOpen, Plug, Plus, Puzzle, RefreshCw, Sparkles, Trash2, Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Markdown } from "../components/Markdown";
import { Modal, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { CatalogEntry, ConnectorInfo, PluginInfo, Skill, ToolInfo } from "../lib/types";

type Tab = "skills" | "connectors" | "plugins" | "tools";

export function EcosystemView() {
  const [tab, setTab] = useState<Tab>("skills");
  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Ecosystem</h2>
          <p>Extend ORION: skills teach it workflows, connectors plug into apps and services, plugins add new abilities.</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        {([["skills", Sparkles, "Skills"], ["connectors", Cable, "Connectors"], ["plugins", Puzzle, "Plugins"], ["tools", Wrench, "Tools"]] as const).map(([id, Icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>
      {tab === "skills" && <SkillsTab />}
      {tab === "connectors" && <ConnectorsTab />}
      {tab === "plugins" && <PluginsTab />}
      {tab === "tools" && <ToolsTab />}
    </div></div>
  );
}

// ── Skills ────────────────────────────────────────────────────────────────
function SkillsTab() {
  const bump = useStore((s) => s.bump["ecosystem.changed"]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [view, setView] = useState<Skill | null>(null);
  const [edit, setEdit] = useState<Partial<Skill> | null>(null);
  const load = () => api.get<Skill[]>("/api/skills").then(setSkills);
  useEffect(() => { load(); }, [bump]);

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="muted grow" style={{ fontSize: 13 }}>Use a skill by typing <kbd>/name</kbd>, or just ask — ORION picks the right one automatically.</div>
        <button className="btn" onClick={() => api.post("/api/open-folder/skills")}><FolderOpen size={14} /> Folder</button>
        <button className="btn primary" onClick={() => setEdit({ tier: "fast", body: "1. …\n2. …\n" })}><Plus size={14} /> New skill</button>
      </div>
      <div className="grid2">
        {skills.map((s) => (
          <div key={s.name} className="tile">
            <div className="head">
              <div className="ic"><Sparkles size={16} /></div>
              <div className="grow"><div className="nm">/{s.name}</div></div>
              <Toggle on={s.enabled} onChange={(v) => api.patch(`/api/skills/${s.name}`, { enabled: v }).then(load)} label="Enabled" />
            </div>
            <div className="ds">{s.description}</div>
            <div className="ft">
              <span className="badge">{s.builtin ? "built-in" : "yours"}</span>
              <span className={`badge ${s.tier === "smart" ? "accent" : ""}`}>{s.tier === "smart" ? "deep" : "fast"}</span>
              <span className="grow" />
              <button className="btn sm ghost" onClick={async () => setView(await api.get<Skill>(`/api/skills/${s.name}`))}>View</button>
              {!s.builtin && <button className="btn sm ghost" onClick={async () => setEdit(await api.get<Skill>(`/api/skills/${s.name}`))}>Edit</button>}
            </div>
          </div>
        ))}
      </div>
      {view && (
        <Modal title={`/${view.name}`} onClose={() => setView(null)} wide
          footer={<button className="btn" onClick={() => { setEdit({ ...view, name: view.builtin ? `${view.name}-custom` : view.name }); setView(null); }}>
            {view.builtin ? "Duplicate & customise" : "Edit"}</button>}>
          <div className="muted">{view.description}</div>
          <Markdown text={view.body ?? ""} />
        </Modal>
      )}
      {edit && <SkillEditor initial={edit} onClose={() => { setEdit(null); load(); }} />}
    </>
  );
}

function SkillEditor({ initial, onClose }: { initial: Partial<Skill>; onClose: () => void }) {
  const toast = useStore((s) => s.toast);
  const [name, setName] = useState(initial.name ?? "");
  const [desc, setDesc] = useState(initial.description ?? "");
  const [body, setBody] = useState(initial.body ?? "");
  const [tier, setTier] = useState(initial.tier ?? "fast");
  const [err, setErr] = useState("");
  const save = async () => {
    try {
      await api.post("/api/skills", { name, description: desc, body, tier });
      toast({ title: "Skill saved", body: `/${name}`, tone: "success" });
      onClose();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title={initial.name && !initial.builtin ? `Edit /${initial.name}` : "New skill"} onClose={onClose} wide
      footer={<>
        {!initial.builtin && initial.name && <button className="btn danger" onClick={() => api.del(`/api/skills/${initial.name}`).then(onClose)}><Trash2 size={13} /> Delete</button>}
        <span className="grow" />
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" onClick={save} disabled={!name || !desc}>Save skill</button>
      </>}>
      <div className="row">
        <div className="grow"><label className="lbl">Name (used as /command)</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} placeholder="weekly-report" /></div>
        <div style={{ width: 160 }}><label className="lbl">Model</label>
          <select className="select" value={tier} onChange={(e) => setTier(e.target.value)}><option value="fast">Fast</option><option value="smart">Deep</option></select></div>
      </div>
      <div><label className="lbl">When should ORION use it?</label>
        <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Compile my weekly status report from notes and send it to the clipboard." /></div>
      <div><label className="lbl">Instructions (Markdown)</label>
        <textarea className="textarea" style={{ minHeight: 260 }} value={body} onChange={(e) => setBody(e.target.value)} /></div>
      <div className="muted" style={{ fontSize: 12.5 }}>Tip: write it like a checklist for a smart assistant. Name the tools to use (web_search, read_file, write_file, set_timer…) and the format of the answer.</div>
      {err && <div className="err">{err}</div>}
    </Modal>
  );
}

// ── Connectors (MCP) ──────────────────────────────────────────────────────
function ConnectorsTab() {
  const bump = useStore((s) => s.bump["ecosystem.changed"]);
  const [data, setData] = useState<{ connectors: ConnectorInfo[]; catalog: CatalogEntry[] }>({ connectors: [], catalog: [] });
  const [adding, setAdding] = useState<CatalogEntry | null>(null);
  const load = () => api.get<typeof data>("/api/connectors").then(setData);
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [bump]);

  const stateDot = (c: ConnectorInfo) => c.state === "ready" ? "ok" : c.state === "error" ? "bad" : c.state === "starting" ? "warn pulse" : "";
  return (
    <>
      <div className="muted" style={{ fontSize: 13, marginBottom: 14, lineHeight: 1.55 }}>
        Connectors use the <b>Model Context Protocol</b> — the same open standard used by Claude and Cowork — so ORION can use
        thousands of existing integrations. Most need <a href="https://nodejs.org" target="_blank" rel="noreferrer">Node.js</a> or <a href="https://docs.astral.sh/uv/" target="_blank" rel="noreferrer">uv</a> installed.
      </div>
      {data.connectors.length > 0 && (
        <div className="list" style={{ marginBottom: 24 }}>
          {data.connectors.map((c) => (
            <div key={c.name} className="item">
              <span className={`dot ${stateDot(c)}`} />
              <div className="grow">
                <div className="t">{c.name}</div>
                <div className="d">{c.state === "ready" ? `${c.tools.length} tools` : c.state === "error" ? c.error : c.state}</div>
              </div>
              <Toggle on={c.enabled} onChange={(v) => api.put(`/api/connectors/${c.name}`, { ...c.config, env: Object.fromEntries((c.config.env_keys ?? []).map((k) => [k, ""])), enabled: v }).then(load)} label="Enabled" />
              <button className="icon-btn" title="Remove" onClick={() => api.del(`/api/connectors/${c.name}`).then(load)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
      <div className="side-section" style={{ padding: "0 0 10px" }}>Add a connector</div>
      <div className="grid2">
        {data.catalog.map((e) => (
          <div key={e.id} className="tile">
            <div className="head"><div className="ic"><Plug size={16} /></div><div className="nm">{e.name}</div></div>
            <div className="ds">{e.description}</div>
            <div className="ft">
              {e.requires && <span className="badge">{e.requires}</span>}
              <span className="grow" />
              <button className="btn sm" onClick={() => setAdding(e)}><Plus size={13} /> Add</button>
            </div>
          </div>
        ))}
      </div>
      {adding && <ConnectorEditor entry={adding} onClose={() => { setAdding(null); load(); }} />}
    </>
  );
}

function ConnectorEditor({ entry, onClose }: { entry: CatalogEntry; onClose: () => void }) {
  const [name, setName] = useState(entry.id === "custom" ? "" : entry.id);
  const [transport, setTransport] = useState(entry.transport);
  const [command, setCommand] = useState(entry.command ?? "");
  const [args, setArgs] = useState((entry.args ?? []).join("\n"));
  const [url, setUrl] = useState(entry.url ?? "");
  const [env, setEnv] = useState<Record<string, string>>(Object.fromEntries(entry.needs.map((k) => [k, ""])));
  const [extraKey, setExtraKey] = useState("");
  const [err, setErr] = useState("");
  const save = async () => {
    try {
      await api.put(`/api/connectors/${name}`, transport === "stdio"
        ? { transport, command, args: args.split("\n").map((a) => a.trim()).filter(Boolean), env, enabled: true }
        : { transport, url, env, enabled: true });
      onClose();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Modal title={`Add ${entry.name}`} onClose={onClose}
      footer={<><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save} disabled={!name}>Connect</button></>}>
      <div className="row">
        <div className="grow"><label className="lbl">Name</label><input className="input" value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"))} /></div>
        <div style={{ width: 150 }}><label className="lbl">Transport</label>
          <select className="select" value={transport} onChange={(e) => setTransport(e.target.value as "stdio" | "http")}><option value="stdio">Local command</option><option value="http">HTTP URL</option></select></div>
      </div>
      {transport === "stdio" ? (
        <>
          <div><label className="lbl">Command</label><input className="input" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx" /></div>
          <div><label className="lbl">Arguments (one per line)</label><textarea className="textarea" value={args} onChange={(e) => setArgs(e.target.value)} /></div>
        </>
      ) : <div><label className="lbl">Server URL</label><input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/mcp" /></div>}
      <div>
        <label className="lbl">Secrets & environment</label>
        {Object.keys(env).map((k) => (
          <div key={k} className="row" style={{ marginBottom: 6 }}>
            <code style={{ width: 230, fontSize: 12 }} className="ellipsis">{k}</code>
            <input className="input" type="password" value={env[k]} onChange={(e) => setEnv({ ...env, [k]: e.target.value })} placeholder="value" />
          </div>
        ))}
        <div className="row">
          <input className="input" value={extraKey} onChange={(e) => setExtraKey(e.target.value.toUpperCase())} placeholder="ADD_VARIABLE" />
          <button className="btn" onClick={() => { if (extraKey) { setEnv({ ...env, [extraKey]: "" }); setExtraKey(""); } }}>Add</button>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>Stored only on this PC in ORION's data folder.</div>
      </div>
      {err && <div className="err">{err}</div>}
    </Modal>
  );
}

// ── Plugins ───────────────────────────────────────────────────────────────
function PluginsTab() {
  const [data, setData] = useState<{ folder: string; plugins: PluginInfo[] }>({ folder: "", plugins: [] });
  const load = () => api.get<typeof data>("/api/plugins").then(setData);
  useEffect(() => { load(); }, []);
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="muted grow" style={{ fontSize: 13, lineHeight: 1.55 }}>
          Plugins are Python files with <code>@tool</code> functions. Drop them into the plugins folder, then reload.
          See <code>_example_plugin.py</code> for a template.
        </div>
        <button className="btn" onClick={() => api.post("/api/open-folder/plugins")}><FolderOpen size={14} /> Open folder</button>
        <button className="btn primary" onClick={() => api.post("/api/plugins/reload").then(load)}><RefreshCw size={14} /> Reload</button>
      </div>
      {data.plugins.length ? (
        <div className="list">
          {data.plugins.map((p) => (
            <div key={p.name} className="item" style={{ alignItems: "flex-start" }}>
              <span className={`dot ${p.ok ? "ok" : "bad"}`} style={{ marginTop: 6 }} />
              <div className="grow">
                <div className="t">{p.name}</div>
                <div className="d">{p.ok ? (p.tools.join(", ") || "no tools found") : <pre style={{ whiteSpace: "pre-wrap", margin: 0, color: "var(--bad)" }}>{p.error}</pre>}</div>
              </div>
            </div>
          ))}
        </div>
      ) : <div className="empty"><Blocks size={28} /><div>No plugins installed yet.</div><div style={{ fontSize: 12, marginTop: 4 }}>{data.folder}</div></div>}
    </>
  );
}

// ── Tools ─────────────────────────────────────────────────────────────────
function ToolsTab() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [q, setQ] = useState("");
  const load = () => api.get<ToolInfo[]>("/api/tools").then(setTools);
  useEffect(() => { load(); }, []);
  const groups = useMemo(() => {
    const f = q.toLowerCase();
    const g: Record<string, ToolInfo[]> = {};
    for (const t of tools) {
      if (f && !(t.name.includes(f) || t.description.toLowerCase().includes(f))) continue;
      const k = t.source.startsWith("mcp:") ? `Connector · ${t.source.slice(4)}` : t.source.startsWith("plugin:") ? `Plugin · ${t.source.slice(7)}` : t.category;
      (g[k] ??= []).push(t);
    }
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b));
  }, [tools, q]);
  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <input className="input" style={{ maxWidth: 320 }} placeholder={`Filter ${tools.length} tools`} value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="grow" />
        <span className="badge safe">safe</span><span className="badge confirm">asks first</span><span className="badge danger">high risk · always asks</span>
      </div>
      {groups.map(([group, list]) => (
        <div key={group} style={{ marginBottom: 18 }}>
          <div className="side-section" style={{ padding: "0 0 8px" }}>{group}</div>
          <div className="list">
            {list.map((t) => (
              <div key={t.name} className="item" style={{ padding: "10px 14px" }}>
                <div className="grow">
                  <div className="row" style={{ gap: 8 }}><code style={{ fontSize: 12.5 }}>{t.name}</code><span className={`badge ${t.risk}`}>{t.risk}</span></div>
                  <div className="d">{t.description}</div>
                </div>
                <Toggle on={t.enabled} onChange={(v) => api.patch(`/api/tools/${t.name}`, { enabled: v }).then(load)} label="Enabled" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
