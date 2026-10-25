import { ActivityLog } from "../components/ActivityLog";
import { WeeklyRecap } from "../components/WeeklyRecap";

export function ActivityView() {
  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>What ORION did</h2>
          <p>Your week at a glance, then every action ORION took, newest first: what it did, how it ran, who approved it and the proof it worked. Kept on this PC only.</p>
        </div>
      </div>
      <WeeklyRecap />
      <h3 className="act-heading">Every action</h3>
      <ActivityLog />
    </div></div>
  );
}
