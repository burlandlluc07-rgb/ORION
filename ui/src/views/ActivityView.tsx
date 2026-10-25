import { ActivityLog } from "../components/ActivityLog";
import { useStore } from "../lib/store";
import { TasksView } from "./TasksView";

/** One page for everything ORION has done: the action log and the task list (they used to be two sidebar entries). */
export function ActivityView() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const tasks = view === "tasks";
  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>Activity</h2>
          <p>{tasks ? "Running and past tasks, step by step." : "Every action ORION took, newest first: what it did, who approved it and the proof it worked. Kept on this PC only."}</p>
        </div>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={!tasks} className={`tab ${tasks ? "" : "on"}`} onClick={() => setView("activity")}>What ORION did</button>
        <button role="tab" aria-selected={tasks} className={`tab ${tasks ? "on" : ""}`} onClick={() => setView("tasks")}>Tasks</button>
      </div>
      <div key={view} className="tab-body">{tasks ? <TasksView /> : <ActivityLog />}</div>
    </div></div>
  );
}
