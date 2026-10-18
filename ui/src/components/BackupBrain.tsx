import { KeyRound, LifeBuoy, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Settings } from "../lib/types";
import { Field } from "./ui";

type Provider = Settings["backup_provider"];
interface Preset { label: string; base_url: string; model: string; key_url: string; needs_key: boolean }
interface Info { provider: Provider; base_url: string; model: string; has_key: boolean; ready: boolean; active: boolean; presets: Record<string, Preset> }

const HINTS: Record<string, string> = {
  omniroute: "OmniRoute is a free gateway you run on this PC. It moves on to another free model when one runs out. Install it with “npm install -g omniroute”, start it, connect a few free providers in its dashboard, then press Test & save.",
  openrouter: "OpenRouter has free models (their names end in “:free”). Make a free account and paste its key.",
  groq: "Groq has a free tier with fast open models. Make a free account and paste its key.",
  custom: "Any service that speaks the OpenAI Chat Completions API. Type its address, for example http://localhost:1234/v1.",
};

/** Settings → Brain → Backup brain: the provider ORION switches to while Gemini's free quota is used up. */
export function BackupBrain() {
  const s = useStore();
  const [info, setInfo] = useState<Info | null>(null);
  const [provider, setProvider] = useState<Provider>("off");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = () => api.get<Info>("/api/backup").then((i) => {
    setInfo(i);
    setProvider(i.provider);
    setBaseUrl(i.provider === "off" ? "" : i.base_url);
    setModel(i.provider === "off" ? "" : i.model);
  }).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!info) return null;

  const preset = provider !== "off" ? info.presets[provider] : null;
  const pick = (p: Provider) => {
    setProvider(p);
    setMsg(null);
    setModels([]);
    const pr = p !== "off" ? info.presets[p] : null;
    setBaseUrl(p === info.provider ? info.base_url : pr?.base_url ?? "");
    setModel(p === info.provider ? info.model : pr?.model ?? "");
    if (p === "off" && info.provider !== "off") {
      api.post("/api/backup", { provider: "off" }).then(() => { load(); s.refreshState(); setMsg({ ok: true, text: "Backup brain turned off." }); })
        .catch((e) => setMsg({ ok: false, text: (e as Error).message }));
    }
  };
  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.post<{ ok: boolean; model: string; models: string[]; stored_in: string | null }>(
        "/api/backup", { provider, base_url: baseUrl, model, key });
      setModel(r.model);
      setModels(r.models);
      setKey("");
      setMsg({ ok: true, text: `It works. ORION will use ${r.model} whenever Gemini runs out of free quota.${r.stored_in === "keyring" ? " Key saved in Windows Credential Manager." : ""}` });
      load();
      s.refreshState();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  const savedKey = info.has_key && provider === info.provider;
  const forget = () => api.post("/api/backup", { provider: "off", forget_key: true })
    .then(() => { load(); s.refreshState(); setMsg({ ok: true, text: "Backup brain turned off and its key removed." }); })
    .catch((e) => setMsg({ ok: false, text: (e as Error).message }));

  return (
    <>
      <Field label={<><LifeBuoy size={14} /> Backup brain</>}
        hint={<>When Gemini's free quota runs out, ORION keeps chatting and running tasks on this instead, then goes back to Gemini. Voice conversations stay on Gemini.{info.active ? <> <b>In use right now.</b></> : null}</>}>
        <select className="select" value={provider} onChange={(e) => pick(e.target.value as Provider)}>
          <option value="off">Off</option>
          {Object.entries(info.presets).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
        </select>
      </Field>
      {preset && <>
        <div className="muted" style={{ padding: "0 18px 10px", fontSize: 12.5, lineHeight: 1.5 }}>
          {HINTS[provider]}{preset.key_url && <> <a href={preset.key_url} target="_blank" rel="noreferrer">{provider === "omniroute" ? "About OmniRoute" : "Get a key"}</a>.</>}
        </div>
        <Field label="Address" hint="Where ORION sends requests. The preset is usually right.">
          <input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={preset.base_url || "http://localhost:1234/v1"} />
        </Field>
        <Field label="Model" hint={provider === "omniroute" ? "“auto” lets OmniRoute choose and switch free models for you." : "Leave empty and ORION picks one when you test."}>
          <input className="input" list="backup-models" value={model} onChange={(e) => setModel(e.target.value)} placeholder={preset.model || "pick automatically"} />
          <datalist id="backup-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
        </Field>
        <Field label={<><KeyRound size={14} /> API key</>} hint={savedKey ? "A key is saved. Paste a new one to replace it." : preset.needs_key ? "Required. Saved in Windows Credential Manager, never in settings or logs." : provider === "omniroute" ? "Only if you turned on OmniRoute's key check (its Dashboard → API keys)." : "Only if the service asks for one."}>
          <input className="input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder={savedKey ? "••••••••••••••••" : "optional"} />
          <button className="btn primary" disabled={busy || (preset.needs_key && !key && !savedKey)} onClick={test}>
            {busy ? <Loader2 size={14} className="spin" /> : null} Test & save
          </button>
        </Field>
      </>}
      {msg && <div style={{ padding: "0 18px 12px" }} className={msg.ok ? "" : "err"}>{msg.text}</div>}
      {info.has_key && <div style={{ padding: "0 18px 12px" }}>
        <button className="btn ghost sm" onClick={forget}>Turn off and forget the saved key</button>
      </div>}
    </>
  );
}
