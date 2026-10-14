import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { useStore } from "../lib/store";

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={`toggle ${on ? "on" : ""}`}
      onClick={() => onChange(!on)} />
  );
}

export function Modal({ title, onClose, children, footer, wide }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" style={wide ? { width: "min(820px, calc(100vw - 40px))" } : undefined}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><X size={17} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      <div>
        <div className="fl">{label}</div>
        {hint && <div className="fh">{hint}</div>}
      </div>
      <div className="fc">{children}</div>
    </div>
  );
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone ?? ""}`} onClick={() => dismiss(t.id)}>
          <div className="tt">{t.title}</div>
          {t.body && <div className="tb">{t.body}</div>}
        </div>
      ))}
    </div>
  );
}

export function Meter({ label, value, max = 100, suffix = "%", text }: {
  label: string; value: number; max?: number; suffix?: string; text?: string;
}) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const tone = pct > 90 ? "bad" : pct > 75 ? "warn" : "";
  return (
    <div className="meter">
      <div className="meter-top">
        <span>{label}</span>
        <span className="v">{text ?? `${Math.round(value)}${suffix}`}</span>
      </div>
      <div className={`bar ${tone}`}><i style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

export function timeAgo(ts: number): string {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
