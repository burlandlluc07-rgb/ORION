import {
  Activity, Bell, BellOff, ChevronLeft, Keyboard, Lock, Mic, Monitor, MonitorUp, MousePointerClick, Moon, Pause, Play,
  Plus, Power, RefreshCw, SkipBack, SkipForward, Smartphone, Square, Volume2, VolumeX, Zap,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer } from "../components/Composer";
import { Orb } from "../components/Orb";
import { PlanList, StepList, TaskStatus } from "../components/TaskCard";
import { api, TOKEN } from "../lib/api";
import { blobToB64, speak, WavRecorder } from "../lib/recorder";
import { levels, useStore } from "../lib/store";
import type { Routine } from "../lib/types";
import { MessageView } from "./ChatView";

type Tab = "chat" | "control" | "screen" | "activity";

async function control(name: string, args: Record<string, unknown> = {}) {
  const r = await api.post<{ ok: boolean; result?: string; error?: string }>(`/api/control/${name}`, args);
  if (!r.ok) throw new Error(r.error ?? "Failed");
  return r.result ?? "";
}

function urlB64ToBytes(b64: string) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** The ORION phone app (installable PWA). Talks to the PC through the paired phone server. */
export function PhoneApp() {
  const s = useStore();
  const [tab, setTab] = useState<Tab>("chat");
  const pendingCount = s.approvals.length;

  useEffect(() => {
    // Register the service worker (offline shell + push) and give it our device token.
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("./sw.js").then(async (reg) => {
        const sw = reg.active ?? (await navigator.serviceWorker.ready).active;
        sw?.postMessage({ type: "token", token: TOKEN });
      }).catch(() => {});
    }
    const last = localStorage.getItem("orion.remoteConv");
    if (last) s.openConversation(last).catch(() => localStorage.removeItem("orion.remoteConv"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (s.activeId) localStorage.setItem("orion.remoteConv", s.activeId); }, [s.activeId]);
  useEffect(() => { if (pendingCount) setTab((t) => (t === "screen" ? t : "activity")); }, [pendingCount]);

  return (
    <div className="phone">
      <header className="phone-head">
        <Orb size={28} state={s.voice.state === "unavailable" ? "standby" : s.voice.state} />
        <div className="grow">
          <div className="brand-name">ORION</div>
          <div className="brand-sub"><span className={`dot ${s.connected ? "ok" : "warn pulse"}`} /> {s.connected ? "Connected" : "Reconnecting…"}</div>
        </div>
        {tab === "chat" && (
          <button className="icon-btn" onClick={() => s.openConversation(null)} aria-label="New conversation"><Plus size={19} /></button>
        )}
      </header>

      <main className="phone-body">
        {tab === "chat" && <ChatTab />}
        {tab === "control" && <ControlTab />}
        {tab === "screen" && <ScreenTab />}
        {tab === "activity" && <ActivityTab />}
      </main>

      <nav className="phone-tabs" role="tablist">
        {([["chat", Zap, "ORION"], ["control", Play, "Control"], ["screen", Monitor, "Screen"], ["activity", Activity, "Activity"]] as const).map(([id, Icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
            <Icon size={20} />
            <span>{label}</span>
            {id === "activity" && pendingCount > 0 && <i className="tab-badge">{pendingCount}</i>}
          </button>
        ))}
      </nav>
    </div>
  );
}

// ── Chat + push-to-talk ─────────────────────────────────────────────────────
function ChatTab() {
  const s = useStore();
  const scroller = useRef<HTMLDivElement>(null);
  const rec = useRef<WavRecorder | null>(null);
  const [recording, setRecording] = useState(false);
  const [heard, setHeard] = useState("");
  const [voiceErr, setVoiceErr] = useState("");
  const speakNext = useRef(false);
  const busy = useMemo(() => s.messages.some((m) => m.streaming) ||
    Object.values(s.tasks).some((t) => t.conv_id === s.activeId && (t.status === "running" || t.status === "waiting")),
  [s.messages, s.tasks, s.activeId]);

  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; });

  // Speak ORION's answer aloud when the question was spoken.
  const last = s.messages[s.messages.length - 1];
  useEffect(() => {
    if (speakNext.current && last?.role === "assistant" && !last.streaming && last.content) {
      speakNext.current = false;
      speak(last.content);
    }
  }, [last?.id, last?.streaming, last?.content, last?.role]);

  const startTalk = async () => {
    setVoiceErr("");
    if (!WavRecorder.supported()) {
      setVoiceErr("Voice needs a secure (https) connection — use Tailscale or the instant link in ORION → Settings → Phone app.");
      return;
    }
    try {
      speechSynthesis?.cancel();
      rec.current = new WavRecorder();
      await rec.current.start();
      setRecording(true);
      navigator.vibrate?.(15);
    } catch (e) {
      setVoiceErr(`Microphone blocked: ${(e as Error).message}`);
    }
  };
  const stopTalk = async () => {
    if (!rec.current || !recording) return;
    setRecording(false);
    const wav = await rec.current.stop();
    rec.current = null;
    try {
      const r = await api.post<{ text: string; conv_id: string }>("/api/phone/voice", { wav_b64: await blobToB64(wav), conv_id: s.activeId });
      setHeard(r.text);
      if (!r.text) { setVoiceErr("Didn't catch that — try again."); return; }
      speakNext.current = true;
      if (r.conv_id !== s.activeId) await s.openConversation(r.conv_id);
    } catch (e) {
      setVoiceErr((e as Error).message);
    }
  };

  return (
    <div className="phone-chat">
      <div className="phone-scroll" ref={scroller}>
        <div className="chat-inner" style={{ padding: "14px 14px 20px" }}>
          {s.messages.length === 0 && (
            <div className="phone-empty">
              <Orb size={110} state={recording ? "listening" : "standby"} />
              <p>Hold the mic and talk, or type. ORION works on your PC while you're away.</p>
            </div>
          )}
          {s.messages.map((m) => <MessageView key={m.id} m={m} />)}
          {s.approvals.map((a) => <ApprovalCard key={a.id} a={a} />)}
        </div>
      </div>
      {(heard || voiceErr) && <div className={`phone-heard ${voiceErr ? "err" : ""}`}>{voiceErr || `“${heard}”`}</div>}
      <div className="phone-compose">
        <div className="grow"><Composer busy={busy} remote /></div>
        <button
          className={`talk-btn ${recording ? "rec" : ""}`}
          onPointerDown={(e) => { e.preventDefault(); startTalk(); }}
          onPointerUp={stopTalk} onPointerLeave={stopTalk} onPointerCancel={stopTalk}
          onContextMenu={(e) => e.preventDefault()}
          aria-label="Hold to talk"
        >
          <Mic size={22} />
          {recording && <TalkLevel />}
        </button>
      </div>
    </div>
  );
}

