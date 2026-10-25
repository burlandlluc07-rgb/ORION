import { MessageSquare } from "lucide-react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { Approval } from "../lib/types";
import { ApprovalCard } from "./ApprovalCard";

/** Approval requests one at a time, in arrival order. The rest wait in a short list below and can be opened
 *  in any order; "Later" moves a request to the back. Nothing here answers a request on its own. */
export function ApprovalQueue({ items, showConversation = false }: { items: Approval[]; showConversation?: boolean }) {
  const s = useStore();
  if (!items.length) return null;
  const front = items.find((a) => a.id === s.approvalFocus) ?? items.find((a) => !a.parked);
  const rest = items.filter((a) => a !== front);
  const conv = front?.task_id ? s.tasks[front.task_id]?.conv_id : null;
  const title = conv ? s.conversations.find((c) => c.id === conv)?.title : undefined;
  const open = (a: Approval) => {
    s.set({ approvalFocus: a.id });
    if (a.parked) api.post(`/api/approvals/${a.id}/park`, { parked: false }).catch(() => {});
  };
  return (
    <div className="ap-queue">
      {front && showConversation && conv && (
        <button className="btn sm ghost ap-conv" onClick={() => s.openConversation(conv)}>
          <MessageSquare size={12} /> {title ?? "Open conversation"}
        </button>
      )}
      {front && <ApprovalCard key={front.id} a={front} position={{ index: items.indexOf(front), total: items.length }} />}
      {rest.length > 0 && (
        <div className="ap-rest" role="list" aria-label="More approvals waiting">
          <div className="ap-rest-title">{front ? `${rest.length} more waiting` : `${rest.length} waiting for later`}</div>
          {rest.map((a) => (
            <button key={a.id} role="listitem" className="ap-row" onClick={() => open(a)}>
              <span className={`ap-dot ${a.risk === "danger" ? "danger" : ""}`} />
              <span className="ap-row-label">{a.label}</span>
              {a.parked && <span className="ap-tag">Later</span>}
              <span className="ap-open">Open</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
