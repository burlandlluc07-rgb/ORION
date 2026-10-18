import { ActivityLog } from "../components/ActivityLog";

export function ActivityView() {
  return (
    <div className="page"><div className="page-inner">
      <div className="page-head">
        <div className="grow">
          <h2>What ORION did</h2>
          <p>Every action ORION took, newest first: what it did, how it ran, who approved it and the proof it worked. Kept on this PC only.</p>
        </div>
      </div>
      <ActivityLog />
    </div></div>
  );
}