function TalkLevel() {
  const [l, setL] = useState(0);
  useEffect(() => { const t = setInterval(() => setL(levels.mic), 80); return () => clearInterval(t); }, []);
  return <span className="talk-ring" style={{ transform: `scale(${1 + l * 0.6})` }} />;
}

// ── Control ─────────────────────────────────────────────────────────────────
const APPS = ["Spotify", "Chrome", "Discord", "Steam", "Notepad", "File Explorer", "Calculator", "Settings"];

function ControlTab() {
  const toast = useStore((st) => st.toast);
  const [vol, setVol] = useState(50);
  const [routines, setRoutines] = useState<Routine[]>([]);
  const volTimer = useRef<number>();
  useEffect(() => { api.get<Routine[]>("/api/routines").then((r) => setRoutines(r.filter((x) => x.kind === "routine"))).catch(() => {}); }, []);

  const run = async (name: string, args: Record<string, unknown> = {}, ok?: string) => {
    navigator.vibrate?.(10);
    try {
      const res = await control(name, args);
      toast({ title: ok ?? "Done", body: res.length < 120 ? res : undefined, tone: "success" });
    } catch (e) {
      toast({ title: "Couldn't do that", body: (e as Error).message, tone: "error" });
    }
  };
  const confirmRun = (msg: string, name: string, args: Record<string, unknown>) => {
    if (window.confirm(msg)) run(name, args);
  };

  return (
    <div className="phone-page">
      <section className="ctl-card">
        <div className="ctl-title">Media</div>
        <div className="ctl-row">
          <button className="ctl-btn" onClick={() => run("media_control", { action: "previous" }, "Previous")}><SkipBack size={22} /></button>
          <button className="ctl-btn big" onClick={() => run("media_control", { action: "play_pause" }, "Play/pause")}><Pause size={26} /></button>
          <button className="ctl-btn" onClick={() => run("media_control", { action: "next" }, "Next")}><SkipForward size={22} /></button>
        </div>
        <div className="ctl-row" style={{ gap: 12 }}>
          <button className="icon-btn" onClick={() => run("mute", { state: "toggle" }, "Mute toggled")}><VolumeX size={20} /></button>
          <input type="range" min={0} max={100} value={vol} className="slider" aria-label="Volume"
            onChange={(e) => {
              const v = Number(e.target.value);
              setVol(v);
              window.clearTimeout(volTimer.current);
              volTimer.current = window.setTimeout(() => control("set_volume", { level: v }).catch((err) => toast({ title: "Volume", body: err.message, tone: "error" })), 250);
            }} />
          <Volume2 size={20} className="muted" />
        </div>
      </section>

      <section className="ctl-card">
        <div className="ctl-title">Open on PC</div>
        <div className="ctl-grid">
          {APPS.map((a) => <button key={a} className="btn" onClick={() => run("open_app", { name: a }, `Opened ${a}`)}>{a}</button>)}
        </div>
      </section>

      {routines.length > 0 && (
        <section className="ctl-card">
          <div className="ctl-title">Routines</div>
          <div className="ctl-grid">
            {routines.map((r) => <button key={r.id} className="btn" onClick={() => api.post(`/api/routines/${r.id}/run`).then(() => toast({ title: `Running ${r.name}`, tone: "success" }))}>{r.name}</button>)}
          </div>
        </section>
      )}

      <section className="ctl-card">
        <div className="ctl-title">PC</div>
        <div className="ctl-grid">
          <button className="btn" onClick={() => run("lock_pc", {}, "PC locked")}><Lock size={15} /> Lock</button>
          <button className="btn" onClick={() => run("take_screenshot", {}, "Screenshot saved")}><MonitorUp size={15} /> Screenshot</button>
          <button className="btn" onClick={() => confirmRun("Put the PC to sleep?", "power", { action: "sleep", delay_seconds: 0 })}><Moon size={15} /> Sleep</button>
          <button className="btn danger" onClick={() => confirmRun("Shut down the PC in 30 seconds?", "power", { action: "shutdown", delay_seconds: 30 })}><Power size={15} /> Shut down</button>
          <button className="btn" onClick={() => run("cancel_shutdown", {}, "Shutdown cancelled")}>Cancel shutdown</button>
          <button className="btn" onClick={() => run("system_status", {}, "PC status")}><Activity size={15} /> Status</button>
        </div>
      </section>
    </div>
  );
}

