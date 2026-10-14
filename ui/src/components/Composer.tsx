import { ArrowUp, Mic, MicOff, Paperclip, Square, X } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, fileToB64 } from "../lib/api";
import { useStore } from "../lib/store";
import type { Skill } from "../lib/types";

export interface ComposerHandle {
  focus: () => void;
  fill: (text: string) => void;
}

const MAX_FILE = 15 * 1024 * 1024;

export const Composer = forwardRef<ComposerHandle, { busy: boolean }>(function Composer({ busy }, ref) {
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
  const ta = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    focus: () => ta.current?.focus(),
    fill: (t: string) => {
      setText(t);
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
          placeholder={voiceLive ? "Listening… or type here" : "Ask ORION anything, or type / for skills"}
          onChange={(e) => { setText(e.target.value); setSel(0); }}
          onPaste={(e) => { if (e.clipboardData.files.length) { e.preventDefault(); addFiles(e.clipboardData.files); } }}
          onKeyDown={(e) => {
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
          <button className="icon-btn" title="Attach files or images" onClick={() => fileInput.current?.click()}><Paperclip size={17} /></button>
          <span className="grow" />
          <button
            className={`mic-btn ${voiceLive ? "live" : ""}`}
            disabled={voiceUnavailable}
            title={voiceUnavailable ? "Voice is off or unavailable — see Settings → Voice" : voiceLive ? "Stop listening (Ctrl+Alt+J)" : "Talk to ORION (Ctrl+Alt+J)"}
            onClick={() => api.post("/api/voice/toggle").catch((e) => toast({ title: "Voice", body: e.message, tone: "error" }))}
          >
            {voiceUnavailable ? <MicOff size={17} /> : <Mic size={17} />}
          </button>
          {busy ? (
            <button className="send-btn stop" onClick={stop} title="Stop (Esc)"><Square size={14} fill="currentColor" /></button>
          ) : (
            <button className="send-btn" onClick={submit} disabled={!text.trim() && !files.length} title="Send (Enter)"><ArrowUp size={18} /></button>
          )}
        </div>
      </div>
      <div className="composer-hint">
        Enter to send · Shift+Enter new line · <kbd>/</kbd> skills · Esc stops · risky actions always ask first
      </div>
    </div>
  );
});
