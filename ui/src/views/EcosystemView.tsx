import {
  Blocks, Cable, CalendarDays, FolderOpen, Lightbulb, Loader2, Mail, MessageCircle, Plug, Plus, Puzzle, RefreshCw, Sparkles, Trash2, UserRound, Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Markdown } from "../components/Markdown";
import { Modal, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { CatalogEntry, ConnectorInfo, PluginInfo, Skill, ToolInfo } from "../lib/types";

type Tab = "skills" | "accounts" | "connectors" | "plugins" | "tools";

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
        {([["skills", Sparkles, "Skills"], ["accounts", UserRound, "Accounts"], ["connectors", Cable, "Connectors"], ["plugins", Puzzle, "Plugins"], ["tools", Wrench, "Tools"]] as const).map(([id, Icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>
      {tab === "skills" && <SkillsTab />}
      {tab === "accounts" && <AccountsTab />}
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

// ── Accounts (email + calendar) ───────────────────────────────────────────
interface AccountsInfo {
  email: { preset: string; address: string; name: string; imap_host: string; imap_port: number; smtp_host: string; smtp_port: number } | null;
  calendars: { name: string; host: string }[];
  add_events_via: "google" | "outlook" | "ics";
  presets: Record<string, { label: string; help: string }>;
}

function AccountsTab() {
  const toast = useStore((s) => s.toast);
  const [info, setInfo] = useState<AccountsInfo | null>(null);
  const [preset, setPreset] = useState("gmail");
  const [address, setAddress] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [imap, setImap] = useState("");
  const [smtp, setSmtp] = useState("");
  const [cals, setCals] = useState<{ name: string; url: string }[]>([]);
  const [via, setVia] = useState<"google" | "outlook" | "ics">("google");
  const [busy, setBusy] = useState<"" | "email" | "cal">("");
  const [err, setErr] = useState<{ email?: string; cal?: string }>({});

  const load = () => api.get<AccountsInfo>("/api/accounts").then((a) => {
    setInfo(a);
    setVia(a.add_events_via);
    if (a.email) { setPreset(a.email.preset); setAddress(a.email.address); setName(a.email.name); }
  });
  useEffect(() => { load(); }, []);
  if (!info) return null;

  const saveEmail = async () => {
    setBusy("email"); setErr({});
    try {
      const body: Record<string, unknown> = { preset, address, name, password };
      if (preset === "custom") { const [ih, ip] = imap.split(":"); const [sh, sp] = smtp.split(":"); Object.assign(body, { imap_host: ih, imap_port: Number(ip) || 993, smtp_host: sh, smtp_port: Number(sp) || 587 }); }
      setInfo(await api.put<AccountsInfo>("/api/accounts/email", body));
      setPassword("");
      toast({ title: "Email connected", body: address, tone: "success" });
    } catch (e) { setErr({ email: (e as Error).message }); } finally { setBusy(""); }
  };
  const saveCals = async () => {
    setBusy("cal"); setErr({});
    try {
      setInfo(await api.put<AccountsInfo>("/api/accounts/calendars", { add: cals.filter((c) => c.url.trim()), add_events_via: via }));
      setCals([]);
      toast({ title: "Calendar settings saved", tone: "success" });
    } catch (e) { setErr({ cal: (e as Error).message }); } finally { setBusy(""); }
  };

  return (
    <>
      <div className="muted" style={{ fontSize: 13, marginBottom: 14, lineHeight: 1.55 }}>
        Connect your mail and calendar so ORION can brief you, find emails, draft and send replies, and add events.
        Everything stays on this PC; passwords go into Windows Credential Manager.
      </div>

      <section className="settings-sec">
        <h3><Mail size={16} /> Email</h3>
        {info.email ? (
          <div className="field">
            <div><div className="fl">{info.email.address}</div><div className="fh">{info.presets[info.email.preset]?.label} · {info.email.imap_host} · read, search and send (sending always asks first)</div></div>
            <div className="fc"><button className="btn danger" onClick={() => api.del<AccountsInfo>("/api/accounts/email").then(setInfo)}><Trash2 size={13} /> Disconnect</button></div>
          </div>
        ) : (
          <div className="modal-body">
            <div className="row" style={{ flexWrap: "wrap" }}>
              <div style={{ width: 200 }}><label className="lbl">Provider</label>
                <select className="select" value={preset} onChange={(e) => setPreset(e.target.value)}>
                  {Object.entries(info.presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                </select></div>
              <div className="grow"><label className="lbl">Email address</label><input className="input" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="you@gmail.com" /></div>
            </div>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <div className="grow"><label className="lbl">App password</label><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></div>
              <div className="grow"><label className="lbl">Your name (for “From”)</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></div>
            </div>
            {preset === "custom" && (
              <div className="row">
                <div className="grow"><label className="lbl">IMAP server:port</label><input className="input" value={imap} onChange={(e) => setImap(e.target.value)} placeholder="imap.example.com:993" /></div>
                <div className="grow"><label className="lbl">SMTP server:port</label><input className="input" value={smtp} onChange={(e) => setSmtp(e.target.value)} placeholder="smtp.example.com:587" /></div>
              </div>
            )}
            <div className="fh muted" style={{ fontSize: 12.5 }}>{info.presets[preset]?.help} Your normal password won't work — it must be an app password.</div>
            {err.email && <div className="err">{err.email}</div>}
            <div><button className="btn primary" disabled={!address || !password || busy === "email"} onClick={saveEmail}>
              {busy === "email" ? <><Loader2 size={14} className="spin" /> Testing sign-in…</> : "Connect email"}</button></div>
          </div>
        )}
      </section>

      <section className="settings-sec">
        <h3><CalendarDays size={16} /> Calendar</h3>
        {info.calendars.map((c) => (
          <div key={c.name + c.host} className="field">
            <div><div className="fl">{c.name}</div><div className="fh">{c.host} · read-only iCal feed</div></div>
            <div className="fc" />
          </div>
        ))}
        {info.calendars.length > 0 && (
          <div style={{ padding: "8px 18px 0" }}>
            <button className="btn sm ghost" onClick={() => api.put<AccountsInfo>("/api/accounts/calendars", { calendars: [], add_events_via: via }).then(setInfo)}>
              <Trash2 size={12} /> Disconnect all calendars</button>
          </div>
        )}
        <div className="modal-body">
          <div className="fh muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
            Paste each calendar's <b>private iCal address</b>. Google Calendar: Settings → your calendar → “Secret address in iCal format”.
            Outlook: Settings → Calendar → Shared calendars → Publish a calendar → ICS link.
          </div>
          {cals.map((c, i) => (
            <div key={i} className="row">
              <input className="input" style={{ width: 150 }} value={c.name} placeholder="Name" onChange={(e) => setCals(cals.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
              <input className="input" value={c.url} placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" onChange={(e) => setCals(cals.map((x, j) => j === i ? { ...x, url: e.target.value } : x))} />
            </div>
          ))}
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button className="btn" onClick={() => setCals([...cals, { name: cals.length || info.calendars.length ? "" : "Personal", url: "" }])}><Plus size={13} /> Add calendar link</button>
            <span className="grow" />
            <span className="muted" style={{ fontSize: 12.5 }}>New events open in</span>
            <select className="select" style={{ width: 190 }} value={via} onChange={(e) => setVia(e.target.value as typeof via)}>
              <option value="google">Google Calendar (web)</option><option value="outlook">Outlook (web)</option><option value="ics">Default calendar app (.ics)</option>
            </select>
            <button className="btn primary" disabled={busy === "cal"} onClick={saveCals}>
              {busy === "cal" ? <><Loader2 size={14} className="spin" /> Checking…</> : "Save"}</button>
          </div>
          {err.cal && <div className="err">{err.cal}</div>}
        </div>
      </section>

      <WhatsAppSection />
      <SmartHomeSection />
    </>
  );
}

// ── WhatsApp (Cloud API) ──────────────────────────────────────────────────
interface WhatsAppInfo {
  configured: boolean; dry_run: boolean; phone_number_id: string; business_account_id: string;
  verified_name: string; display_number: string; has_token: boolean; contacts: { name: string; number: string }[];
}

function WhatsAppSection() {
  const toast = useStore((s) => s.toast);
  const [info, setInfo] = useState<WhatsAppInfo | null>(null);
  const [pid, setPid] = useState("");
  const [waba, setWaba] = useState("");
  const [token, setToken] = useState("");
  const [cName, setCName] = useState("");
  const [cNum, setCNum] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<WhatsAppInfo>("/api/accounts/whatsapp").then(setInfo); }, []);
  if (!info) return null;

  const save = async (dry_run: boolean) => {
    setBusy(true); setErr("");
    try {
      setInfo(await api.put<WhatsAppInfo>("/api/accounts/whatsapp", dry_run ? { dry_run } : { phone_number_id: pid, business_account_id: waba, access_token: token }));
      setToken("");
      toast({ title: dry_run ? "WhatsApp dry run on" : "WhatsApp connected", tone: "success" });
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const addContact = async () => {
    setErr("");
    try { setInfo(await api.put<WhatsAppInfo>("/api/accounts/whatsapp/contacts", { name: cName, number: cNum })); setCName(""); setCNum(""); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <section className="settings-sec">
      <h3><MessageCircle size={16} /> WhatsApp</h3>
      {info.configured && (
        <div className="field">
          <div><div className="fl">{info.dry_run ? "Dry run (nothing is sent)" : `${info.verified_name || "WhatsApp Business"} ${info.display_number}`}</div>
            <div className="fh">Official Cloud API, runs in the background · every message shows you the recipient and exact text before it is sent</div></div>
          <div className="fc"><button className="btn danger" onClick={() => api.del<WhatsAppInfo>("/api/accounts/whatsapp").then(setInfo)}><Trash2 size={13} /> Disconnect</button></div>
        </div>
      )}
      {(!info.configured || info.dry_run) && (
        <div className="modal-body">
          <div className="fh muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
            <b>Your own WhatsApp</b> works without any setup: add contacts below, then ask ORION to WhatsApp someone. It asks you
            yes or no with the exact message, then opens WhatsApp Desktop and presses Send (or just types it in for you to send).
          </div>
          <div className="fh muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
            <b>Optional, a business number in the background:</b> from Meta's developer site: your app → WhatsApp → API Setup. Messages come from that WhatsApp Business number, not your personal WhatsApp.
            Free text only reaches people who messaged that number in the last 24 hours; otherwise ORION sends an approved template.
          </div>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <div className="grow"><label className="lbl">Phone number ID</label><input className="input" value={pid} onChange={(e) => setPid(e.target.value)} /></div>
            <div className="grow"><label className="lbl">WhatsApp Business Account ID (for templates)</label><input className="input" value={waba} onChange={(e) => setWaba(e.target.value)} /></div>
          </div>
          <div><label className="lbl">Access token</label><input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)} /></div>
          <div className="row">
            <button className="btn primary" disabled={!pid || !token || busy} onClick={() => save(false)}>
              {busy ? <><Loader2 size={14} className="spin" /> Checking with Meta…</> : "Connect WhatsApp"}</button>
            {!info.configured && <button className="btn" disabled={busy} onClick={() => save(true)}>Try a dry run first</button>}
          </div>
        </div>
      )}
      {info.contacts.map((c) => (
        <div key={c.name} className="field">
          <div><div className="fl">{c.name}</div><div className="fh">{c.number}</div></div>
          <div className="fc"><button className="btn sm ghost" onClick={() => api.del<WhatsAppInfo>(`/api/accounts/whatsapp/contacts/${encodeURIComponent(c.name)}`).then(setInfo)}><Trash2 size={12} /></button></div>
        </div>
      ))}
      {(
        <div className="modal-body">
          <div className="row">
            <input className="input" style={{ width: 150 }} value={cName} placeholder="Name" onChange={(e) => setCName(e.target.value)} />
            <input className="input" value={cNum} placeholder="+34 612 345 678" onChange={(e) => setCNum(e.target.value)} />
            <button className="btn" disabled={!cName || !cNum} onClick={addContact}><Plus size={13} /> Add contact</button>
          </div>
        </div>
      )}
      {err && <div className="err" style={{ padding: "0 18px 12px" }}>{err}</div>}
    </section>
  );
}

// ── Smart home ────────────────────────────────────────────────────────────
interface HomeInfo { ha_url: string | null; ha_connected: boolean; hue_ip: string | null; hue_connected: boolean; devices: number }

function SmartHomeSection() {
  const toast = useStore((s) => s.toast);
  const [info, setInfo] = useState<HomeInfo | null>(null);
  const [url, setUrl] = useState("http://homeassistant.local:8123");
  const [token, setToken] = useState("");
  const [bridges, setBridges] = useState<string[] | null>(null);
  const [hueIp, setHueIp] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api.get<HomeInfo>("/api/home").then(setInfo).catch(() => {}); }, []);
  if (!info) return null;
  const run = async (key: string, fn: () => Promise<HomeInfo>, ok: string) => {
    setBusy(key); setErr("");
    try { setInfo(await fn()); toast({ title: ok, tone: "success" }); } catch (e) { setErr((e as Error).message); } finally { setBusy(""); }
  };
  return (
    <section className="settings-sec">
      <h3><Lightbulb size={16} /> Smart home</h3>
      <div className="modal-body">
        <div className="fh muted" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
          Say “turn off the lights”, “dim the bedroom to 30%”, “movie night”. Locks and alarms always ask first.
          {info.devices > 0 && <> Connected devices: <b>{info.devices}</b>.</>}
        </div>
        <div className="fl" style={{ fontWeight: 600 }}>Home Assistant <span className="muted" style={{ fontWeight: 400 }}>— works with thousands of brands (Tuya, IKEA, Shelly, Nest, Sonos…)</span></div>
        {info.ha_connected ? (
          <div className="row"><span className="dot ok" /><span className="grow">{info.ha_url}</span>
            <button className="btn sm ghost" onClick={() => run("ha", () => api.del<HomeInfo>("/api/home/ha"), "Disconnected")}><Trash2 size={12} /> Disconnect</button></div>
        ) : (
          <>
            <div className="row">
              <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://homeassistant.local:8123" />
              <input className="input" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Long-lived access token" />
            </div>
            <div className="fh muted" style={{ fontSize: 12 }}>In Home Assistant: your profile → Security → Long-lived access tokens → Create token.</div>
            <div><button className="btn primary" disabled={!token || busy === "ha"} onClick={() => run("ha", () => api.put<HomeInfo>("/api/home/ha", { url, token }), "Home Assistant connected")}>
              {busy === "ha" ? <><Loader2 size={13} className="spin" /> Connecting…</> : "Connect Home Assistant"}</button></div>
          </>
        )}
        <div className="fl" style={{ fontWeight: 600, marginTop: 6 }}>Philips Hue</div>
        {info.hue_connected ? (
          <div className="row"><span className="dot ok" /><span className="grow">Bridge {info.hue_ip}</span>
            <button className="btn sm ghost" onClick={() => run("hue", () => api.del<HomeInfo>("/api/home/hue"), "Disconnected")}><Trash2 size={12} /> Disconnect</button></div>
        ) : (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <button className="btn" onClick={async () => { setBusy("disc"); const r = await api.get<{ bridges: string[] }>("/api/home/hue/discover").catch(() => ({ bridges: [] })); setBridges(r.bridges); if (r.bridges[0]) setHueIp(r.bridges[0]); setBusy(""); }}>
                {busy === "disc" ? <Loader2 size={13} className="spin" /> : null} Find my bridge</button>
              <input className="input" style={{ maxWidth: 200 }} value={hueIp} onChange={(e) => setHueIp(e.target.value)} placeholder="Bridge IP" />
              <button className="btn primary" disabled={!hueIp || busy === "hue"} onClick={() => run("hue", () => api.post<HomeInfo>("/api/home/hue/pair", { ip: hueIp }), "Hue connected")}>Pair</button>
            </div>
            <div className="fh muted" style={{ fontSize: 12 }}>
              {bridges && !bridges.length ? "No bridge found automatically — type its IP (see the Hue app → Settings → Bridges). " : ""}
              Press the round button on top of the bridge, then click Pair within 30 seconds.</div>
          </>
        )}
        {err && <div className="err">{err}</div>}
      </div>
    </section>
  );
}
