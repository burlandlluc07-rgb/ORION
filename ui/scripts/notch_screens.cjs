// Screenshot the notch (#/notch) in each state over a plain desktop, plus a short video of it animating.
// Needs a running engine (python -m orion --headless) and the Vite dev server, plus Playwright.
//
//   node scripts/notch_screens.cjs <engine token> <out dir> [base url]
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [token, out = "notch", base = "http://127.0.0.1:5173"] = process.argv.slice(2);
if (!token) { console.error("usage: node scripts/notch_screens.cjs <token> [out] [base]"); process.exit(2); }
fs.mkdirSync(out, { recursive: true });

const now = Date.now() / 1000;
const CONV = "notch-conv";
const running = {
  id: "t9", title: "Tidy Downloads into folders", conv_id: CONV, status: "running", created: now - 20, updated: now - 1, plan: [],
  steps: [
    { id: "s1", tool: "list_folder", label: "Read 142 files in Downloads", risk: "safe", status: "done", started: now - 18, ended: now - 16 },
    { id: "s2", tool: "organize_folder", label: "Moving installers to Downloads/Installers", risk: "safe", status: "running", started: now - 15 },
  ],
};
const messages = [
  { id: "n1", conv_id: CONV, role: "user", content: "what's on Saturday?", meta: {}, ts: now - 30 },
  { id: "n2", conv_id: CONV, role: "assistant", meta: { model: "local" }, ts: now - 28,
    content: "**Saturday**\n\n- **10:00** Padel with Jordi\n- **14:30** Lunch at Marta's\n- **20:00** Cinema, *Dune: Part Three*" },
];
const approval = { id: "a9", tool: "whatsapp_send", label: "Send a WhatsApp message to Marta Puig", risk: "confirm", task_id: null,
  expires: now + 600, args: { to: "Marta Puig", message: "I'll bring dessert!" } };

const DESKTOP = `
  html.notch-doc body { background: linear-gradient(160deg, #3a3a3a 0%, #1d1d1d 55%, #111 100%) !important; }
  html.notch-doc body::after { content: ""; position: fixed; left: 8%; right: 8%; top: 70px; bottom: 0; border-radius: 10px 10px 0 0;
    background: #f3f3f2; box-shadow: 0 20px 60px rgba(0,0,0,.5); z-index: -1; }`;

async function open(browser, opts = {}) {
  const page = await browser.newPage({ viewport: { width: 1100, height: 560 }, deviceScaleFactor: 2, ...opts });
  await page.goto(`${base}/?token=${token}#/notch`);
  await page.waitForFunction(() => window.__orion && window.__orion.getState().engine);
  await page.addStyleTag({ content: DESKTOP });
  await page.evaluate(() => window.__orion.setState({ approvals: [], tasks: {}, messages: [], voice: { state: "standby" }, transcript: null }));
  await page.waitForTimeout(600);
  return page;
}
const set = (page, st) => page.evaluate((st) => window.__orion.setState(st), st);
async function shot(page, name) {
  await page.waitForTimeout(900);
  await page.screenshot({ path: path.join(out, `${name}.png`), clip: { x: 150, y: 0, width: 800, height: 540 } });
  console.log("wrote", name);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const page = await open(browser);
  await shot(page, "1-idle");
  await page.hover(".notch-pill");
  await shot(page, "2-idle-hover");
  await page.mouse.move(5, 500);
  await set(page, { voice: { state: "listening" }, transcript: { role: "user", text: "Hey ORION, tidy my downloads", tentative: true } });
  await shot(page, "3-peek-listening");
  await set(page, { voice: { state: "standby" }, transcript: null, tasks: { t9: running } });
  await shot(page, "4-peek-task");
  await set(page, { tasks: {}, activeId: CONV, messages });
  await page.evaluate(() => window.__orionNotch("open"));
  await shot(page, "5-open-reply");
  await page.evaluate(() => window.__orionNotch("close"));
  await set(page, { approvals: [approval] });
  await shot(page, "6-open-approval");
  await page.close();

  // Video: idle → hover → listening → task → open → closed.
  const vdir = path.join(out, "video");
  const vpage = await open(browser, { recordVideo: { dir: vdir, size: { width: 1100, height: 560 } }, deviceScaleFactor: 1 });
  await vpage.waitForTimeout(800);
  await vpage.hover(".notch-pill"); await vpage.waitForTimeout(1100);
  await vpage.mouse.move(5, 500); await vpage.waitForTimeout(600);
  await set(vpage, { voice: { state: "listening" }, transcript: { role: "user", text: "Hey ORION, tidy my downloads", tentative: true } });
  await vpage.waitForTimeout(1600);
  await set(vpage, { voice: { state: "standby" }, transcript: null, tasks: { t9: running } });
  await vpage.waitForTimeout(1800);
  await set(vpage, { tasks: {}, activeId: CONV, messages });
  await vpage.evaluate(() => window.__orionNotch("open"));
  await vpage.waitForTimeout(2200);
  await vpage.evaluate(() => window.__orionNotch("close"));
  await vpage.waitForTimeout(1400);
  await vpage.close();
  await browser.close();
  console.log("video in", vdir);
})();
