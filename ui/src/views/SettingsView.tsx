import { Brain, FolderOpen, Info, KeyRound, Keyboard, Mic, Monitor, RefreshCw, ShieldCheck, Smartphone, Trash2, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Field, Toggle } from "../components/ui";
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
        <Field label="Assistant name" hint="What ORION calls itself (wake word stays “Hey Jarvis”).">
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
        <Field label="Free-tier limits" hint="ORION paces itself to stay under these. Match them to your quota in AI Studio.">
          <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
            {[st.model_fast, st.model_smart].filter((m, i, a) => a.indexOf(m) === i).map((m) => {
              const lim = st.model_limits[m] ?? { rpm: 10, rpd: 250 };
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
        <Field label="“Hey Jarvis” wake word" hint="Runs fully offline on your PC; audio is only sent to Gemini after you say it. Needs the optional openwakeword package.">
          <Toggle on={st.voice.wake_word} onChange={(v) => saveVoice({ wake_word: v })} />
        </Field>
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
        <Field label={`ORION ${s.engine?.version}`} hint={<>Data folder: <code>{s.engine?.data_dir}</code></>}>
          <button className="btn" onClick={() => api.post("/api/open-folder/data")}><FolderOpen size={14} /> Open</button>
        </Field>
      </section>
    </div></div>
  );
}

interface RemoteStatus { enabled: boolean; running: boolean; port: number; ip: string; url?: string; qr_svg?: string; error?: string | null }

function PhoneRemote() {
  const toast = useStore((s) => s.toast);
  const [st, setSt] = useState<RemoteStatus | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<RemoteStatus>("/api/remote").then(setSt).catch(() => {}); }, []);
  const act = async (fn: () => Promise<RemoteStatus>) => {
    setBusy(true);
    try { setSt(await fn()); } catch (e) { toast({ title: "Phone remote", body: (e as Error).message, tone: "error" }); }
    finally { setBusy(false); }
  };
  return (
    <section className="settings-sec">
      <h3><Smartphone size={16} /> Phone remote</h3>
      <Field label="Control ORION from your phone" hint="Chat, quick actions (media, volume, lock), live task progress and approvals from any phone on the same Wi-Fi. The phone can't change settings. Windows may ask to allow ORION through the firewall — allow it for private networks.">
        <Toggle on={!!st?.enabled} onChange={(v) => !busy && act(() => api.post<RemoteStatus>("/api/remote", { enabled: v }))} />
      </Field>
      {st?.running && st.url && (
        <div className="field" style={{ gridTemplateColumns: "auto 1fr", alignItems: "start" }}>
          {st.qr_svg ? <div className="remote-qr" dangerouslySetInnerHTML={{ __html: st.qr_svg }} /> : null}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="fl">Scan with your phone's camera</div>
            <div className="fh">Then use “Add to Home Screen” for an app-like icon. Or open this link on the phone:</div>
            <code style={{ fontSize: 11.5, wordBreak: "break-all", color: "var(--text-2)" }}>{st.url}</code>
            <div className="row">
              <button className="btn sm" onClick={() => { navigator.clipboard.writeText(st.url!); toast({ title: "Link copied", tone: "success" }); }}>Copy link</button>
              <button className="btn sm" disabled={busy} onClick={() => act(() => api.post<RemoteStatus>("/api/remote/rotate"))}><RefreshCw size={12} /> New code</button>
            </div>
            <div className="fh">Lost a phone? “New code” disconnects every paired device. The link is plain HTTP — use it on networks you trust.</div>
          </div>
        </div>
      )}
      {st?.error && <div className="err" style={{ padding: "0 18px 12px" }}>{st.error}</div>}
    </section>
  );
}
