import { useStore } from "../lib/store";
import { ApprovalQueue } from "./ApprovalQueue";

/** Pending approvals that aren't visible in the current chat — so a risky step is never stuck unseen. */
export function ApprovalDock() {
  const s = useStore();
  const hidden = s.approvals.filter((a) => {
    const task = a.task_id ? s.tasks[a.task_id] : undefined;
    return s.view !== "chat" || a.parked || a.id === s.approvalFocus || (task?.conv_id && task.conv_id !== s.activeId);
  });
  if (!hidden.length) return null;
  return (
    <div className="approval-dock" role="region" aria-label="Pending approvals">
      <ApprovalQueue items={hidden} showConversation />
    </div>
  );
}
