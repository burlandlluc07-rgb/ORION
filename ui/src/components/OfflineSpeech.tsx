import { Download, HardDrive, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { Field, Toggle } from "./ui";

interface Info {
  model: string; label: string; installed: boolean; runtime: boolean; enabled: boolean; loaded: boolean;
  load_error: string | null; voice_on: boolean; state: "idle" | "downloading" | "verifying" | "unpacking" | "error";
  done_bytes: number; download_bytes: number; disk_bytes: number; ram_mb: number; languages: string;
  source: string; folder: string; error: string | null;
}

const mb = (b: number) => (b >= 1e9 ? `${(b / 1e9).toFixed(2)} GB` : `${Math.round(b / 1e6)} MB`);

/** Settings → Voice → Offline speech recognition: the optional Parakeet model, downloaded only on request. */
export function OfflineSpeech() {
  const s = useStore();
  const vs = s.engine?.settings.voice;
  const [info, setInfo] = useState<Info | null>(null);
  const [agree, setAgree] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api.get<Info>("/api/offline-speech").then(setInfo).catch(() => {});
  useEffect(() => { load(); }, []);
  const busy = !!info && info.state !== "idle" && info.state !== "error";
  useEffect(() => {  // poll while downloading, and while the model loads after it's turned on
    if (!info || !(busy || (info.enabled && info.installed && info.voice_on && !info.loaded && !info.load_error))) return;
    const t = window.setInterval(load, 1000);
    return () => window.clearInterval(t);
  }, [info?.state, info?.enabled, info?.loaded, info?.installed]);
  if (!info || !vs) return null;

  const act = (p: Promise<Info>) => { setErr(null); p.then((i) => { setInfo(i); s.refreshState(); }).catch((e) => setErr((e as Error).message)); };
  const pct = info.download_bytes ? Math.min(100, (info.done_bytes / info.download_bytes) * 100) : 0;

  return (
    <Field label="Offline speech recognition"
      hint="Understands what you say on this PC, with no Gemini request and no audio leaving the computer. Makes the wake word and spoken yes/no reliable when the free quota is used up. Gemini is still used when the offline model hears nothing. Classic voice mode, wake word and spoken approvals use it; Live mode keeps talking to Gemini.">
      <div style={{ display: "flex", flexDirection: "column", gap: 8, width: "100%" }}>
        {!info.runtime ? (
          <div className="d" style={{ fontSize: 12.5 }}>The offline speech engine isn't part of this ORION build.</div>
        ) : info.installed ? (
          <>
            <div className="row" style={{ gap: 10 }}>
              <span className="grow" style={{ fontSize: 13 }}>
                {info.label} · {mb(info.disk_bytes)} on disk
                <div className="muted" style={{ fontSize: 12 }}>
                  {!info.enabled ? "Installed, turned off." : info.loaded ? `Ready · about ${info.ram_mb} MB of memory while voice is on.`
                    : info.load_error ? <span style={{ color: "var(--bad)" }}>{info.load_error}</span>
                    : info.voice_on ? "Loading…" : "Ready. It loads when voice is on."}
                </div>
              </span>
              <Toggle label="Use offline speech recognition" on={info.enabled}
                onChange={(v) => { s.saveSettings({ voice: { ...vs, offline_stt: v, ...(v || vs.wake_engine !== "offline" ? {} : { wake_engine: "windows" }) } }); setTimeout(load, 300); }} />
            </div>
            <div className="row" style={{ gap: 6 }}>
              <button className="btn ghost" style={{ fontSize: 12 }} onClick={() => act(api.del<Info>("/api/offline-speech"))}>
                <Trash2 size={13} /> Remove model
              </button>
            </div>
          </>
        ) : busy ? (
          <>
            <div className="meter">
              <div className="meter-top">
                <span>{info.state === "downloading" ? "Downloading" : info.state === "verifying" ? "Checking the SHA-256" : "Unpacking"}</span>
                <span className="v">{info.state === "downloading" ? `${mb(info.done_bytes)} / ${mb(info.download_bytes)}` : <Loader2 size={13} className="spin" />}</span>
              </div>
              <div className="bar"><i style={{ width: `${info.state === "downloading" ? pct : 100}%` }} /></div>
            </div>
            <div className="row"><button className="btn ghost" style={{ fontSize: 12 }} onClick={() => act(api.post<Info>("/api/offline-speech/cancel"))}>Cancel</button></div>
          </>
        ) : (
          <>
            <div className="d" style={{ fontSize: 12.5, lineHeight: 1.5 }}>
              <HardDrive size={13} style={{ verticalAlign: -2 }} /> {info.label}: <b>{mb(info.download_bytes)}</b> download,
              {" "}<b>{mb(info.disk_bytes)}</b> on disk, about <b>{info.ram_mb} MB</b> of memory while voice is on.
              Understands {info.languages}. Downloaded from sherpa-onnx's GitHub releases and checked against a fixed
              SHA-256 before it's used. Remove it any time.
            </div>
            <label className="row" style={{ gap: 8, fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              Download {mb(info.download_bytes)} and keep {mb(info.disk_bytes)} in ORION's data folder
            </label>
            <div className="row">
              <button className="btn" disabled={!agree} onClick={() => act(api.post<Info>("/api/offline-speech/download", { consent: true }))}>
                <Download size={14} /> Download
              </button>
            </div>
          </>
        )}
        {info.error && !busy && <div className="err">{info.error}</div>}
        {err && <div className="err">{err}</div>}
      </div>
    </Field>
  );
}
