import { Clock3, Fingerprint, ShieldAlert, ShieldX } from "lucide-react";
import { useEffect, useState } from "react";
import { api, IS_PHONE } from "../lib/api";
import { biometricWillAsk, sendDecision } from "../lib/biometric";
import { frontApproval, useStore } from "../lib/store";
import type { Approval } from "../lib/types";

// Asked once per page load: does Approve on this phone ask for the fingerprint?
let bioAsk: Promise<boolean> | null = null;

/** One approval request. It waits until answered (unless a time limit is set in Settings); "Later" puts it
 *  back in the queue without answering it. Ctrl+Enter approves only the front request. */
export function ApprovalCard({ a, position }: { a: Approval; position?: { index: number; total: number } }) {
  const [busy, setBusy] = useState(false);
  const toast = useStore((s) => s.toast);
  const voiceOn = useStore((s) => ["listening", "speaking", "thinking", "working"].includes(s.voice.state));
  const replyOn = useStore((s) => s.engine?.settings?.voice_approvals !== false);
  const isFront = useStore((s) => frontApproval(s)?.id === a.id);
  const [bio, setBio] = useState(false);
  const leftOf = () => (a.expires == null ? null : Math.max(0, Math.round(a.expires - Date.now() / 1000)));
  const [left, setLeft] = useState(leftOf);
  useEffect(() => {
    if (a.expires == null) return setLeft(null);
    const t = setInterval(() => setLeft(leftOf()), 1000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.expires]);
  useEffect(() => {
    if (!IS_PHONE) return;
    (bioAsk ??= biometricWillAsk()).then(setBio);
  }, []);

  const decide = async (decision: "approve" | "deny" | "always") => {
    setBusy(true);
    try {
      await sendDecision(`/api/approvals/${a.id}`, `approve:${a.id}`, decision);
    } catch (e) {
      toast({ title: "Not done", body: (e as Error).message, tone: "warn" });
    } finally {
      setBusy(false);
    }
  };
  const park = (parked: boolean) => api.post(`/api/approvals/${a.id}/park`, { parked }).catch(() => {});

  useEffect(() => {
    if (!isFront || IS_PHONE) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key === "Enter") decide("approve");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.id, isFront]);

  const args = Object.entries(a.args);
  return (
    <div className={`approval ${a.risk === "danger" ? "danger" : ""}`} role="alertdialog" aria-label="Approval required">
      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <span className="ap-icon">{a.risk === "danger" ? <ShieldX size={17} /> : <ShieldAlert size={17} />}</span>
        <div className="grow">
          <div className="ap-kicker">
            {position && position.total > 1 && <span className="ap-pos">{position.index + 1} of {position.total} · </span>}
            {a.desktop ? "ORION wants to use your desktop to" : "ORION wants to"}
          </div>
          <div className="what">{a.label}</div>
          {a.desktop && <div className="ap-kicker" style={{ marginTop: 4 }}>Allowed for this one step only. Your mouse, keyboard or active window may be used.</div>}
        </div>
        <span className={`badge ${a.risk}`}>{a.risk === "danger" ? "High risk" : "Needs approval"}</span>
      </div>
      {args.length > 0 && (
        <div className="args">{args.map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join("\n")}</div>
      )}
      <div className="actions">
        <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>
          {bio && <Fingerprint size={14} />}Allow{isFront && !IS_PHONE && <kbd>Ctrl ⏎</kbd>}
        </button>
        {a.risk !== "danger" && !a.desktop && <button className="btn" disabled={busy} onClick={() => decide("always")}>Allow for this session</button>}
        <button className="btn ghost" disabled={busy} onClick={() => decide("deny")}>Deny</button>
        {a.parked
          ? <button className="btn ghost" disabled={busy} onClick={() => park(false)}>Back to front</button>
          : <button className="btn ghost" disabled={busy} title="Keep it waiting and come back to it" onClick={() => park(true)}><Clock3 size={13} /> Later</button>}
        <span className="grow" />
        {replyOn && <span className="timer">{voiceOn ? "or say “yes” / “no”" : "or type “yes” / “no”"}</span>}
        {left == null
          ? <span className="timer" title="It never lapses on its own and is never approved without you">Waits for you</span>
          : <span className="timer" title="Not done if you don't answer in time">{left >= 60 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : `${left}s`}</span>}
      </div>
    </div>
  );
}
