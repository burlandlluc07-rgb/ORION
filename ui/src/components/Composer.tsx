import { ArrowUp, Check, ChevronDown, Mic, MicOff, Paperclip, Rocket, Square, Telescope, X, Zap } from "lucide-react";
import { forwardRef, type ChangeEvent, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, fileToB64 } from "../lib/api";
import { autocorrect, keepWord, undoFix, type Correction } from "../lib/autocorrect";
import { useStore } from "../lib/store";
import type { Skill } from "../lib/types";

export interface ComposerHandle {
  focus: () => void;
  fill: (text: string) => void;
}

const MAX_FILE = 15 * 1024 * 1024;

const MODES = [
  { id: "auto", label: "Auto", icon: Rocket, desc: "ORION picks the model for each request" },
  { id: "fast", label: "Fast", icon: Zap, desc: "Flash-Lite: quickest, biggest free quota" },
  { id: "smart", label: "Deep", icon: Telescope, desc: "Flash: stronger reasoning for harder jobs" },
] as const;

/** Thinking-mode picker that lives in the composer bar, like a model picker. */
function ModePicker() {
  const mode = useStore((s) => s.mode);
  const set = useStore((s) => s.set);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", close); };
  }, [open]);
  const cur = MODES.find((m) => m.id === mode) ?? MODES[0];
  return (
    <div className="mode-wrap" ref={wrap}>
      <button className={`mode-btn ${open ? "open" : ""}`} onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}
        title="Thinking mode">
        <cur.icon size={14} /> {cur.label} <ChevronDown size={13} />
      </button>
      {open && (
        <div className="menu" role="menu" aria-label="Thinking mode">
          {MODES.map((m) => (
            <button key={m.id} role="menuitemradio" aria-checked={mode === m.id} className={`menu-item ${mode === m.id ? "sel" : ""}`}
              onClick={() => { set({ mode: m.id }); setOpen(false); }}>
              <m.icon size={15} />
              <span><div className="mi-t">{m.label}</div><div className="mi-d">{m.desc}</div></span>
              {mode === m.id && <Check size={15} className="mi-check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export const Composer = forwardRef<ComposerHandle, { busy: boolean; remote?: boolean }>(function Composer({ busy, remote = false }, ref) {
  const send = useStore((s) => s.send);
  const stop = useStore((s) => s.stop);
  const toast = useStore((s) => s.toast);
  const voice = useStore((s) => s.voice);
  const bump = useStore((s) => s.bump["ecosystem.changed"]);
  const [text, setText] = useState(() => sessionStorage.getItem("orion.draft") ?? "");
  const [files, setFiles] = useState<File[]>([]);
  const [drag, setDrag] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [sel, setSel] = useState(0);
  const autoOn = useStore((s) => s.engine?.settings?.autocorrect !== false);
  const [fix, setFix] = useState<Correction | null>(null); // the last auto-correction, undoable until you move on
  const fixAt = useRef(0); // caret position right after the fix's boundary character
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    focus: () => ta.current?.focus(),
    fill: (t: string) => {
      setText(t);
      setFix(null);
      setTimeout(() => {
        ta.current?.focus();
        ta.current?.setSelectionRange(t.length, t.length);
      });
    },
  }));

  useEffect(() => {
    api.get<Skill[]>("/api/skills").then((list) => setSkills(list.filter((s) => s.enabled))).catch(() => {});
  }, [bump]);

  useEffect(() => {
    sessionStorage.setItem("orion.draft", text);
    const el = ta.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
    }
  }, [text]);

  // The undo hint fades after a few seconds; Backspace/Ctrl+Z still work until you type on.
  const [fixShown, setFixShown] = useState(false);
  useEffect(() => {
    if (!fix) return;
    setFixShown(true);
    const t = setTimeout(() => setFixShown(false), 4000);
    return () => clearTimeout(t);
  }, [fix]);

  const setCaret = (pos: number) => setTimeout(() => ta.current?.setSelectionRange(pos, pos));

  /** Put back the word ORION corrected. `dropBoundary` also removes the space/punctuation typed after it. */
  const revert = (dropBoundary: boolean) => {
    if (!fix) return false;
    let t = undoFix(text, fix);
    if (t === null) { setFix(null); return false; }
    const end = fix.start + fix.original.length;
    if (dropBoundary) t = t.slice(0, end) + t.slice(end + 1);
    keepWord(fix.original);
    setText(t);
    setFix(null);
    setCaret(dropBoundary ? end : end + 1);
    return true;
  };

  const onType = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    const caret = e.target.selectionEnd ?? next.length;
    setSel(0);
    // Phone keyboards don't send a usable Backspace key event, so spot "the boundary after a fix was deleted" here.
    if (fix && next.length === text.length - 1 && caret === fixAt.current - 1 && text.slice(0, caret) + text.slice(caret + 1) === next) {
      revert(true);
      return;
    }
    const composing = (e.nativeEvent as InputEvent).isComposing;
    const done = autoOn && !composing ? autocorrect(text, next, caret) : null;
    if (done) {
      setText(done.text);
      setFix(done.fix);
      fixAt.current = caret + done.fix.fixed.length - done.fix.original.length;
      setCaret(fixAt.current);
    } else {
      setText(next);
      if (fix) setFix(null);
    }
  };

  const slash = useMemo(() => {
    const m = /^\/([a-z0-9-]*)$/.exec(text);
    if (!m) return [];
    return skills.filter((s) => s.name.startsWith(m[1]));
  }, [text, skills]);

  const addFiles = (list: FileList | File[]) => {
    const ok: File[] = [];
    for (const f of Array.from(list)) {
      if (f.size > MAX_FILE) toast({ title: "File too large", body: `${f.name} is over 15 MB.`, tone: "error" });
      else ok.push(f);
    }
    setFiles((prev) => [...prev, ...ok].slice(0, 8));
  };

  const submit = async () => {
    const t = text.trim();
    if ((!t && !files.length) || busy) return;
    const payload = await Promise.all(files.map(async (f) => ({ name: f.name, mime: f.type || "application/octet-stream", b64: await fileToB64(f) })));
    setText("");
    setFiles([]);
    setFix(null);
    try {
      await send(t, payload);
    } catch (e) {
      toast({ title: "Couldn't send", body: String((e as Error).message), tone: "error" });
      setText(t);
    }
  };

  const voiceLive = ["listening", "speaking", "thinking", "working", "connecting"].includes(voice.state);
  const voiceUnavailable = voice.state === "unavailable" || voice.mode === "off";

  return (
    <div className="composer-wrap">
      <div
        className={`composer ${drag ? "drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
      >
        {slash.length > 0 && (
          <div className="slash-menu" role="listbox">
            {slash.map((s, i) => (
              <div key={s.name} className={`slash-item ${i === sel ? "sel" : ""}`} onMouseDown={(e) => { e.preventDefault(); setText(`/${s.name} `); }}>
                <span className="n">/{s.name}</span>
                <span className="muted ellipsis" style={{ fontSize: 12.5 }}>{s.description}</span>
              </div>
            ))}
          </div>
        )}
        {files.length > 0 && (
          <div className="file-chips">
            {files.map((f, i) => (
              <span key={i} className="file-chip">
                <span className="ellipsis">{f.name}</span>
                <button className="icon-btn sm" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Remove"><X size={12} /></button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={ta}
          value={text}
          rows={1}
          placeholder={voiceLive ? "Listening… or type here" : "How can I help? Type / for a skill"}
          onChange={onType}
          onBlur={() => setFix(null)}
          onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
          onKeyDown={(e) => {
            if (fix) {
              const el = e.currentTarget;
              const atFix = el.selectionStart === el.selectionEnd && el.selectionEnd === fixAt.current;
              if ((e.key === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey) || (e.key === "Backspace" && atFix)) {
                if (revert(e.key === "Backspace")) { e.preventDefault(); return; }
              }
              if (e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End") setFix(null);
            }
            if (slash.length) {
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((sel + 1) % slash.length); return; }
              if (e.key === "ArrowUp") { e.preventDefault(); setSel((sel - 1 + slash.length) % slash.length); return; }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); setText(`/${slash[sel].name} `); return; }
            }
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
        />
        <div className="composer-bar">
          <input ref={fileInput} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
          <button className="icon-btn" aria-label="Attach files" title="Attach files or images" onClick={() => fileInput.current?.click()}><Paperclip size={17} /></button>
          {fix && fixShown && (
            <button className="ac-note" onMouseDown={(e) => e.preventDefault()} onClick={() => revert(false)}
              title="Undo this correction (Backspace or Ctrl+Z)" aria-label={`Corrected ${fix.original} to ${fix.fixed}. Undo`}>
              <span className="ac-old">{fix.original}</span> → {fix.fixed} <span className="ac-undo">Undo</span>
            </button>
          )}
          <span className="grow" />
          {!remote && <ModePicker />}
          {!remote && <button
            className={`mic-btn ${voiceLive ? "live" : ""}`}
            disabled={voiceUnavailable}
            title={voiceUnavailable ? "Voice is off or unavailable — see Settings → Voice" : voiceLive ? "Stop listening (Ctrl+Alt+J)" : "Talk to ORION (Ctrl+Alt+J)"}
            onClick={() => api.post("/api/voice/toggle").catch((e) => toast({ title: "Voice", body: e.message, tone: "error" }))}
          >
            {voiceUnavailable ? <MicOff size={17} /> : <Mic size={17} />}
          </button>}
          {busy ? (
            <button className="send-btn stop" onClick={stop} title="Stop (Esc)"><Square size={14} fill="currentColor" /></button>
          ) : (
            <button className="send-btn" onClick={submit} disabled={!text.trim() && !files.length} title="Send (Enter)"><ArrowUp size={18} /></button>
          )}
        </div>
      </div>
    </div>
  );
});
