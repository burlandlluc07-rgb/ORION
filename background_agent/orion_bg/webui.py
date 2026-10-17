"""Task history page served by the daemon at / (open it from the tray).

Kept as a Python string so PyInstaller needs no extra data files. All task
text is inserted with textContent, never as HTML.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orion background tasks</title>
<style>
  :root { --bg:#f7f7f8; --fg:#1d1d1f; --muted:#6b6b70; --card:#fff; --line:#e3e3e6; --ok:#1f7a3a; --bad:#b3261e; --warn:#8a5a00; --acc:#2856d6; }
  @media (prefers-color-scheme: dark) { :root { --bg:#16171a; --fg:#ececf0; --muted:#9a9aa3; --card:#1f2024; --line:#2e2f35; --ok:#5cc47a; --bad:#ff7b72; --warn:#e3b341; --acc:#7aa2ff; } }
  body { margin:0; font:14px/1.45 system-ui, "Segoe UI", sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:960px; margin:0 auto; padding:16px; }
  h1 { font-size:20px; margin:4px 0 12px; } h2 { font-size:15px; margin:20px 0 8px; }
  .bar { display:flex; gap:8px; flex-wrap:wrap; }
  .bar input { flex:1; min-width:200px; padding:8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); }
  button { padding:7px 12px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); cursor:pointer; }
  button.primary { background:var(--acc); color:#fff; border-color:var(--acc); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; margin:6px 0; }
  .meta { color:var(--muted); font-size:12px; }
  .st { font-weight:600; font-size:12px; text-transform:uppercase; }
  .completed { color:var(--ok); } .failed, .cancelled { color:var(--bad); } .needs_approval { color:var(--warn); } .running, .queued { color:var(--acc); }
  pre { white-space:pre-wrap; word-break:break-word; margin:6px 0 0; font:12px/1.4 ui-monospace, Consolas, monospace; }
  #status { color:var(--muted); margin-bottom:8px; }
</style>
</head>
<body>
<main>
  <h1>Orion background tasks</h1>
  <div id="status">Connecting...</div>
  <div class="bar">
    <input id="prompt" placeholder="New task for Orion to do in the background">
    <button class="primary" id="add">Queue</button>
    <button id="pause">Pause</button>
  </div>
  <h2>Waiting for your OK</h2>
  <div id="approvals"></div>
  <h2>History</h2>
  <div id="tasks"></div>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
async function api(method, path, body) {
  const res = await fetch(path, { method, headers: { "X-Orion-Token": token, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function when(t) { return t ? new Date(t * 1000).toLocaleString() : ""; }
let paused = false;
async function refresh() {
  try {
    const [st, approvals, tasks] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/approvals"), api("GET", "/api/tasks?limit=100")]);
    paused = st.paused;
    document.getElementById("pause").textContent = paused ? "Resume" : "Pause";
    document.getElementById("status").textContent = (paused ? "Paused. " : "") + `${st.running.length} running, ${st.counts.queued} queued, ${st.pending_approvals} waiting for approval.`;
    const ab = document.getElementById("approvals"); ab.replaceChildren();
    if (!approvals.length) ab.append(el("div", "meta", "Nothing is waiting."));
    for (const a of approvals) {
      const c = el("div", "card");
      c.append(el("div", "st needs_approval", a.tool), el("div", null, a.reason), el("pre", null, JSON.stringify(a.input, null, 2)));
      const ok = el("button", "primary", "Approve"); ok.onclick = () => api("POST", `/api/approvals/${a.id}/approve`).then(refresh);
      const no = el("button", null, "Deny"); no.onclick = () => api("POST", `/api/approvals/${a.id}/deny`).then(refresh);
      const row = el("div", "bar"); row.append(ok, no); c.append(row); ab.append(c);
    }
    const tb = document.getElementById("tasks"); tb.replaceChildren();
    for (const t of tasks) {
      const c = el("div", "card");
      c.append(el("span", "st " + t.status, t.status.replace("_", " ")), el("span", "meta", `  ${t.kind} from ${t.source}, ${when(t.created)}  #${t.id}`));
      c.append(el("div", null, t.prompt));
      if (t.result) c.append(el("pre", null, t.result));
      if (t.error) c.append(el("pre", "failed", t.error));
      if (["queued", "running", "needs_approval"].includes(t.status)) {
        const x = el("button", null, "Cancel"); x.onclick = () => api("POST", `/api/tasks/${t.id}/cancel`).then(refresh); c.append(x);
      }
      tb.append(c);
    }
  } catch (e) { document.getElementById("status").textContent = "Cannot reach the daemon: " + e.message; }
}
document.getElementById("add").onclick = async () => {
  const p = document.getElementById("prompt"); if (!p.value.trim()) return;
  await api("POST", "/api/tasks", { prompt: p.value, source: "ui" }); p.value = ""; refresh();
};
document.getElementById("prompt").addEventListener("keydown", e => { if (e.key === "Enter") document.getElementById("add").click(); });
document.getElementById("pause").onclick = () => api("POST", paused ? "/api/resume" : "/api/pause").then(refresh);
refresh(); setInterval(refresh, 3000);
</script>
</body>
</html>
"""