// ── Screen: live view + tap to click + remote keyboard ──────────────────────
function ScreenTab() {
  const toast = useStore((st) => st.toast);
  const [src, setSrc] = useState("");
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [live, setLive] = useState(true);
  const [tapClick, setTapClick] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const timer = useRef<number>();

  const refresh = async () => {
    try {
      const r = await fetch(`/api/screen?w=1280`, { headers: { "x-orion-token": TOKEN } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
      const dims = (r.headers.get("x-screen-size") ?? "0x0").split("x").map(Number) as [number, number];
      const url = URL.createObjectURL(await r.blob());
      setSrc((old) => { if (old) URL.revokeObjectURL(old); return url; });
      setSize(dims);
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  useEffect(() => {
    refresh();
    if (live) timer.current = window.setInterval(refresh, 1500);
    return () => window.clearInterval(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  const onTap = async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!tapClick || !size[0]) return;
    const rect = e.currentTarget.getBoundingClientRect();
    // Send the tap as a fraction of the screen; the PC converts it to real pixels.
    const fx = (e.clientX - rect.left) / rect.width, fy = (e.clientY - rect.top) / rect.height;
    try {
      await control("mouse_click", { x: fx, y: fy, relative: true });
      navigator.vibrate?.(10);
      setTimeout(refresh, 400);
    } catch (er) {
      toast({ title: "Click failed", body: (er as Error).message, tone: "error" });
    }
  };

  const key = (k: string) => control("press_keys", { keys: k }).then(() => setTimeout(refresh, 300)).catch((er) => toast({ title: "Key failed", body: er.message, tone: "error" }));

  return (
    <div className="phone-page">
      <div className="screen-bar">
        <button className={`btn sm ${live ? "primary" : ""}`} onClick={() => setLive(!live)}>{live ? <><Square size={11} /> Live</> : <><Play size={11} /> Paused</>}</button>
        <button className="btn sm" onClick={refresh}><RefreshCw size={12} /></button>
        <span className="grow" />
        <button className={`btn sm ${tapClick ? "primary" : ""}`} onClick={() => setTapClick(!tapClick)}><MousePointerClick size={12} /> Tap to click</button>
      </div>
      {err ? <div className="empty">{err}</div> : src
        ? <img src={src} className={`screen-img ${tapClick ? "clickable" : ""}`} onClick={onTap} alt="PC screen" />
        : <div className="empty">Loading screen…</div>}
      <div className="ctl-card">
        <div className="ctl-title"><Keyboard size={13} /> Keyboard</div>
        <div className="ctl-row" style={{ gap: 8 }}>
          <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type on the PC…" />
          <button className="btn primary" disabled={!text} onClick={() => control("type_text", { text }).then(() => { setText(""); setTimeout(refresh, 300); })}>Send</button>
        </div>
        <div className="ctl-grid" style={{ marginTop: 8 }}>
          {["enter", "esc", "tab", "backspace", "alt+tab", "win", "ctrl+c", "ctrl+v"].map((k) => <button key={k} className="btn sm" onClick={() => key(k)}>{k}</button>)}
          <button className="btn sm" onClick={() => control("scroll", { amount: 5 }).then(refresh)}>scroll ↑</button>
          <button className="btn sm" onClick={() => control("scroll", { amount: -5 }).then(refresh)}>scroll ↓</button>
        </div>
      </div>
    </div>
  );
}

// ── Activity: approvals, tasks, notifications ───────────────────────────────
function ActivityTab() {
  const s = useStore();
  const [push, setPush] = useState<"on" | "off" | "unsupported" | "denied">("off");
  useEffect(() => {
    if (!("Notification" in window) || !("serviceWorker" in navigator) || !window.isSecureContext) setPush("unsupported");
    else if (Notification.permission === "denied") setPush("denied");
    else navigator.serviceWorker.ready.then((r) => r.pushManager.getSubscription()).then((sub) => setPush(sub ? "on" : "off")).catch(() => {});
  }, []);

  const enablePush = async () => {
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { setPush("denied"); return; }
      const reg = await navigator.serviceWorker.ready;
      const { key } = await api.get<{ key: string }>("/api/push/key");
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToBytes(key) });
      await api.post("/api/push/subscribe", { subscription: sub.toJSON() });
      setPush("on");
      s.toast({ title: "Notifications on", body: "ORION will alert you here — even when the app is closed.", tone: "success" });
    } catch (e) {
      s.toast({ title: "Couldn't enable notifications", body: (e as Error).message, tone: "error" });
    }
  };

  const tasks = Object.values(s.tasks).sort((a, b) => b.updated - a.updated).slice(0, 12);
  return (
    <div className="phone-page">
      {s.approvals.length > 0 && (
        <section>
          <div className="ctl-title">Waiting for you</div>
          <div className="list">{s.approvals.map((a) => <ApprovalCard key={a.id} a={a} />)}</div>
        </section>
      )}
      <section className="ctl-card">
        <div className="ctl-row" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="ctl-title" style={{ margin: 0 }}>{push === "on" ? <Bell size={13} /> : <BellOff size={13} />} Notifications</div>
            <div className="muted" style={{ fontSize: 12.5 }}>
              {push === "on" ? "On — approvals, reminders and alerts arrive on this phone."
                : push === "unsupported" ? "Needs the secure link (Tailscale or instant link). On iPhone, add ORION to your Home Screen first."
                : push === "denied" ? "Blocked in your phone's settings for this site." : "Get approvals, reminders and alerts here."}
            </div>
          </div>
          {push === "off" && <button className="btn primary" onClick={enablePush}>Enable</button>}
        </div>
      </section>
      <section>
        <div className="ctl-title">Recent tasks</div>
        {tasks.length === 0 ? <div className="empty">Nothing yet.</div> : (
          <div className="list">
            {tasks.map((t) => (
              <div key={t.id} className="item" style={{ flexDirection: "column", alignItems: "stretch", gap: 4 }}>
                <div className="row"><span className="grow ellipsis t">{t.title}</span><TaskStatus task={t} /></div>
                {(t.status === "running" || t.status === "waiting") && <><PlanList task={t} /><StepList steps={t.steps.slice(-5)} showResults={false} /></>}
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="ctl-card">
        <div className="ctl-title"><Smartphone size={13} /> This phone</div>
        <div className="muted" style={{ fontSize: 12.5, marginBottom: 8 }}>Paired with your PC. Remove it any time in ORION → Settings → Phone app.</div>
        <button className="btn sm" onClick={() => { if (confirm("Unpair this phone?")) { localStorage.removeItem("orion.device"); location.reload(); } }}>
          <ChevronLeft size={12} /> Unpair
        </button>
      </section>
    </div>
  );
}

/** First open from the QR code: exchange the one-time code for this phone's own token. */
export function PairView({ code }: { code: string }) {
  const [err, setErr] = useState("");
  useEffect(() => {
    const name = /iphone|ipad/i.test(navigator.userAgent) ? "iPhone" : /android/i.test(navigator.userAgent) ? "Android phone" : "Phone";
    api.post<{ token: string }>("/api/pair", { code, name })
      .then((r) => { localStorage.setItem("orion.device", r.token); location.replace(`${location.pathname}#/remote`); location.reload(); })
      .catch((e) => setErr((e as Error).message));
  }, [code]);
  return (
    <div className="onb"><div className="onb-card">
      <Orb size={120} state={err ? "error" : "connecting"} />
      <h1>{err ? "Pairing failed" : "Pairing with your PC…"}</h1>
      <p>{err || "One moment."}</p>
      {!err && <p className="muted" style={{ fontSize: 13 }}>Tip: after this, use your browser's “Add to Home Screen” to install ORION as an app.</p>}
    </div></div>
  );
}
