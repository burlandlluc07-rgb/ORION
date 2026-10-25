import { useEffect, useMemo, useRef, useState } from "react";
import { Logo } from "../components/Logo";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import { QuickPanel } from "./QuickBar";

/**
 * The notch: a small black pill hanging from the top centre of the screen.
 *
 *   idle  a tiny pill, nothing else. Hover shows how to open it.
 *   peek  grows into a one-line status while ORION listens, works on a task, or just finished one.
 *   open  the ask box with the latest reply. Opens on click or the hotkey, and by itself (without taking focus)
 *         when an approval is waiting, so a risky step is never missed.
 *
 * The page picks the mode and animates the pill; the desktop shell resizes the native window to fit
 * (orion/desktop/notch.py), and on Windows clips the window to the pill's shape. Sizes here match notch.PILLS.
 */
export type NotchMode = "idle" | "peek" | "open";
const ORDER: Record<NotchMode, number> = { idle: 0, peek: 1, open: 2 };
const DONE_MS = 6000;

const VOICE_LABEL: Record<string, string> = {
  listening: "Listening…", thinking: "Thinking…", working: "Working…", speaking: "Speaking", connecting: "Connecting…",
};

function hotkeyLabel(combo?: string) {
  if (!combo) return "";
  return combo.split("+").map((k) => k.replace(/[<>]/g, "")).map((k) => k[0].toUpperCase() + k.slice(1)).join(" ");
}

export interface NotchStatus { title: string; detail?: string; busy: boolean }

/** What the pill should say right now, or null when there's nothing worth showing. */
export function notchStatus(s: ReturnType<typeof useStore.getState>, done: NotchStatus | null): NotchStatus | null {
  const label = VOICE_LABEL[s.voice.state];
  if (label) return { title: label, detail: s.transcript?.text.slice(-90), busy: s.voice.state !== "speaking" && s.voice.state !== "listening" };
  const running = Object.values(s.tasks).filter((t) => t.status === "running" || t.status === "waiting").sort((a, b) => b.updated - a.updated)[0];
  if (running) {
    const step = [...running.steps].reverse().find((x) => x.status === "running") ?? running.steps[running.steps.length - 1];
    return { title: running.title, detail: step?.label, busy: true };
  }
  if (s.messages.some((m) => m.streaming)) return { title: "Thinking…", busy: true };
  return done;
}

export function NotchView() {
  const s = useStore();
  const [userOpen, setUserOpen] = useState(false);
  const [done, setDone] = useState<NotchStatus | null>(null);
  const status = notchStatus(s, done);
  const mode: NotchMode = userOpen || s.approvals.length > 0 ? "open" : status ? "peek" : "idle";
  const [shown, setShown] = useState<NotchMode>(mode);
  const applied = useRef<NotchMode>("idle");

  // "Done · title" for a few seconds after a task finishes, then back to idle.
  const finished = useMemo(() => Object.values(s.tasks).filter((t) => t.status === "done" || t.status === "failed")
    .sort((a, b) => b.updated - a.updated)[0], [s.tasks]);
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!finished || seen.current === null) { seen.current = finished?.id ?? ""; return; }
    if (finished.id === seen.current || Date.now() / 1000 - finished.updated > DONE_MS / 1000) return;
    seen.current = finished.id;
    setDone({ title: `${finished.status === "done" ? "Done" : "Didn't finish"} · ${finished.title}`, busy: false });
    const t = setTimeout(() => setDone(null), DONE_MS);
    return () => clearTimeout(t);
  }, [finished]);

  // Tell the shell first (on Windows it morphs the window's shape natively, growing or shrinking in the right
  // order), then let the CSS spring follow. Growing waits for the bigger window so the pill isn't clipped.
  useEffect(() => {
    if (mode === applied.current) { setShown(mode); return; }
    const grow = ORDER[mode] > ORDER[applied.current];
    applied.current = mode;
    const sent = api.post(`/api/window/notch-${mode}`).catch(() => {});
    if (grow) sent.finally(() => requestAnimationFrame(() => setShown(mode)));
    else setShown(mode);
  }, [mode]);

  // Keyboard focus only while *you* opened it, so an approval popping up never steals your typing.
  useEffect(() => {
    if (userOpen) {
      api.post("/api/window/notch-focus").catch(() => {}).finally(() => setTimeout(() => (window as any).__orionQuickFocus?.(), 60));
    } else {
      api.post("/api/window/notch-blur").catch(() => {});
    }
  }, [userOpen]);

  useEffect(() => {
    (window as any).__orionNotch = (cmd: string) => setUserOpen((o) => (cmd === "toggle" ? !o : cmd === "open"));
    const onBlur = () => setUserOpen(false);  // clicked back into another app
    window.addEventListener("blur", onBlur);
    return () => { window.removeEventListener("blur", onBlur); delete (window as any).__orionNotch; };
  }, []);

  const hotkey = hotkeyLabel(s.engine?.settings.hotkey_quickbar);
  const logoState = status?.busy ? "thinking" : s.voice.state;

  return (
    <div className="notch-root">
      <div className={`notch-pill ${shown}`} data-mode={shown} role={shown === "open" ? "dialog" : "button"}
        aria-label={shown === "open" ? "ORION" : status ? `ORION: ${status.title}` : "Open ORION"}
        tabIndex={shown === "open" ? -1 : 0}
        onClick={() => shown !== "open" && setUserOpen(true)}
        onMouseEnter={() => shown === "idle" && api.post("/api/window/notch-hover").catch(() => {})}
        onMouseLeave={() => shown === "idle" && api.post("/api/window/notch-idle").catch(() => {})}
        onKeyDown={(e) => shown !== "open" && (e.key === "Enter" || e.key === " ") && setUserOpen(true)}>
        {shown === "open" ? (
          <div className="notch-panel">
            <QuickPanel logoSize={26} onHide={() => setUserOpen(false)} onOpenMain={() => api.post("/api/window/notch-open-main")} />
          </div>
        ) : shown === "peek" && status ? (
          <div className="notch-peek" key={status.title}>
            <Logo size={22} state={logoState} />
            <div className="grow">
              <div className="nt">{status.title}</div>
              {status.detail && <div className="nd">{status.detail}</div>}
            </div>
            {status.busy && <span className="notch-dots"><i /><i /><i /></span>}
          </div>
        ) : (
          <div className="notch-idle">
            <Logo size={15} state={s.voice.state} />
            <span className="notch-hint">Ask ORION{hotkey && <kbd>{hotkey}</kbd>}</span>
          </div>
        )}
      </div>
    </div>
  );
}
