import { Zap, Brain, FolderOpen, Info, KeyRound, Keyboard, Mic, Monitor, RefreshCw, ShieldCheck, Smartphone, Stethoscope, CheckCircle2, XCircle, MinusCircle, Loader2, Trash2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, Modal, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Settings } from "../lib/types";

interface VoiceInfo {
  devices: { inputs: { id: number; name: string; default: boolean }[]; outputs: { id: number; name: string; default: boolean }[]; error?: string };
  tts_voices: { id: string; label: string }[];
  live_voices: string[];
}

export function SettingsView() {
  const s = useStore();
  const st = s.engine?.settings;
  const [voice, setVoice] = useState<VoiceInfo | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [key, setKey] = useState("");
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [newRoot, setNewRoot] = useState("");

  useEffect(() => {
    api.get<VoiceInfo>("/api/voice").then(setVoice).catch(() => {});
    api.get<{ available: string[] }>("/api/quota").then((q) => setModels(q.available)).catch(() => {});
  }, []);
  useEffect(() => { if (s.models.length) setModels(s.models); }, [s.models]);

  if (!st) return null;
  const save = (patch: Partial<Settings> | Record<string, unknown>) =>
    s.saveSettings(patch).catch((e) => s.toast({ title: "Couldn't save", body: e.message, tone: "error" }));
  const saveVoice = (patch: Partial<Settings["voice"]>) => save({ voice: patch });

  const testKey = async () => {
    setKeyMsg(null);
    try {
      const r = await api.post<{ ok: boolean; stored_in: string; models: number }>("/api/key", { key });
      setKeyMsg({ ok: true, text: `Key saved (${r.stored_in === "keyring" ? "Windows Credential Manager" : "local file"}) — ${r.models} models available.` });
      setKey("");
      s.refreshState();
    } catch (e) {
      setKeyMsg({ ok: false, text: (e as Error).message });
    }
  };

  const genModels = models.filter((m) => m.startsWith("gemini") && !/tts|embedding|image|live|native-audio|aqa/.test(m));
  const modelOptions = (cur: string) => Array.from(new Set([cur, "gemini-flash-lite-latest", "gemini-flash-latest", ...genModels]));

  return (
    <div className="page"><div className="page-inner" style={{ maxWidth: 860 }}>
      <div className="page-head"><div className="grow"><h2>Settings</h2><p>Make ORION yours.</p></div></div>

      <section className="settings-sec">
        <h3><UserRound size={16} /> Personal</h3>
        <Field label="Your name" hint="ORION greets and addresses you by it.">
          <input className="input" defaultValue={st.user_name} onBlur={(e) => save({ user_name: e.target.value.trim() })} />
        </Field>
        <Field label="Assistant name" hint="What ORION calls itself. Set the words that wake it under Voice → Wake word.">
          <input className="input" defaultValue={st.assistant_name} onBlur={(e) => save({ assistant_name: e.target.value.trim() || "ORION" })} />
        </Field>
        <Field label="Personality">
          <select className="select" value={st.persona} onChange={(e) => save({ persona: e.target.value })}>
            <option value="jarvis">J.A.R.V.I.S. — composed, witty</option>
            <option value="concise">Concise — minimal words</option>
            <option value="friendly">Friendly — warm, upbeat</option>
          </select>
        </Field>
        <Field label="Theme">
          <select className="select" value={st.theme} onChange={(e) => save({ theme: e.target.value })}>
            <option value="dark">Dark</option><option value="light">Light</option><option value="system">Match Windows</option>
          </select>
        </Field>
      </section>

      <section className="settings-sec">
        <h3><Brain size={16} /> Brain (Gemini)</h3>
        <Field label={<><KeyRound size={14} /> API key</>} hint={<>{s.engine?.has_key ? "A key is saved. Paste a new one to replace it." : "Required."} Get a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">aistudio.google.com</a>.</>}>
          <input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={s.engine?.has_key ? "••••••••••••••••" : "AIza…"} />
          <button className="btn primary" disabled={key.length < 20} onClick={testKey}>Save</button>
        </Field>
        {keyMsg && <div style={{ padding: "0 18px 12px" }} className={keyMsg.ok ? "" : "err"}>{keyMsg.text}</div>}
        <Field label="Fast model" hint="Used for everyday requests. Flash-Lite has the biggest free quota.">
          <select className="select" value={st.model_fast} onChange={(e) => save({ model_fast: e.target.value })}>
            {modelOptions(st.model_fast).map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Deep model" hint="Used for research, writing, multi-step jobs and screen vision.">
          <select className="select" value={st.model_smart} onChange={(e) => save({ model_smart: e.target.value })}>
            {modelOptions(st.model_smart).map((m) => <option key={m}>{m}</option>)}
          </select>
        </Field>
        <Field label="Instant local commands" hint="Handle “open Spotify”, “volume 40”, “next song”, timers… on-device with zero API calls.">
          <Toggle on={st.fast_path} onChange={(v) => save({ fast_path: v })} />
        </Field>
        <Field label="Thinking budget" hint="0 = fastest. Higher values let Gemini reason longer on hard problems (uses more quota).">
          <select className="select" value={st.thinking_budget} onChange={(e) => save({ thinking_budget: Number(e.target.value) })}>
            <option value={0}>Off (fastest)</option><option value={512}>Light</option><option value={2048}>Medium</option><option value={8192}>Deep</option>
          </select>
        </Field>
        <Field label="Spare models" hint="When the daily free quota runs out, keep going on other free Flash models. Each has its own quota.">
          <Toggle on={st.extra_models} onChange={(v) => save({ extra_models: v })} />
        </Field>
        <Field label="Free-tier limits" hint="ORION paces itself to stay under these, and corrects them automatically when Google reports a different limit.">
          <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
            {[st.model_fast, st.model_smart].filter((m, i, a) => a.indexOf(m) === i).map((m) => {
              const lim = st.model_limits[m] ?? (m.includes("lite") ? { rpm: 15, rpd: 500 } : { rpm: 5, rpd: 20 });
              const set = (p: Partial<typeof lim>) => save({ model_limits: { ...st.model_limits, [m]: { ...lim, ...p } } });
              return (
                <div key={m} className="row" style={{ fontSize: 12 }}>
                  <span className="grow ellipsis muted">{m.replace("gemini-", "")}</span>
                  <input className="input" style={{ width: 64 }} type="number" min={1} defaultValue={lim.rpm} onBlur={(e) => set({ rpm: Number(e.target.value) })} title="Requests per minute" />/min
                  <input className="input" style={{ width: 76 }} type="number" min={1} defaultValue={lim.rpd} onBlur={(e) => set({ rpd: Number(e.target.value) })} title="Requests per day" />/day
                </div>
              );
            })}
          </div>
        </Field>
      </section>

      <section className="settings-sec">
        <h3><Mic size={16} /> Voice</h3>
        <Field label="Voice mode" hint={<>Live: real-time voice-to-voice with Gemini (fastest, most natural). Classic: transcribe → think → speak with a neural voice; also runs instant local commands.{s.engine?.live_model ? <> Live model: <code>{s.engine.live_model}</code>.</> : null}</>}>
          <select className="select" value={st.voice.mode} onChange={(e) => saveVoice({ mode: e.target.value as Settings["voice"]["mode"] })}>
            <option value="live">Live (recommended)</option><option value="classic">Classic</option><option value="off">Off</option>
          </select>
        </Field>
        {st.voice.mode === "live" && (
          <Field label="Live voice">
            <select className="select" value={st.voice.live_voice} onChange={(e) => saveVoice({ live_voice: e.target.value })}>
              {(voice?.live_voices ?? [st.voice.live_voice]).map((v) => <option key={v}>{v}</option>)}
            </select>
          </Field>
        )}
        {st.voice.mode === "classic" && (
          <Field label="Speaking voice" hint="Microsoft neural voices — free, no key needed.">
            <select className="select" value={st.voice.tts_voice} onChange={(e) => saveVoice({ tts_voice: e.target.value })}>
              {(voice?.tts_voices ?? []).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </Field>
        )}
        <Field label="Wake word" hint="Say it to start talking, hands-free. Recognised offline by Windows: audio only goes to Gemini after it hears the wake word.">
          <Toggle on={st.voice.wake_word} onChange={(v) => saveVoice({ wake_word: v })} />
        </Field>
        {st.voice.wake_word && <WakeWordSettings />}
        <Field label="Speak replies" hint="Read answers aloud for voice requests, timers and reminders.">
          <Toggle on={st.voice.speak_replies} onChange={(v) => saveVoice({ speak_replies: v })} />
        </Field>
        <Field label="Microphone">
          <select className="select" value={st.voice.input_device ?? ""} onChange={(e) => saveVoice({ input_device: e.target.value === "" ? null : Number(e.target.value) })}>
            <option value="">System default</option>
            {voice?.devices.inputs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        <Field label="Speakers">
          <select className="select" value={st.voice.output_device ?? ""} onChange={(e) => saveVoice({ output_device: e.target.value === "" ? null : Number(e.target.value) })}>
            <option value="">System default</option>
            {voice?.devices.outputs.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </Field>
        {voice?.devices.error && <div className="err" style={{ padding: "0 18px 12px" }}>Audio: {voice.devices.error}</div>}
      </section>

      <section className="settings-sec">
        <h3><Zap size={16} /> Proactive & learning</h3>
        <Field label="Proactive mode" hint="ORION speaks up on its own: low battery, a program hogging the PC, low disk space, finished downloads, upcoming meetings, internet drops. Runs locally — no AI quota used.">
          <Toggle on={st.proactive} onChange={(v) => save({ proactive: v })} />
        </Field>
        <Field label="Say alerts out loud"><Toggle on={st.proactive_voice} onChange={(v) => save({ proactive_voice: v })} /></Field>
        <Field label="Tell me when downloads finish"><Toggle on={st.proactive_downloads} onChange={(v) => save({ proactive_downloads: v })} /></Field>
        <Field label="Quiet hours" hint="Only critical alerts (like a dying battery) interrupt you during these hours.">
          <input type="time" className="input" style={{ width: 120 }} defaultValue={st.quiet_start} onBlur={(e) => save({ quiet_start: e.target.value })} />
          <span className="muted">to</span>
          <input type="time" className="input" style={{ width: 120 }} defaultValue={st.quiet_end} onBlur={(e) => save({ quiet_end: e.target.value })} />
        </Field>
        <Field label="Learn about me automatically" hint="After conversations, ORION files what it learned in its memory vault: lasting facts in permanent notes, passing details in This week. Uses a few requests a day at most.">
          <Toggle on={st.auto_memory} onChange={(v) => save({ auto_memory: v })} />
        </Field>
        <Field label="Memory vault folder" hint="Where ORION keeps its Obsidian notes. Leave empty for Documents\ORION Brain. Point it at your own Obsidian vault and ORION uses an “ORION Brain” folder inside it.">
          <input className="input" defaultValue={st.vault_path} placeholder="Documents\ORION Brain" onBlur={(e) => e.target.value !== st.vault_path && save({ vault_path: e.target.value.trim() })} />
        </Field>
        <Field label="Weekly memory review" hint="When ORION goes through This week's short-term memory: keep what matters, delete the rest. If the PC is off then, it runs next time it's on.">
          <div className="row" style={{ gap: 8 }}>
            <select className="select" value={st.memory_review_day} onChange={(e) => save({ memory_review_day: Number(e.target.value) })}>
              {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
            <select className="select" value={st.memory_review_hour} onChange={(e) => save({ memory_review_hour: Number(e.target.value) })}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
            </select>
          </div>
        </Field>
      </section>

      <section className="settings-sec">
        <h3><ShieldCheck size={16} /> Safety & permissions</h3>
        <Field label="Approvals" hint="Safe actions (reading, searching, volume…) never ask. High-risk actions (deleting, shell commands, power) always ask unless you choose Trust.">
          <select className="select" value={st.approval_mode} onChange={(e) => save({ approval_mode: e.target.value })}>
            <option value="ask">Ask before changes (recommended)</option>
            <option value="auto_safe">Only ask for high-risk actions</option>
            <option value="trust">Never ask (not recommended)</option>
          </select>
        </Field>
        <Field label="Extra folders ORION may change" hint="By default file changes are limited to your user folder. Deletes always go to the Recycle Bin.">
          <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
            {st.allowed_roots.map((r) => (
              <div key={r} className="row"><code className="grow ellipsis" style={{ fontSize: 12 }}>{r}</code>
                <button className="icon-btn sm" onClick={() => save({ allowed_roots: st.allowed_roots.filter((x) => x !== r) })}><Trash2 size={13} /></button></div>
            ))}
            <div className="row">
              <input className="input" placeholder="D:\\Projects" value={newRoot} onChange={(e) => setNewRoot(e.target.value)} />
              <button className="btn" disabled={!newRoot.trim()} onClick={() => { save({ allowed_roots: [...st.allowed_roots, newRoot.trim()] }); setNewRoot(""); }}>Add</button>
            </div>
          </div>
        </Field>
      </section>

      <PhoneRemote />

      <section className="settings-sec">
        <h3><Monitor size={16} /> App</h3>
        <Field label="Launch when Windows starts" hint="Starts quietly in the tray, ready for “Hey Jarvis” and your routines.">
          <Toggle on={st.launch_on_startup} onChange={(v) => save({ launch_on_startup: v })} />
        </Field>
        <Field label="Check for updates" hint="Looks for new ORION releases on GitHub once a day and shows a link — never installs anything by itself.">
          <Toggle on={st.check_updates} onChange={(v) => save({ check_updates: v })} />
        </Field>
        <Field label="Start minimized to tray"><Toggle on={st.start_minimized} onChange={(v) => save({ start_minimized: v })} /></Field>
        <Field label="Closing the window keeps ORION running in the tray"><Toggle on={st.close_to_tray} onChange={(v) => save({ close_to_tray: v })} /></Field>
        <Field label="Conversation memory" hint="How many recent turns are sent with each message. Fewer = faster and cheaper.">
          <select className="select" value={st.history_turns} onChange={(e) => save({ history_turns: Number(e.target.value) })}>
            {[4, 8, 12, 20, 30].map((n) => <option key={n} value={n}>{n} turns</option>)}
          </select>
        </Field>
      </section>

      <section className="settings-sec">
        <h3><Keyboard size={16} /> Shortcuts</h3>
        <Field label="Quick Bar" hint="Summon ORION over any app. Takes effect after restart."><input className="input" defaultValue={st.hotkey_quickbar} onBlur={(e) => save({ hotkey_quickbar: e.target.value })} /></Field>
        <Field label="Push to talk / end voice" hint="Takes effect after restart."><input className="input" defaultValue={st.hotkey_voice} onBlur={(e) => save({ hotkey_voice: e.target.value })} /></Field>
        <Field label="In the app" hint="Ctrl+K focus input · Ctrl+N new chat · Ctrl+B sidebar · Esc stop · Ctrl+Enter approve"><span /></Field>
      </section>

      <section className="settings-sec">
        <h3><Info size={16} /> About</h3>
        <Field label="System check" hint="Tests your key, models, microphone, speakers, voice, search and app control — and says how to fix anything broken.">
          <SystemCheck />
        </Field>
        <Field label={`ORION ${s.engine?.version}`} hint={<>Data folder: <code>{s.engine?.data_dir}</code> (settings, database, logs, skills, plugins)</>}>
          <button className="btn" onClick={() => api.post("/api/open-folder/data")}><FolderOpen size={14} /> Open</button>
        </Field>
      </section>
    </div></div>
  );
}

interface RemoteStatus {
  enabled: boolean; running: boolean; port: number; lan_url: string; access: "lan" | "tailscale" | "cloudflare"; secure: boolean;
  tunnel: { mode: string; url: string | null; state: string; detail: string; tailscale_installed: boolean; cloudflared_installed: boolean };
  devices: { id: string; name: string; created: number; last_seen: number; push: boolean }[];
  pair_url?: string; qr_svg?: string; error?: string | null;
}

function PhoneRemote() {
  const toast = useStore((s) => s.toast);
  const bump = useStore((s) => s.bump["remote.changed"]);
  const [st, setSt] = useState<RemoteStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<RemoteStatus>("/api/remote").then(setSt).catch(() => {});
  useEffect(() => { load(); }, [bump]);
  useEffect(() => {  // poll while a tunnel is starting
    if (st?.tunnel.state !== "starting") return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [st?.tunnel.state]);
  const act = async (body: Record<string, unknown>) => {
    setBusy(true);
    try { setSt(await api.post<RemoteStatus>("/api/remote", body)); } catch (e) { toast({ title: "Phone app", body: (e as Error).message, tone: "error" }); }
    finally { setBusy(false); }
  };
  if (!st) return null;
  const MODES = [
    { id: "lan", name: "Home Wi-Fi", desc: "Phone and PC on the same network. Simplest; no voice or notifications (needs HTTPS)." },
    { id: "tailscale", name: "Anywhere · Tailscale", desc: "Recommended. Free, private and encrypted, with a stable address. Install Tailscale on this PC and your phone and sign in with the same account." },
    { id: "cloudflare", name: "Anywhere · Instant link", desc: "No account needed. HTTPS link via Cloudflare; it changes when ORION restarts (your phone gets a one-tap re-pair notification)." },
  ] as const;
  return (
    <section className="settings-sec">
      <h3><Smartphone size={16} /> Phone app</h3>
      <Field label="Control ORION from your phone" hint="Chat and talk to ORION, see and control your screen, run media and apps, approve actions and get notifications — like a remote for your PC. The phone can't change settings or accounts.">
        <Toggle on={st.enabled} onChange={(v) => !busy && act({ enabled: v })} />
      </Field>
      {st.enabled && (
        <>
          <div className="field" style={{ gridTemplateColumns: "1fr" }}>
            <div className="fl">How your phone connects</div>
            <div className="list">
              {MODES.map((m) => (
                <button key={m.id} className="item" style={{ textAlign: "left", cursor: "pointer", borderColor: st.access === m.id ? "var(--text-2)" : undefined }}
                  disabled={busy} onClick={() => act({ access: m.id })}>
                  <span className={`dot ${st.access === m.id ? "ok" : ""}`} />
                  <div className="grow"><div className="t">{m.name}</div><div className="d">{m.desc}</div></div>
                </button>
              ))}
            </div>
            {st.access !== "lan" && (
              <div className={st.tunnel.state === "error" ? "err" : "muted"} style={{ fontSize: 12.5 }}>
                {st.tunnel.state === "starting" && <Loader2 size={12} className="spin" />} {st.tunnel.detail || st.tunnel.state}
                {st.access === "tailscale" && !st.tunnel.tailscale_installed && <> — <a href="https://tailscale.com/download" target="_blank" rel="noreferrer">download Tailscale</a></>}
              </div>
            )}
          </div>
          {st.running && st.pair_url && (
            <div className="field" style={{ gridTemplateColumns: "auto 1fr", alignItems: "start" }}>
              {st.qr_svg ? <div className="remote-qr" dangerouslySetInnerHTML={{ __html: st.qr_svg }} /> : null}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div className="fl">Pair a phone</div>
                <div className="fh">1. Scan with the phone's camera (code works once, for 10 minutes).<br />
                  2. In the browser menu choose <b>Add to Home Screen</b> / <b>Install app</b>.<br />
                  3. Open ORION from your home screen and tap Activity → Enable notifications.</div>
                <code style={{ fontSize: 11, wordBreak: "break-all", color: "var(--text-3)" }}>{st.pair_url}</code>
                <div className="row">
                  <button className="btn sm" onClick={load}><RefreshCw size={12} /> New code</button>
                  <button className="btn sm" onClick={() => { navigator.clipboard.writeText(st.pair_url!); toast({ title: "Pairing link copied", tone: "success" }); }}>Copy link</button>
                </div>
                {!st.secure && <div className="fh">This connection isn't HTTPS, so voice and notifications won't work on the phone. Choose an “Anywhere” option to enable them.</div>}
              </div>
            </div>
          )}
          <div className="field" style={{ gridTemplateColumns: "1fr" }}>
            <div className="row"><div className="fl grow">Paired phones</div>
              {st.devices.some((d) => d.push) && <button className="btn sm" onClick={() => api.post<{ sent: number }>("/api/push/test").then((r) => toast({ title: `Test sent to ${r.sent} phone(s)`, tone: "success" }))}>Send test notification</button>}
            </div>
            {st.devices.length === 0 ? <div className="fh">None yet.</div> : st.devices.map((d) => (
              <div key={d.id} className="row" style={{ fontSize: 13 }}>
                <Smartphone size={14} className="muted" />
                <span className="grow">{d.name} <span className="muted">· last seen {new Date(d.last_seen * 1000).toLocaleString()}{d.push ? " · notifications on" : ""}</span></span>
                <button className="btn sm ghost" onClick={() => api.del<RemoteStatus>(`/api/devices/${d.id}`).then(setSt)}><Trash2 size={12} /> Remove</button>
              </div>
            ))}
          </div>
        </>
      )}
      {st.error && <div className="err" style={{ padding: "0 18px 12px" }}>{st.error}</div>}
    </section>
  );
}

interface CheckRow { id: string; name: string; ok: boolean | null; detail: string; fix: string }

function SystemCheck() {
  const [rows, setRows] = useState<CheckRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setOpen(true); setBusy(true); setRows(null);
    try { setRows(await api.post<CheckRow[]>("/api/diagnostics", { generate: true })); }
    catch (e) { setRows([{ id: "err", name: "System check", ok: false, detail: (e as Error).message, fix: "" }]); }
    finally { setBusy(false); }
  };
  const bad = rows?.filter((r) => r.ok === false).length ?? 0;
  return (
    <>
      <button className="btn" onClick={run}><Stethoscope size={14} /> Run</button>
      {open && (
        <Modal title={busy ? "Checking ORION…" : bad ? `${bad} problem${bad === 1 ? "" : "s"} found` : "All systems nominal"} onClose={() => setOpen(false)} wide
          footer={<><button className="btn ghost" onClick={() => setOpen(false)}>Close</button><button className="btn" disabled={busy} onClick={run}>Run again</button></>}>
          {busy && <div className="row muted"><Loader2 size={15} className="spin" /> Testing microphone, Gemini, voice and search — about 10 seconds.</div>}
          {rows && <div className="list">
            {rows.map((r) => (
              <div key={r.id} className="item" style={{ padding: "10px 14px", alignItems: "flex-start" }}>
                {r.ok === true ? <CheckCircle2 size={17} style={{ color: "var(--text-2)" }} /> : r.ok === false
                  ? <XCircle size={17} style={{ color: "var(--bad)" }} /> : <MinusCircle size={17} className="muted" />}
                <div className="grow">
                  <div className="t" style={{ fontWeight: 550 }}>{r.name}</div>
                  <div className="d">{r.detail}</div>
                  {r.fix && <div className="d" style={{ color: "var(--text)" }}>→ {r.fix}</div>}
                </div>
              </div>
            ))}
          </div>}
        </Modal>
      )}
    </>
  );
}


const WAKE_SUGGESTIONS = ["Hey ORION", "ORION", "Hey Jarvis", "Jarvis", "Friday", "Computer", "Hey Assistant"];

function WakeWordSettings() {
  const st = useStore((s) => s.engine?.settings)!;
  const status = useStore((s) => s.engine?.voice);
  const heard = useStore((s) => s.heard);
  const save = useStore((s) => s.saveSettings);
  const vs = st.voice;
  const [draft, setDraft] = useState("");
  const setPhrases = (p: string[]) => save({ voice: { ...vs, wake_phrases: p } });
  const add = (p: string) => {
    const t = p.trim();
    if (t && !vs.wake_phrases.some((x) => x.toLowerCase() === t.toLowerCase())) setPhrases([...vs.wake_phrases, t]);
    setDraft("");
  };
  const fresh = heard && Date.now() - heard.at < 15000;
  return (
    <>
      <Field label="Wake phrases" hint="Any words or names, e.g. “Hey ORION”, “Friday” or your own. Two or more syllables work best; several can be active at once.">
        <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%" }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {vs.wake_phrases.map((p) => (
              <span key={p} className="chip">{p}
                <button className="icon-btn" title="Remove" disabled={vs.wake_phrases.length < 2}
                  onClick={() => setPhrases(vs.wake_phrases.filter((x) => x !== p))}>×</button></span>
            ))}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <input className="input" value={draft} placeholder="Add a phrase…" onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add(draft)} />
            <button className="btn" onClick={() => add(draft)} disabled={!draft.trim()}>Add</button>
          </div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {WAKE_SUGGESTIONS.filter((s) => !vs.wake_phrases.some((p) => p.toLowerCase() === s.toLowerCase())).map((s) => (
              <button key={s} className="btn ghost" style={{ fontSize: 12, padding: "2px 8px" }} onClick={() => add(s)}>+ {s}</button>
            ))}
          </div>
        </div>
      </Field>
      <Field label="Sensitivity" hint="Lower it if ORION wakes up by itself; raise it if it misses you. Say a wake phrase and watch the meter below.">
        <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
          <div className="row" style={{ gap: 10 }}>
            <span className="muted" style={{ fontSize: 12 }}>Strict</span>
            <input type="range" min={0} max={1} step={0.05} defaultValue={vs.wake_sensitivity} className="grow"
              onMouseUp={(e) => save({ voice: { ...vs, wake_sensitivity: Number((e.target as HTMLInputElement).value) } })}
              onTouchEnd={(e) => save({ voice: { ...vs, wake_sensitivity: Number((e.target as HTMLInputElement).value) } })} />
            <span className="muted" style={{ fontSize: 12 }}>Relaxed</span>
          </div>
          <div className="d" style={{ fontSize: 12.5 }}>
            {status?.error && status.wake_word === false ? <span style={{ color: "var(--danger)" }}>{status.error}</span>
              : fresh ? <>Heard “{heard!.phrase}” · {Math.round(heard!.confidence * 100)}% sure · {heard!.accepted ? "woke up" : "ignored (below sensitivity)"}</>
              : status?.wake_word ? <>Listening for {(status.wake_phrases ?? vs.wake_phrases).map((p) => `“${p}”`).join(", ")}{status.wake_language ? ` (${status.wake_language})` : ""}. Say one to test.</>
              : "Starting…"}
          </div>
        </div>
      </Field>
      <Field label="Speech language" hint="The language you say the wake phrase in. Uses the speech languages installed in Windows (Settings → Time & language → Speech).">
        <select className="select" value={vs.wake_language} onChange={(e) => save({ voice: { ...vs, wake_language: e.target.value } })}>
          <option value="">Same as Windows</option>
          {[["en-US", "English (US)"], ["en-GB", "English (UK)"], ["es-ES", "Spanish"], ["fr-FR", "French"], ["de-DE", "German"],
            ["ja-JP", "Japanese"], ["zh-CN", "Chinese (Simplified)"], ["zh-TW", "Chinese (Traditional)"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </Field>
    </>
  );
}
