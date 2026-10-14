import { ArrowRight, ExternalLink, KeyRound, Loader2, Mic, Zap } from "lucide-react";
import { useState } from "react";
import { Orb } from "../components/Orb";
import { api } from "../lib/api";
import { useStore } from "../lib/store";

export function Onboarding() {
  const s = useStore();
  const hasKey = !!s.engine?.has_key;
  const [step, setStep] = useState(0);
  const [name, setName] = useState(s.engine?.settings.user_name ?? "");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState(s.engine?.settings.voice.mode ?? "live");

  const saveKey = async () => {
    setBusy(true);
    setErr("");
    try {
      await api.post("/api/key", { key });
      await s.refreshState();
      setStep(2);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const finish = async () => {
    await s.saveSettings({ user_name: name.trim(), onboarded: true, voice: { mode } });
    s.set({ onboarding: false });
  };

  return (
    <div className="onb">
      <div className="onb-card">
        <Orb size={step === 3 ? 170 : 130} state={step === 3 ? "listening" : "standby"} />
        <div className="steps-dots">{[0, 1, 2, 3].map((i) => <i key={i} className={i <= step ? "on" : ""} />)}</div>

        {step === 0 && (
          <>
            <h1>Hello. I'm ORION.</h1>
            <p>Your personal assistant for this PC — I can open apps, manage files, search the web, run routines and talk with you in real time.</p>
            <div className="onb-form">
              <label className="lbl">What should I call you?</label>
              <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name"
                onKeyDown={(e) => e.key === "Enter" && setStep(hasKey ? 2 : 1)} />
            </div>
            <button className="btn primary" onClick={() => setStep(hasKey ? 2 : 1)}>Continue <ArrowRight size={15} /></button>
          </>
        )}

        {step === 1 && (
          <>
            <h1>Connect my brain</h1>
            <p>ORION thinks with Google Gemini. A key is <b>free</b> and takes about a minute: sign in, click <i>Create API key</i>, copy it here.</p>
            <a className="btn" href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer"><ExternalLink size={14} /> Open Google AI Studio</a>
            <div className="onb-form">
              <label className="lbl"><KeyRound size={13} /> Gemini API key</label>
              <input className="input" type="password" autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder="AIza…"
                onKeyDown={(e) => e.key === "Enter" && key.length > 20 && saveKey()} />
              {err && <div className="err">{err}</div>}
              <div className="muted" style={{ fontSize: 12.5 }}>Stored in Windows Credential Manager on this PC only.</div>
            </div>
            <button className="btn primary" disabled={key.length < 20 || busy} onClick={saveKey}>
              {busy ? <><Loader2 size={15} className="spin" /> Checking…</> : <>Verify & continue <ArrowRight size={15} /></>}
            </button>
          </>
        )}

        {step === 2 && (
          <>
            <h1>How should we talk?</h1>
            <p>You can change this any time in Settings.</p>
            <div className="cards" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <button className={`card ${mode === "live" ? "on" : ""}`} style={mode === "live" ? { borderColor: "var(--accent)" } : undefined} onClick={() => setMode("live")}>
                <span className="ct"><Mic size={16} /> Live voice</span>
                <span className="cd">Real-time conversation — interrupt me any time. Fastest and most natural.</span>
              </button>
              <button className="card" style={mode === "classic" ? { borderColor: "var(--accent)" } : undefined} onClick={() => setMode("classic")}>
                <span className="ct"><Zap size={16} /> Classic</span>
                <span className="cd">Speak, then I answer with a neural voice. Uses less quota on long sessions.</span>
              </button>
            </div>
            <button className="btn primary" onClick={() => setStep(3)}>Continue <ArrowRight size={15} /></button>
          </>
        )}

        {step === 3 && (
          <>
            <h1>All systems online{name ? `, ${name}` : ""}.</h1>
            <p>Try <b>“open Spotify”</b>, <b>“what's the weather?”</b> or <b>“/morning-briefing”</b>.
              Press <kbd>Alt</kbd>+<kbd>Space</kbd> anywhere to summon me, or <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>J</kbd> to talk.</p>
            <button className="btn primary" autoFocus onClick={finish}>Let's go <ArrowRight size={15} /></button>
          </>
        )}
      </div>
    </div>
  );
}
