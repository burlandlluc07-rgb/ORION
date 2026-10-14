import { WifiOff } from "lucide-react";
import { useEffect } from "react";
import { ApprovalDock } from "./components/ApprovalDock";
import { RightPanel } from "./components/RightPanel";
import { Sidebar } from "./components/Sidebar";
import { Toasts } from "./components/ui";
import { api, connectEvents, TOKEN } from "./lib/api";
import { useStore } from "./lib/store";
import type { QuotaRow } from "./lib/types";
import { ChatView } from "./views/ChatView";
import { EcosystemView } from "./views/EcosystemView";
import { MemoryView } from "./views/MemoryView";
import { Onboarding } from "./views/Onboarding";
import { QuickBar } from "./views/QuickBar";
import { PairView, PhoneApp } from "./views/PhoneApp";
import { RoutinesView } from "./views/RoutinesView";
import { SettingsView } from "./views/SettingsView";
import { TasksView } from "./views/TasksView";

function useTheme(theme: string | undefined) {
  useEffect(() => {
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches) || !theme;
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);
}

export default function App() {
  const s = useStore();
  const quick = location.hash.startsWith("#/quick");
  const remote = location.hash.startsWith("#/remote");
  const pairCode = location.hash.startsWith("#/pair/") ? location.hash.slice("#/pair/".length) : null;
  useTheme(s.engine?.settings.theme);

  useEffect(() => {
    const off = connectEvents(s.handleEvent, (connected) => {
      s.set({ connected });
      if (connected) s.refreshConversations().catch(() => {});
    });
    const quota = setInterval(() => {
      api.get<{ models: QuotaRow[] }>("/api/quota").then((q) => {
        const e = useStore.getState().engine;
        if (e) useStore.setState({ engine: { ...e, quota: q.models } });
      }).catch(() => {});
    }, 15000);
    return () => { off(); clearInterval(quota); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey) return;
      const k = e.key.toLowerCase();
      if (k === "n") { e.preventDefault(); s.openConversation(null); }
      if (k === "k") { e.preventDefault(); s.setView("chat"); setTimeout(() => document.querySelector<HTMLTextAreaElement>(".composer textarea")?.focus()); }
      if (k === "b") { e.preventDefault(); localStorage.setItem("orion.sidebar", s.sidebarOpen ? "0" : "1"); s.set({ sidebarOpen: !s.sidebarOpen }); }
      if (k === ".") { e.preventDefault(); s.stop(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [s]);

  if (pairCode) return <><PairView code={pairCode} /><Toasts /></>;

  if (!TOKEN) {
    return (
      <div className="onb"><div className="onb-card">
        <h1>{remote ? "Pair this phone" : "Open ORION from the app"}</h1>
        <p>{remote ? "On your PC open ORION → Settings → Phone app and scan the QR code with this phone's camera."
          : "This page needs the secure link the ORION desktop app opens. Launch ORION from the Start menu or tray."}</p>
      </div></div>
    );
  }

  if (quick) return <><QuickBar /><Toasts /></>;
  if (remote) return <><PhoneApp /><Toasts /></>;

  return (
    <>
      <div className="app">
        <Sidebar />
        {s.view === "chat" && <ChatView />}
        {s.view !== "chat" && (
          <div className="main">
            {s.view === "tasks" && <TasksView />}
            {s.view === "routines" && <RoutinesView />}
            {s.view === "ecosystem" && <EcosystemView />}
            {s.view === "memory" && <MemoryView />}
            {s.view === "settings" && <SettingsView />}
          </div>
        )}
        <RightPanel />
      </div>
      {!s.connected && s.engine && (
        <div className="toasts"><div className="toast warn"><div className="tt"><WifiOff size={13} /> Reconnecting to the ORION engine…</div></div></div>
      )}
      <ApprovalDock />
      {s.onboarding && s.engine && <Onboarding />}
      <Toasts />
    </>
  );
}
