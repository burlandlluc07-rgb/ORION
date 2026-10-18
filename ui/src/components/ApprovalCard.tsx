import { ShieldAlert, ShieldX } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Approval } from "../lib/types";

export function ApprovalCard({ a, hotkey = false }: { a: Approval; hotkey?: boolean }) {
  const [busy, setBusy] = useState(false);
  const voiceOn = useStore((s) => ["listening", "speaking", "thinking", "working"].includes(s.voice.state));
  const replyOn = useStore((s) => s.engine?.settings?.voice_approvals !== false);
  const [left, setLeft] = useState(Math.max(0, Math.round(a.expires - Date.now() / 1000)));
  useEffect(() => {
    const t = setInterval(() => setLeft(Math.max(0, Math.round(a.expires - Date.now() / 1000))), 1000);
    return () => clearInterval(t);
  }, [a.expires]);

  const decide = async (decision: "approve" | "deny" | "always") => {
    setBusy(true);
    try {
      await api.post(`/api/approvals/${a.id}`, { decision });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!hotkey) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "Enter") decide("approve");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.id, hotkey]);

  const args = Object.entries(a.args);
  return (
    <div className={`approval ${a.risk === "danger" ? "danger" : ""}`} role="alertdialog" aria-label="Approval required">
      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <span className="ap-icon">{a.risk === "danger" ? <ShieldX size={17} /> : <ShieldAlert size={17} />}</span>
        <div className="grow">
          <div className="ap-kicker">{a.desktop ? "ORION wants to use your desktop to" : "ORION wants to"}</div>
          <div className="what">{a.label}</div>
          {a.desktop && <div className="ap-kicker" style={{ marginTop: 4 }}>Allowed for this one step only. Your mouse, keyboard or active window may be used.</div>}
        </div>
        <span className={`badge ${a.risk}`}>{a.risk === "danger" ? "High risk" : "Needs approval"}</span>
      </div>
      {args.length > 0 && (
        <div className="args">{args.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join("\n")}</div>
      )}
      <div className="actions">
        <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>Allow <kbd>Ctrl ⏎</kbd></button>
        {a.risk !== "danger" && !a.desktop && <button className="btn" disabled={busy} onClick={() => decide("always")}>Allow for this session</button>}
        <button className="btn ghost" disabled={busy} onClick={() => decide("deny")}>Deny</button>
        <span className="grow" />
        {replyOn && <span className="timer">{voiceOn ? "or say “yes” / “no”" : "or type “yes” / “no”"}</span>}
        <span className="timer" title="Not done if you don't answer in time">{left >= 60 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : `${left}s`}</span>
      </div>
    </div>
  );
}
