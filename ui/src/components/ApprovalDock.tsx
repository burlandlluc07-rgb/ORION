import { MessageSquare } from "lucide-react";
import { useStore } from "../lib/store";
import { ApprovalCard } from "./ApprovalCard";

/** Pending approvals that aren't visible in the current chat — so a risky step is never stuck unseen. */
export function ApprovalDock() {
  const s = useStore();
  const hidden = s.approvals.filter((a) => {
    const task = a.task_id ? s.tasks[a.task_id] : undefined;
    return s.view !== "chat" || (task?.conv_id && task.conv_id !== s.activeId);
  });
  if (!hidden.length) return null;
  return (
    <div className="approval-dock" role="region" aria-label="Pending approvals">
      {hidden.map((a, i) => {
        const conv = a.task_id ? s.tasks[a.task_id]?.conv_id : null;
        const title = s.conversations.find((c) => c.id === conv)?.title;
        return (
          <div key={a.id}>
            {conv && (
              <button className="btn sm ghost" style={{ marginBottom: 6 }} onClick={() => s.openConversation(conv)}>
                <MessageSquare size={12} /> {title ?? "Open conversation"}
              </button>
            )}
            <ApprovalCard a={a} hotkey={i === hidden.length - 1} />
          </div>
        );
      })}
    </div>
  );
}
