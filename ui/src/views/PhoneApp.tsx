import {
  Activity, Bell, BellOff, Calendar, ChevronLeft, Clock, Expand, Hand, History, Keyboard, Lock, Mail, Maximize, Mic, Minimize2,
  Monitor, MonitorUp, MousePointerClick, Moon, Music, Pause, Play, Plus, Power, RefreshCw, RotateCcw, RotateCw, SkipBack, SkipForward,
  Smartphone, Volume2, VolumeX, X, Zap, ZoomIn,
} from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Composer } from "../components/Composer";
import { Logo } from "../components/Logo";
import { Orb } from "../components/Orb";
import { PlanList, StepList, TaskStatus } from "../components/TaskCard";
import { timeAgo } from "../components/ui";
import { api, TOKEN } from "../lib/api";
import { blobToB64, speak, WavRecorder } from "../lib/recorder";
import { levels, useStore } from "../lib/store";
import type { Routine } from "../lib/types";
import { MessageView } from "./ChatView";
import { MissionCard, useMissions } from "./MissionsView";

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

const buzz = (ms = 10) => navigator.vibrate?.(ms);

// ── Bottom sheet + confirm (replaces window.confirm, which looks out of place on a phone) ──
function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="sheet-back" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grab" />
        {title && <div className="sheet-title">{title}</div>}
        {children}
      </div>
    </div>
  );
}

interface Ask { msg: string; detail?: string; ok: string; danger?: boolean; resolve: (v: boolean) => void }

function useConfirm() {
  const [q, setQ] = useState<Ask | null>(null);
  const ask = useCallback((msg: string, opts: { detail?: string; ok?: string; danger?: boolean } = {}) =>
    new Promise<boolean>((resolve) => setQ({ msg, ok: "Confirm", ...opts, resolve })), []);
  const close = (v: boolean) => { q?.resolve(v); setQ(null); };
  const ui = (
    <Sheet open={!!q} onClose={() => close(false)} title={q?.msg}>
      {q?.detail && <p className="sheet-text">{q.detail}</p>}
      <div className="sheet-actions">
        <button className={`btn lg ${q?.danger ? "danger-solid" : "primary"}`} onClick={() => close(true)}>{q?.ok}</button>
        <button className="btn lg" onClick={() => close(false)}>Cancel</button>
      </div>
    </Sheet>
  );
  return [ask, ui] as const;
}

