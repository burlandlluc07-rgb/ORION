// Screenshot every main screen of the desktop UI in dark and light, with demo data.
// Needs a running engine (python -m orion --headless) and the Vite dev server (npm run dev),
// plus Playwright (NODE_PATH pointing at a global install is fine).
//
//   node scripts/ui_screens.cjs <engine token> <out dir> [base url] [width] [height]
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [token, out = "screens", base = "http://127.0.0.1:5173", w = "1440", h = "900"] = process.argv.slice(2);
if (!token) { console.error("usage: node scripts/ui_screens.cjs <token> [out] [base] [w] [h]"); process.exit(2); }
fs.mkdirSync(out, { recursive: true });

const now = Date.now() / 1000;
const CONV = "demo-conv";
const demo = {
  conversations: [
    { id: CONV, title: "Plan Saturday and tidy Downloads", created: now - 600, updated: now - 60, pinned: true },
    { id: "c2", title: "Email triage: what needs me today", created: now - 7200, updated: now - 3600, pinned: false },
    { id: "c3", title: "WhatsApp Marta about dinner", created: now - 90000, updated: now - 86000, pinned: false },
    { id: "c4", title: "Why is my PC slow?", created: now - 300000, updated: now - 290000, pinned: false },
    { id: "c5", title: "AutoCAD: export layouts to PDF", created: now - 600000, updated: now - 590000, pinned: false },
  ],
  messages: [
    { id: "m1", conv_id: CONV, role: "user", content: "What's on this Saturday, and can you tidy my Downloads folder while I'm out?", meta: {}, ts: now - 120 },
    { id: "m2", conv_id: CONV, role: "assistant", meta: { model: "gemini-flash-latest", latency_ms: 2400, task_id: "t1" }, ts: now - 110,
      content: "Here's your **Saturday**:\n\n- **10:00** Padel with Jordi at Club Turó\n- **14:30** Lunch at Marta's\n- **20:00** Cinema, *Dune: Part Three*\n\nI also sorted **142 files** in Downloads into folders. Nothing was deleted, and the installers older than 90 days are in `Downloads/Old installers` if you want to review them.\n\n```text\nDocuments  38\nImages     51\nInstallers 22\nArchives   17\nOther      14\n```" },
    { id: "m3", conv_id: CONV, role: "user", content: "Great. Send Marta a WhatsApp saying I'll bring dessert.", meta: {}, ts: now - 40 },
  ],
  tasks: {
    t1: { id: "t1", title: "Saturday plan and Downloads tidy", conv_id: CONV, status: "done", created: now - 115, updated: now - 100,
      plan: [{ text: "Read calendar for Saturday", status: "done" }, { text: "Sort Downloads into folders", status: "done" }, { text: "Verify nothing was deleted", status: "done" }],
      steps: [
        { id: "s1", tool: "calendar_events", label: "Read Saturday from your calendars", risk: "safe", status: "done", result: "3 events", started: now - 114, ended: now - 113 },
        { id: "s2", tool: "organize_folder", label: "Sort 142 files in Downloads", risk: "safe", status: "done", result: "142 moved, 0 deleted", started: now - 112, ended: now - 104 },
        { id: "s3", tool: "verify_folder", label: "Check file count before and after", risk: "safe", status: "done", result: "142 = 142", started: now - 104, ended: now - 101 },
      ] },
  },
  approvals: [
    { id: "a1", tool: "whatsapp_send", label: "Send a WhatsApp message to Marta Puig", risk: "confirm", task_id: null, expires: now + 600,
      args: { to: "Marta Puig (+34 6•• ••• 412)", message: "I'll bring dessert! 🍰" } },
  ],
  vitals: { cpu: 18, ram: 54, ram_used_gb: 8.6, ram_total_gb: 16, disk: 61, disk_free_gb: 182, battery: 84, plugged: true },
};

async function seed(page, extra = {}) {
  await page.evaluate(({ d, extra }) => {
    const st = window.__orion;
    st.setState({ conversations: d.conversations, vitals: d.vitals, onboarding: false, ...extra });
  }, { d: demo, extra });
}