/** The ORION phone app (installable PWA). Talks to the PC through the paired phone server. */
export function PhoneApp() {
  const s = useStore();
  const [tab, setTab] = useState<Tab>("chat");
  const [history, setHistory] = useState(false);
  const pendingCount = s.approvals.length;
  const running = Object.values(s.tasks).filter((t) => t.status === "running" || t.status === "waiting").length;

  useEffect(() => {
    document.documentElement.classList.add("is-phone");
    // Register the service worker (offline shell + push) and give it our device token.
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("./sw.js").then(async (reg) => {
        const sw = reg.active ?? (await navigator.serviceWorker.ready).active;
        sw?.postMessage({ type: "token", token: TOKEN });
      }).catch(() => {});
    }
    const last = localStorage.getItem("orion.remoteConv");
    if (last) s.openConversation(last).catch(() => localStorage.removeItem("orion.remoteConv"));
    return () => document.documentElement.classList.remove("is-phone");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { if (s.activeId) localStorage.setItem("orion.remoteConv", s.activeId); }, [s.activeId]);
  useEffect(() => { if (pendingCount) setTab((t) => (t === "screen" || t === "chat" ? t : "activity")); }, [pendingCount]);

  const conv = s.conversations.find((c) => c.id === s.activeId);
  const titles: Record<Tab, string> = { chat: conv?.title ?? "New chat", control: "Control", screen: "Screen", activity: "Activity" };

  return (
    <div className={`phone tab-${tab}`}>
      <header className="phone-head">
        <Logo size={34} state={s.connected ? (s.voice.state === "unavailable" ? "standby" : s.voice.state) : "connecting"} />
        <div className="grow phone-head-text">
          <div className="phone-head-title ellipsis">{titles[tab]}</div>
          <div className="phone-head-sub"><span className={`dot ${s.connected ? "ok" : "warn pulse"}`} /> {s.connected ? "ORION · connected to your PC" : "Reconnecting…"}</div>
        </div>
        {tab === "chat" && <>
          <button className="head-btn" onClick={() => setHistory(true)} aria-label="Conversations"><History size={20} /></button>
          <button className="head-btn" onClick={() => s.openConversation(null)} aria-label="New conversation"><Plus size={21} /></button>
        </>}
      </header>

      <main className="phone-body">
        {tab === "chat" && <ChatTab />}
        {tab === "control" && <ControlTab />}
        {tab === "screen" && <ScreenTab />}
        {tab === "activity" && <ActivityTab />}
      </main>

      <nav className="phone-tabs" role="tablist">
        {([["chat", Zap, "ORION"], ["control", Play, "Control"], ["screen", Monitor, "Screen"], ["activity", Activity, "Activity"]] as const).map(([id, Icon, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => { buzz(5); setTab(id); }}>
            <span className="tab-ico"><Icon size={21} />
              {id === "activity" && pendingCount > 0 && <i className="tab-badge">{pendingCount}</i>}
              {id === "activity" && !pendingCount && running > 0 && <i className="tab-dot" />}
            </span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <Sheet open={history} onClose={() => setHistory(false)} title="Conversations">
        <button className="btn lg primary" style={{ width: "100%" }} onClick={() => { s.openConversation(null); setHistory(false); }}><Plus size={16} /> New conversation</button>
        <div className="sheet-list">
          {s.conversations.length === 0 && <div className="muted" style={{ padding: 12, textAlign: "center" }}>No conversations yet.</div>}
          {[...s.conversations].sort((a, b) => b.updated - a.updated).slice(0, 40).map((c) => (
            <button key={c.id} className={`sheet-row ${c.id === s.activeId ? "on" : ""}`}
              onClick={() => { s.openConversation(c.id); setHistory(false); }}>
              <span className="grow ellipsis">{c.title || "Untitled"}</span>
              <span className="muted">{timeAgo(c.updated)}</span>
            </button>
          ))}
        </div>
      </Sheet>
    </div>
  );
}

// ── Chat + push-to-talk ─────────────────────────────────────────────────────
const SUGGESTIONS: [typeof Calendar, string][] = [
  [Calendar, "What's on my calendar today?"],
  [Mail, "Show my most important emails"],
  [Music, "Play some music on my PC"],
  [Clock, "Remind me in 30 minutes to stretch"],
];

function ChatTab() {
  const s = useStore();
  const scroller = useRef<HTMLDivElement>(null);
  const rec = useRef<WavRecorder | null>(null);
  const [recording, setRecording] = useState(false);
  const [heard, setHeard] = useState("");
  const [voiceErr, setVoiceErr] = useState("");
  const speakNext = useRef(false);
  const busy = useMemo(() => s.messages.some((m) => m.streaming) ||
    (!!s.activeId && Object.values(s.tasks).some((t) => t.conv_id === s.activeId && (t.status === "running" || t.status === "waiting"))),
  [s.messages, s.tasks, s.activeId]);

  // Follow new messages; an empty chat stays at the top so the greeting isn't cut off on short screens.
  useEffect(() => { const el = scroller.current; if (el) el.scrollTop = s.messages.length ? el.scrollHeight : 0; });

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
      buzz(15);
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
        <div className="chat-inner phone-chat-inner">
          {s.messages.length === 0 && (
            <div className="phone-empty">
              <Orb size={72} state={recording ? "listening" : "standby"} />
              <h2>What can I do for you?</h2>
              <p>Hold the mic to talk, or type. ORION works on your PC while you're away.</p>
              <div className="suggest">
                {SUGGESTIONS.map(([Icon, text]) => (
                  <button key={text} className="suggest-chip" onClick={() => { buzz(); s.send(text).catch((e) => s.toast({ title: "Couldn't send", body: (e as Error).message, tone: "error" })); }}>
                    <Icon size={16} /><span>{text}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {s.messages.map((m) => <MessageView key={m.id} m={m} />)}
          {s.approvals.map((a) => <ApprovalCard key={a.id} a={a} />)}
        </div>
      </div>
      {recording && <div className="phone-heard rec"><span className="rec-dot" /> Listening… release to send</div>}
      {!recording && (heard || voiceErr) && (
        <div className={`phone-heard ${voiceErr ? "err" : ""}`}>
          <span className="grow">{voiceErr || `“${heard}”`}</span>
          <button className="icon-btn sm" aria-label="Dismiss" onClick={() => { setHeard(""); setVoiceErr(""); }}><X size={13} /></button>
        </div>
      )}
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
  const [result, setResult] = useState<{ title: string; text: string } | null>(null);
  const [ask, confirmUi] = useConfirm();
  const volTimer = useRef<number>();
  useEffect(() => { api.get<Routine[]>("/api/routines").then((r) => setRoutines(r.filter((x) => x.kind === "routine"))).catch(() => {}); }, []);

  const run = async (name: string, args: Record<string, unknown> = {}, ok?: string) => {
    buzz();
    try {
      const res = await control(name, args);
      // Long answers (like PC status) read better in a sheet than in a toast.
      if (res.length >= 120) setResult({ title: ok ?? "Done", text: res });
      else toast({ title: ok ?? "Done", body: res || undefined, tone: "success" });
    } catch (e) {
      toast({ title: "Couldn't do that", body: (e as Error).message, tone: "error" });
    }
  };
  const confirmRun = async (msg: string, detail: string, okLabel: string, name: string, args: Record<string, unknown>, danger = false) => {
    if (await ask(msg, { detail, ok: okLabel, danger })) run(name, args);
  };

  return (
    <div className="phone-page">
      <section className="ctl-card">
        <div className="ctl-title">Media</div>
        <div className="ctl-row media-row">
          <button className="ctl-btn" aria-label="Previous" onClick={() => run("media_control", { action: "previous" }, "Previous")}><SkipBack size={22} /></button>
          <button className="ctl-btn big" aria-label="Play or pause" onClick={() => run("media_control", { action: "play_pause" }, "Play/pause")}><Play size={18} /><Pause size={18} /></button>
          <button className="ctl-btn" aria-label="Next" onClick={() => run("media_control", { action: "next" }, "Next")}><SkipForward size={22} /></button>
        </div>
        <div className="vol-row">
          <button className="ctl-btn sm" aria-label="Mute" onClick={() => run("mute", { state: "toggle" }, "Mute toggled")}><VolumeX size={19} /></button>
          <input type="range" min={0} max={100} value={vol} className="slider" aria-label="Volume"
            onChange={(e) => {
              const v = Number(e.target.value);
              setVol(v);
              window.clearTimeout(volTimer.current);
              volTimer.current = window.setTimeout(() => control("set_volume", { level: v }).catch((err) => toast({ title: "Volume", body: err.message, tone: "error" })), 250);
            }} />
          <span className="vol-num"><Volume2 size={15} /> {vol}</span>
        </div>
      </section>

      <HomeCard />

      {routines.length > 0 && (
        <section className="ctl-card">
          <div className="ctl-title">Routines</div>
          <div className="ctl-grid">
            {routines.map((r) => <button key={r.id} className="ptile" onClick={() => { buzz(); api.post(`/api/routines/${r.id}/run`).then(() => toast({ title: `Running ${r.name}`, tone: "success" })).catch((e) => toast({ title: r.name, body: e.message, tone: "error" })); }}><Zap size={16} /> <span className="ellipsis">{r.name}</span></button>)}
          </div>
        </section>
      )}

      <section className="ctl-card">
        <div className="ctl-title">Open on PC</div>
        <div className="ctl-grid">
          {APPS.map((a) => <button key={a} className="ptile" onClick={() => run("open_app", { name: a }, `Opened ${a}`)}><span className="ptile-letter">{a[0]}</span><span className="ellipsis">{a}</span></button>)}
        </div>
      </section>

      <section className="ctl-card">
        <div className="ctl-title">PC</div>
        <div className="ctl-grid">
          <button className="ptile" onClick={() => run("lock_pc", {}, "PC locked")}><Lock size={17} /> Lock</button>
          <button className="ptile" onClick={() => run("take_screenshot", {}, "Screenshot saved")}><MonitorUp size={17} /> Screenshot</button>
          <button className="ptile" onClick={() => run("system_status", {}, "PC status")}><Activity size={17} /> Status</button>
          <button className="ptile" onClick={() => confirmRun("Put the PC to sleep?", "Anything running on the PC pauses until it wakes.", "Sleep", "power", { action: "sleep", delay_seconds: 0 })}><Moon size={17} /> Sleep</button>
          <button className="ptile danger" onClick={() => confirmRun("Shut down the PC?", "It shuts down in 30 seconds. You can still cancel from here.", "Shut down", "power", { action: "shutdown", delay_seconds: 30 }, true)}><Power size={17} /> Shut down</button>
          <button className="ptile" onClick={() => run("cancel_shutdown", {}, "Shutdown cancelled")}><X size={17} /> Cancel shutdown</button>
        </div>
      </section>

      <Sheet open={!!result} onClose={() => setResult(null)} title={result?.title}>
        <pre className="sheet-pre">{result?.text}</pre>
        <button className="btn lg" style={{ width: "100%" }} onClick={() => setResult(null)}>Close</button>
      </Sheet>
      {confirmUi}
    </div>
  );
}

// ── Screen: live view, full screen, pinch-zoom, tap-to-click, remote keyboard ──
const KEYS = ["enter", "esc", "tab", "backspace", "alt+tab", "win", "ctrl+c", "ctrl+v", "ctrl+z", "alt+f4"];
const MIN_Z = 1, MAX_Z = 5;
interface View { s: number; x: number; y: number }
type FsElement = HTMLElement & { webkitRequestFullscreen?: () => void };
type FsDocument = Document & { webkitFullscreenElement?: Element; webkitExitFullscreen?: () => void; webkitFullscreenEnabled?: boolean };

function canNativeFullscreen() {
  // iPhone Safari has no element full screen (fullscreenEnabled / webkitFullscreenEnabled stay false there).
  const d = document as FsDocument, el = document.documentElement as FsElement;
  return !!((d.fullscreenEnabled && el.requestFullscreen) || (d.webkitFullscreenEnabled && el.webkitRequestFullscreen));
}

const fsElement = () => document.fullscreenElement ?? (document as FsDocument).webkitFullscreenElement ?? null;

function ScreenTab() {
  const toast = useStore((st) => st.toast);
  const [src, setSrc] = useState("");
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [live, setLive] = useState(true);
  const [clickMode, setClickMode] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [full, setFull] = useState<"" | "native" | "page">("");
  const [kbOpen, setKbOpen] = useState(false);
  const [chrome, setChrome] = useState(true);  // overlay controls in full screen
  const [view, setView] = useState<View>({ s: 1, x: 0, y: 0 });
  const [mark, setMark] = useState<{ x: number; y: number; k: number; right?: boolean } | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const img = useRef<HTMLImageElement>(null);
  const viewRef = useRef(view);
  viewRef.current = view;
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      // Ask for enough pixels for this phone's screen (and zoom level), capped by the PC side at 1920.
      const box = stage.current?.clientWidth || 400;
      const want = Math.round(box * Math.min(window.devicePixelRatio || 1, 3) * Math.min(viewRef.current.s, 2));
      const r = await fetch(`/api/screen?w=${Math.max(640, Math.min(1920, want))}`, { headers: { "x-orion-token": TOKEN } });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
      const dims = (r.headers.get("x-screen-size") ?? "0x0").split("x").map(Number) as [number, number];
      const url = URL.createObjectURL(await r.blob());
      setSrc((old) => { if (old) URL.revokeObjectURL(old); return url; });
      setSize(dims);
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      inflight.current = false;
    }
  }, []);

  useEffect(() => {
    refresh();
    if (!live) return;
    // Pause while the app is in the background so the phone and PC don't do needless work.
    const t = window.setInterval(() => { if (!document.hidden) refresh(); }, full ? 1000 : 1500);
    return () => window.clearInterval(t);
  }, [live, full, refresh]);

  // ── Full screen: the real Fullscreen API where the browser has it, otherwise a full-page view (iPhone Safari).
  const enterFull = async () => {
    buzz();
    const el = wrap.current as FsElement | null;
    setChrome(true);
    if (el && canNativeFullscreen()) {
      try {
        if (el.requestFullscreen) await el.requestFullscreen({ navigationUI: "hide" });
        else el.webkitRequestFullscreen?.();
        if (!fsElement()) await new Promise((r) => setTimeout(r, 250));  // the webkit call doesn't return a promise
        if (fsElement()) {
          setFull("native");
          // A PC screen is landscape; lock to it where the phone allows (Android). Rotating by hand works everywhere else.
          await (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> })?.lock?.("landscape").catch(() => {});
          return;
        }
      } catch { /* fall through to the in-page view */ }
    }
    setFull("page");
  };
  const exitFull = useCallback(() => {
    const d = document as FsDocument;
    try { screen.orientation?.unlock?.(); } catch { /* not supported */ }
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else if (d.webkitFullscreenElement) d.webkitExitFullscreen?.();
    setFull("");
    setKbOpen(false);
  }, []);
  useEffect(() => {
    const onChange = () => { if (!fsElement()) setFull((f) => (f === "native" ? "" : f)); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") exitFull(); };
    document.addEventListener("fullscreenchange", onChange);
    document.addEventListener("webkitfullscreenchange", onChange);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      document.removeEventListener("webkitfullscreenchange", onChange);
      window.removeEventListener("keydown", onKey);
    };
  }, [exitFull]);
  // Leaving the tab while in full screen must not leave the phone stuck in it.
  useEffect(() => () => { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); }, []);
  // Zoom resets whenever the layout changes size.
  useEffect(() => { setView({ s: 1, x: 0, y: 0 }); }, [full]);

  // ── Geometry: where a finger lands on the PC screen, as a 0–1 fraction (the image is letterboxed).
  const toScreen = (cx: number, cy: number): [number, number] | null => {
    const im = img.current;
    if (!im || !im.naturalWidth) return null;
    const r = im.getBoundingClientRect();  // includes the zoom transform
    const a = im.naturalWidth / im.naturalHeight;
    let w = r.width, h = r.height;
    if (w / h > a) w = h * a; else h = w / a;
    const fx = (cx - r.left - (r.width - w) / 2) / w, fy = (cy - r.top - (r.height - h) / 2) / h;
    return fx < 0 || fx > 1 || fy < 0 || fy > 1 ? null : [fx, fy];
  };
  const clampView = (v: View): View => {
    const el = stage.current;
    if (!el) return v;
    const W = el.clientWidth, H = el.clientHeight, s = Math.min(MAX_Z, Math.max(MIN_Z, v.s));
    return { s, x: Math.min(0, Math.max(W - W * s, v.x)), y: Math.min(0, Math.max(H - H * s, v.y)) };
  };
  const zoomAt = (s: number, px: number, py: number, from: View = viewRef.current) => {
    // Keep the point under the finger(s) still while zooming.
    const cx = (px - from.x) / from.s, cy = (py - from.y) / from.s;
    setView(clampView({ s, x: px - cx * s, y: py - cy * s }));
  };

  const click = async (cx: number, cy: number, opts: { button?: string; double?: boolean } = {}) => {
    const pt = toScreen(cx, cy);
    if (!pt) return;
    const sr = stage.current!.getBoundingClientRect();
    setMark({ x: cx - sr.left, y: cy - sr.top, k: Date.now(), right: opts.button === "right" });
    buzz(opts.button === "right" ? 25 : 10);
    try {
      await control("mouse_click", { x: pt[0], y: pt[1], relative: true, ...opts });
      setTimeout(refresh, 350);
    } catch (er) {
      toast({ title: "Click failed", body: (er as Error).message, tone: "error" });
    }
  };

  // ── Touch gestures: pinch to zoom, drag to pan, double-tap to zoom (or double-click), hold to right-click.
  const pts = useRef(new Map<number, { x: number; y: number }>());
  const g = useRef<{ x: number; y: number; t: number; v: View; moved: boolean; long: boolean; pinch?: { d: number; v: View; mx: number; my: number } }>();
  const longTimer = useRef<number>();
  const tapTimer = useRef<number>();
  const lastTap = useRef<{ x: number; y: number; t: number } | null>(null);

  const local = (x: number, y: number) => { const r = stage.current!.getBoundingClientRect(); return [x - r.left, y - r.top] as const; };
  const pinchInfo = () => {
    const [a, b] = [...pts.current.values()];
    const [mx, my] = local((a.x + b.x) / 2, (a.y + b.y) / 2);
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, mx, my };
  };

  const onDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    window.clearTimeout(longTimer.current);
    if (pts.current.size === 1) {
      g.current = { x: e.clientX, y: e.clientY, t: Date.now(), v: viewRef.current, moved: false, long: false };
      if (clickMode) {
        const { clientX, clientY } = e;
        longTimer.current = window.setTimeout(() => {
          if (g.current && !g.current.moved) { g.current.long = true; click(clientX, clientY, { button: "right" }); }
        }, 550);
      }
    } else if (pts.current.size === 2 && g.current) {
      g.current.moved = true;
      g.current.pinch = { ...pinchInfo(), v: viewRef.current };
    }
  };
  const onMove = (e: React.PointerEvent) => {
    if (!pts.current.has(e.pointerId) || !g.current) return;
    pts.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const st = g.current;
    if (pts.current.size >= 2 && st.pinch) {
      const p = pinchInfo();
      const s = Math.min(MAX_Z, Math.max(MIN_Z, st.pinch.v.s * (p.d / st.pinch.d)));
      // Zoom around the starting midpoint, then follow the fingers as they move.
      const cx = (st.pinch.mx - st.pinch.v.x) / st.pinch.v.s, cy = (st.pinch.my - st.pinch.v.y) / st.pinch.v.s;
      setView(clampView({ s, x: p.mx - cx * s, y: p.my - cy * s }));
      return;
    }
    const dx = e.clientX - st.x, dy = e.clientY - st.y;
    if (!st.moved && Math.hypot(dx, dy) > 8) { st.moved = true; window.clearTimeout(longTimer.current); }
    if (st.moved && st.v.s > 1) setView(clampView({ s: st.v.s, x: st.v.x + dx, y: st.v.y + dy }));
  };
  const onUp = (e: React.PointerEvent) => {
    pts.current.delete(e.pointerId);
    window.clearTimeout(longTimer.current);
    const st = g.current;
    if (pts.current.size > 0) {
      // One finger lifted after a pinch: carry on panning from where things are now.
      const [rest] = [...pts.current.values()];
      if (st) { st.x = rest.x; st.y = rest.y; st.v = viewRef.current; st.pinch = undefined; }
      return;
    }
    g.current = undefined;
    if (!st || st.moved || st.long || e.type === "pointercancel" || Date.now() - st.t > 400) return;
    const now = Date.now(), { clientX: x, clientY: y } = e;
    const prev = lastTap.current;
    const isDouble = prev && now - prev.t < 300 && Math.hypot(prev.x - x, prev.y - y) < 30;
    lastTap.current = isDouble ? null : { x, y, t: now };
    if (clickMode) {
      // Wait a moment so two quick taps become one double-click on the PC.
      window.clearTimeout(tapTimer.current);
      if (isDouble) click(x, y, { double: true });
      else tapTimer.current = window.setTimeout(() => click(x, y), 260);
    } else if (isDouble) {
      const [lx, ly] = local(x, y);
      zoomAt(viewRef.current.s > 1.05 ? 1 : 2.5, lx, ly);
    } else if (full) {
      setChrome((c) => !c);
    }
  };
  const onWheel = (e: React.WheelEvent) => {
    if (!e.ctrlKey) return;  // trackpad pinch on a laptop browser
    const [lx, ly] = local(e.clientX, e.clientY);
    zoomAt(viewRef.current.s * (e.deltaY < 0 ? 1.1 : 0.9), lx, ly);
  };

  const key = (k: string) => { buzz(); control("press_keys", { keys: k }).then(() => setTimeout(refresh, 300)).catch((er) => toast({ title: "Key failed", body: er.message, tone: "error" })); };
  const scroll = (amount: number) => { buzz(); control("scroll", { amount }).then(refresh).catch((er) => toast({ title: "Scroll failed", body: er.message, tone: "error" })); };
  const sendText = () => { if (!text) return; buzz(); control("type_text", { text }).then(() => { setText(""); setTimeout(refresh, 300); }).catch((er) => toast({ title: "Typing failed", body: er.message, tone: "error" })); };

  const zoomed = view.s > 1.02;
  const aspect = size[0] && size[1] ? `${size[0]} / ${size[1]}` : "16 / 9";
  const keyboard = (
    <div className="kb">
      <form className="kb-row" onSubmit={(e) => { e.preventDefault(); sendText(); }}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Type on the PC…" enterKeyHint="send" autoCapitalize="off" autoCorrect="off" />
        <button className="btn primary lg" type="submit" disabled={!text}>Send</button>
      </form>
      <div className="kb-keys">
        {KEYS.map((k) => <button key={k} className="key" onClick={() => key(k)}>{k}</button>)}
        <button className="key" onClick={() => scroll(5)}>scroll ↑</button>
        <button className="key" onClick={() => scroll(-5)}>scroll ↓</button>
      </div>
    </div>
  );

  return (
    <div className="phone-page screen-page">
      <div ref={wrap} className={`scr ${full ? "scr-full" : ""} ${full === "page" ? "scr-page" : ""} ${chrome ? "" : "scr-bare"} ${full && kbOpen ? "kb-open" : ""}`}>
        {!full && (
          <div className="scr-bar">
            <button className={`chip ${live ? "on" : ""}`} onClick={() => setLive(!live)} aria-pressed={live}>
              {live ? <><span className="live-dot" /> Live</> : <><Pause size={13} /> Paused</>}
            </button>
            <button className="chip icon" onClick={refresh} aria-label="Refresh"><RefreshCw size={15} /></button>
            <span className="grow" />
            <div className="seg" role="group" aria-label="Touch mode">
              <button className={!clickMode ? "on" : ""} onClick={() => setClickMode(false)}><Hand size={14} /> View</button>
              <button className={clickMode ? "on" : ""} onClick={() => setClickMode(true)}><MousePointerClick size={14} /> Click</button>
            </div>
            <button className="chip icon solid" onClick={enterFull} aria-label="Full screen"><Maximize size={16} /></button>
          </div>
        )}

        <div ref={stage} className={`scr-stage ${clickMode ? "clicking" : ""}`} style={full ? undefined : { aspectRatio: aspect }}
          onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onWheel={onWheel}
          onContextMenu={(e) => e.preventDefault()}>
          {src && <img ref={img} src={src} alt="Your PC screen" draggable={false}
            style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})` }} />}
          {!src && !err && <div className="scr-msg"><Monitor size={22} /> Connecting to your screen…</div>}
          {err && <div className="scr-msg"><Monitor size={22} /> {err}<button className="btn sm" onClick={refresh}>Try again</button></div>}
          {mark && <span key={mark.k} className={`tap-mark ${mark.right ? "right" : ""}`} style={{ left: mark.x, top: mark.y }} />}
          {!full && src && <button className="scr-expand" onClick={(e) => { e.stopPropagation(); enterFull(); }} onPointerDown={(e) => e.stopPropagation()} aria-label="Full screen"><Expand size={16} /></button>}
          {!full && zoomed && <button className="scr-zoom" onPointerDown={(e) => e.stopPropagation()} onClick={() => setView({ s: 1, x: 0, y: 0 })}><ZoomIn size={13} /> {view.s.toFixed(1)}× · reset</button>}
        </div>

        {full && (
          <>
            <div className="fs-top">
              <button className="fs-btn" onClick={exitFull} aria-label="Exit full screen"><Minimize2 size={18} /> <span>Exit</span></button>
              <span className="grow" />
              <button className={`fs-btn ${live ? "on" : ""}`} onClick={() => setLive(!live)} aria-label={live ? "Pause live view" : "Resume live view"}>
                {live ? <><span className="live-dot" /> Live</> : <><Pause size={15} /> Paused</>}
              </button>
              {zoomed && <button className="fs-btn" onClick={() => setView({ s: 1, x: 0, y: 0 })} aria-label="Reset zoom"><RotateCcw size={16} /> {view.s.toFixed(1)}×</button>}
              <button className={`fs-btn ${clickMode ? "on" : ""}`} onClick={() => setClickMode(!clickMode)} aria-pressed={clickMode}>
                {clickMode ? <MousePointerClick size={17} /> : <Hand size={17} />} <span>{clickMode ? "Click" : "View"}</span>
              </button>
              <button className={`fs-btn ${kbOpen ? "on" : ""}`} onClick={() => setKbOpen(!kbOpen)} aria-label="Keyboard"><Keyboard size={18} /></button>
            </div>
            <span className="fs-rotate"><RotateCw size={15} /> Turn your phone sideways for a bigger view</span>
            {kbOpen && <div className="fs-kb">{keyboard}</div>}
          </>
        )}
      </div>

      {!full && (
        <>
          <p className="scr-hint">
            {clickMode ? "Tap to click · hold for right-click · double-tap to double-click · pinch to zoom"
              : "Pinch or double-tap to zoom · drag to move around · switch to Click to control the PC"}
          </p>
          <section className="ctl-card">
            <div className="ctl-title"><Keyboard size={13} /> Keyboard</div>
            {keyboard}
          </section>
        </>
      )}
    </div>
  );
}

// ── Activity: approvals, tasks, notifications ───────────────────────────────
function ActivityTab() {
  const s = useStore();
  const [push, setPush] = useState<"on" | "off" | "unsupported" | "denied">("off");
  const [ask, confirmUi] = useConfirm();
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
  const unpair = async () => {
    if (await ask("Unpair this phone?", { detail: "You'll need to scan the QR code in ORION → Settings → Phone app to use it again.", ok: "Unpair", danger: true })) {
      localStorage.removeItem("orion.device");
      location.reload();
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
        <div className="notif-row">
          <span className="notif-ico">{push === "on" ? <Bell size={18} /> : <BellOff size={18} />}</span>
          <div className="grow">
            <div className="t">Notifications</div>
            <div className="muted" style={{ fontSize: 13 }}>
              {push === "on" ? "On. Approvals, reminders and alerts arrive on this phone."
                : push === "unsupported" ? "Needs the secure link (Tailscale or instant link). On iPhone, add ORION to your Home Screen first."
                : push === "denied" ? "Blocked in your phone's settings for this site." : "Get approvals, reminders and alerts here."}
            </div>
          </div>
          {push === "off" && <button className="btn primary lg" onClick={enablePush}>Enable</button>}
        </div>
      </section>
      <AlertsAndMissions />
      <section>
        <div className="ctl-title">Recent tasks</div>
        {tasks.length === 0 ? <div className="empty">Nothing yet.</div> : (
          <div className="list">
            {tasks.map((t) => (
              <div key={t.id} className="item task-item">
                <div className="row"><span className="grow ellipsis t">{t.title}</span><TaskStatus task={t} /></div>
                {(t.status === "running" || t.status === "waiting") && <><PlanList task={t} /><StepList steps={t.steps.slice(-5)} showResults={false} /></>}
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="ctl-card">
        <div className="ctl-title"><Smartphone size={13} /> This phone</div>
        <div className="muted" style={{ fontSize: 13, marginBottom: 10 }}>Paired with your PC. Remove it any time in ORION → Settings → Phone app.</div>
        <button className="btn lg" onClick={unpair}><ChevronLeft size={14} /> Unpair</button>
      </section>
      {confirmUi}
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

interface HomeDevice { id: string; name: string; type: string; state: string; brightness?: number }

function HomeCard() {
  const toast = useStore((st) => st.toast);
  const [devices, setDevices] = useState<HomeDevice[] | null>(null);
  const load = () => api.get<HomeDevice[]>("/api/home/devices").then(setDevices).catch(() => setDevices(null));
  useEffect(() => { load(); }, []);
  if (!devices) return null;
  const lights = devices.filter((d) => ["light", "switch", "room", "fan", "input_boolean"].includes(d.type)).slice(0, 16);
  const scenes = devices.filter((d) => d.type === "scene").slice(0, 8);
  const sensors = devices.filter((d) => d.type === "sensor" && /°|%/.test(d.state)).slice(0, 4);
  const toggle = async (d: HomeDevice) => {
    buzz();
    try { await control("home_control", { target: d.id, action: d.state === "on" ? "off" : "on" }); setTimeout(load, 500); }
    catch (e) { toast({ title: d.name, body: (e as Error).message, tone: "error" }); }
  };
  return (
    <section className="ctl-card">
      <div className="ctl-title">Home</div>
      {sensors.length > 0 && <div className="muted" style={{ fontSize: 13, marginBottom: 10 }}>{sensors.map((x) => `${x.name}: ${x.state}`).join(" · ")}</div>}
      <div className="ctl-grid">
        {lights.map((d) => (
          <button key={d.id} className={`ptile ${d.state === "on" ? "lit" : ""}`} aria-pressed={d.state === "on"} onClick={() => toggle(d)}>
            <span className="ellipsis grow">{d.name}</span><span className="ptile-state">{d.state === "on" ? "On" : "Off"}</span>
          </button>
        ))}
        {scenes.map((d) => (
          <button key={d.id} className="ptile" onClick={() => { buzz(); control("home_scene", { name: d.name }).then(() => toast({ title: d.name, tone: "success" })).catch((e) => toast({ title: d.name, body: e.message, tone: "error" })); }}>✦ <span className="ellipsis">{d.name}</span></button>
        ))}
      </div>
    </section>
  );
}

function AlertsAndMissions() {
  const s = useStore();
  const missions = useMissions();
  const [alerts, setAlerts] = useState<{ title: string; message: string; ts: number }[]>([]);
  const [goal, setGoal] = useState("");
  useEffect(() => { api.get<typeof alerts>("/api/alerts").then(setAlerts).catch(() => {}); }, [s.bump["alert"]]);
  const start = () => api.post("/api/missions", { goal }).then(() => { setGoal(""); s.toast({ title: "Mission started", tone: "success" }); })
    .catch((e) => s.toast({ title: "Couldn't start mission", body: (e as Error).message, tone: "error" }));
  return (
    <>
      <section className="ctl-card">
        <div className="ctl-title">New mission</div>
        <form className="kb-row" onSubmit={(e) => { e.preventDefault(); if (goal.trim().length >= 8) start(); }}>
          <input className="input" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. research the best budget earbuds" enterKeyHint="go" />
          <button className="btn primary lg" type="submit" disabled={goal.trim().length < 8}>Go</button>
        </form>
      </section>
      {missions.length > 0 && (
        <section>
          <div className="ctl-title">Missions</div>
          <div className="list">{missions.slice(0, 6).map((m) => <MissionCard key={m.id} m={m} compact />)}</div>
        </section>
      )}
      {alerts.length > 0 && (
        <section>
          <div className="ctl-title">Alerts</div>
          <div className="list">{alerts.slice(0, 8).map((a, i) => (
            <div key={i} className="item alert-item">
              <div className="grow"><div className="t">{a.title}</div><div className="d">{a.message}</div></div>
              {a.ts > 0 && <span className="muted" style={{ fontSize: 12 }}>{timeAgo(a.ts)}</span>}
            </div>
          ))}</div>
        </section>
      )}
    </>
  );
}