async function shot(page, name) {
  await page.waitForTimeout(1300);  // lets the logo finish drawing in
  await page.screenshot({ path: path.join(out, `${name}.png`) });
  console.log("wrote", name);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  for (const theme of ["dark", "light"]) {
    const page = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
    await page.goto(`${base}/?token=${token}`);
    await page.waitForFunction(() => window.__orion && window.__orion.getState().engine);
    await page.evaluate((t) => {
      const st = window.__orion;
      const e = st.getState().engine;
      st.setState({ engine: { ...e, settings: { ...e.settings, theme: t, user_name: "Lluc" } } });
    }, theme);
    await page.evaluate(() => { localStorage.setItem("orion.right", "1"); window.__orion.setState({ rightOpen: true }); });

    // Home (empty chat)
    await seed(page, { view: "chat", activeId: null, messages: [], approvals: [] });
    await shot(page, `${theme}-01-home`);

    // Conversation with a task card and an approval card
    await seed(page, { view: "chat", activeId: CONV, messages: demo.messages, tasks: demo.tasks, approvals: demo.approvals });
    await shot(page, `${theme}-02-chat`);

    // Same, side panel closed
    await page.evaluate(() => window.__orion.setState({ rightOpen: false }));
    await shot(page, `${theme}-03-chat-focus`);
    await page.evaluate(() => window.__orion.setState({ rightOpen: true, approvals: [] }));

    for (const [view, n] of [["missions", "04"], ["tasks", "05"], ["routines", "06"], ["memory", "07"]]) {
      await seed(page, { view });
      await shot(page, `${theme}-${n}-${view}`);
    }

    await seed(page, { view: "ecosystem" });
    await shot(page, `${theme}-08-ecosystem-skills`);
    const acc = page.getByRole("tab", { name: "Accounts" });
    if (await acc.count()) { await acc.click(); await shot(page, `${theme}-09-ecosystem-accounts`); }

    await seed(page, { view: "settings" });
    await shot(page, `${theme}-10-settings`);
    const safety = page.locator("#set-safety");
    if (await safety.count()) { await safety.evaluate((el) => el.scrollIntoView({ block: "start" })); await shot(page, `${theme}-11-settings-safety`); }

    // Approval while on another page shows the dock
    await seed(page, { view: "tasks", approvals: demo.approvals });
    await shot(page, `${theme}-12-approval-dock`);

    // A modal (new routine)
    await seed(page, { view: "routines", approvals: [] });
    const nr = page.getByRole("button", { name: "New routine" });
    if (await nr.count()) { await nr.click(); await shot(page, `${theme}-13-modal`); await page.keyboard.press("Escape"); }

    // Collapsed sidebar on a conversation
    await seed(page, { view: "chat", activeId: CONV, messages: demo.messages, tasks: demo.tasks, approvals: [], sidebarOpen: false, rightOpen: false });
    await shot(page, `${theme}-14-collapsed`);
    await page.evaluate(() => window.__orion.setState({ sidebarOpen: true }));

    // Narrow window
    await page.setViewportSize({ width: 960, height: 760 });
    await seed(page, { view: "chat", activeId: CONV, messages: demo.messages, tasks: demo.tasks, approvals: demo.approvals, rightOpen: true });
    await shot(page, `${theme}-15-narrow-chat`);
    await page.evaluate(() => window.__orion.setState({ rightPeek: true }));
    await shot(page, `${theme}-18-narrow-activity`);
    await page.evaluate(() => window.__orion.setState({ rightPeek: false }));
    await seed(page, { view: "settings", approvals: [] });
    await shot(page, `${theme}-16-narrow-settings`);
    await seed(page, { view: "ecosystem" });
    const accN = page.getByRole("tab", { name: "Accounts" });
    if (await accN.count()) { await accN.click(); await shot(page, `${theme}-19-narrow-accounts`); }
    await page.setViewportSize({ width: +w, height: +h });

    // First-run onboarding
    await seed(page, { view: "chat", activeId: null, messages: [], approvals: [], onboarding: true });
    await shot(page, `${theme}-17-onboarding`);
    await page.close();
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
