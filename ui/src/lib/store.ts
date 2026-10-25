import { create } from "zustand";
import { api } from "./api";
import type {
  Approval, Conversation, EngineState, Message, QuotaRow, Settings, Task, Vitals, VoiceStatus,
} from "./types";

export type View = "chat" | "missions" | "tasks" | "activity" | "routines" | "ecosystem" | "memory" | "settings";

export interface Toast {
  id: number;
  title: string;
  body?: string;
  tone?: "info" | "success" | "warn" | "error";
}

// High-frequency audio levels live outside React state (read by canvas animations).
export const levels = { mic: 0, out: 0 };

interface Store {
  connected: boolean;
  engine: EngineState | null;
  view: View;
  conversations: Conversation[];
  activeId: string | null;
  messages: Message[];
  tasks: Record<string, Task>;
  approvals: Approval[];
  /** The approval brought up by the approvals hotkey or picked from the queue; shown first. */
  approvalFocus: string | null;
  vitals: Vitals | null;
  voice: VoiceStatus;
  transcript: { role: string; text: string; tentative?: boolean } | null;
  heard: { phrase: string; confidence: number; accepted: boolean; at: number } | null;
  models: string[];
  toasts: Toast[];
  rightOpen: boolean;
  /** Activity panel opened as a drawer on narrow windows; not remembered. */
  rightPeek: boolean;
  sidebarOpen: boolean;
  onboarding: boolean;
  mode: "auto" | "fast" | "smart";
  bump: Record<string, number>;

  setView: (v: View) => void;
  openConversation: (id: string | null) => Promise<void>;
  refreshConversations: () => Promise<void>;
  refreshState: () => Promise<void>;
  send: (text: string, files?: { name: string; mime: string; b64: string }[]) => Promise<void>;
  stop: () => Promise<void>;
  toast: (t: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
  set: (p: Partial<Store>) => void;
  saveSettings: (patch: Partial<Settings> | Record<string, unknown>) => Promise<void>;
  handleEvent: (ev: { type: string; [k: string]: any }) => void;
}

let toastSeq = 1;

/** Approvals in queue order: waiting ones by arrival, then the ones put back with "Later". */
export function queueOrder(list: Approval[], ids?: string[]): Approval[] {
  const pos = ids ? new Map(ids.map((id, i) => [id, i])) : null;
  const sorted = pos ? [...list].sort((a, b) => (pos.get(a.id) ?? 1e9) - (pos.get(b.id) ?? 1e9)) : list;
  return [...sorted.filter((a) => !a.parked), ...sorted.filter((a) => a.parked)];
}

/** The one approval that keyboard shortcuts act on: the focused one, else the front of the queue. */
export function frontApproval(s: { approvals: Approval[]; approvalFocus: string | null }): Approval | undefined {
  return s.approvals.find((a) => a.id === s.approvalFocus) ?? s.approvals.find((a) => !a.parked);
}

const normTask = (t: Task): Task => ({ ...t, plan: t.plan ?? [], steps: t.steps ?? [] });

export const useStore = create<Store>((set, get) => ({
  connected: false,
  engine: null,
  view: "chat",
  conversations: [],
  activeId: null,
  messages: [],
  tasks: {},
  approvals: [],
  approvalFocus: null,
  vitals: null,
  voice: { state: "off" },
  transcript: null,
  heard: null,
  models: [],
  toasts: [],
  rightOpen: localStorage.getItem("orion.right") !== "0",
  rightPeek: false,
  sidebarOpen: localStorage.getItem("orion.sidebar") !== "0",
  onboarding: false,
  mode: "auto",
  bump: {},

  set: (p) => set(p),
  setView: (view) => set({ view }),

  openConversation: async (id) => {
    set({ activeId: id, view: "chat", transcript: null });
    if (!id) {
      set({ messages: [] });
      return;
    }
    const data = await api.get<{ messages: Message[]; tasks: Task[] }>(`/api/conversations/${id}`);
    if (get().activeId !== id) return;
    const tasks = { ...get().tasks };
    for (const t of data.tasks) tasks[t.id] = tasks[t.id] && tasks[t.id].updated > t.updated ? tasks[t.id] : normTask(t);
    set({ messages: data.messages, tasks });
  },

  refreshConversations: async () => {
    set({ conversations: await api.get<Conversation[]>("/api/conversations") });
  },

  refreshState: async () => {
    const engine = await api.get<EngineState>("/api/state");
    set({ engine, voice: engine.voice, approvals: queueOrder(engine.approvals), onboarding: !engine.has_key || !engine.settings.onboarded });
  },

  send: async (text, files) => {
    const { activeId, mode } = get();
    const res = await api.post<{ conv_id: string }>("/api/chat", { text, conv_id: activeId, mode, attachments: files ?? [] });
    if (res.conv_id !== activeId) {
      set({ activeId: res.conv_id, view: "chat" });
      const data = await api.get<{ messages: Message[] }>(`/api/conversations/${res.conv_id}`);
      // Merge: streamed events may already have arrived.
      const seen = new Set(get().messages.map((m) => m.id));
      set({ messages: [...data.messages.filter((m) => !seen.has(m.id)), ...get().messages.filter((m) => m.conv_id === res.conv_id)] });
    }
  },

  stop: async () => {
    await api.post("/api/stop");
  },

  toast: (t) => {
    const id = toastSeq++;
    set({ toasts: [...get().toasts.slice(-3), { ...t, id }] });
    setTimeout(() => get().dismissToast(id), t.tone === "error" ? 9000 : 5000);
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((x) => x.id !== id) }),

  saveSettings: async (patch) => {
    const settings = await api.patch<Settings>("/api/settings", patch);
    const engine = get().engine;
    if (engine) set({ engine: { ...engine, settings } });
  },

  handleEvent: (ev) => {
    const s = get();
    const bump = (k: string) => set({ bump: { ...get().bump, [k]: (get().bump[k] ?? 0) + 1 } });
    switch (ev.type) {
      case "hello": {
        const st = ev.state as EngineState;
        const tasks = { ...s.tasks };
        for (const t of st.tasks) tasks[t.id] = normTask(t);
        set({ engine: st, voice: st.voice, approvals: queueOrder(st.approvals), tasks,
          onboarding: !st.has_key || !st.settings.onboarded });
        break;
      }
      case "conversation.created":
        set({ conversations: [ev.conversation, ...s.conversations.filter((c) => c.id !== ev.conversation.id)] });
        break;
      case "conversations.changed":
        get().refreshConversations();
        break;
      case "message": {
        const m = ev.message as Message;
        touch(m.conv_id);
        if (m.conv_id === s.activeId && !s.messages.some((x) => x.id === m.id)) {
          set({ messages: [...get().messages, m] });
        }
        if (m.role === "assistant" && m.meta?.source === "voice") set({ transcript: null });
        break;
      }
      case "message.start":
        if (ev.conv_id === s.activeId) {
          set({ messages: [...s.messages, { id: ev.id, conv_id: ev.conv_id, role: "assistant", content: "",
            meta: { task_id: ev.task_id }, ts: ev.ts, streaming: true }] });
        }
        break;
      case "message.model":
      case "message.delta":
        if (ev.conv_id === s.activeId) {
          set({ messages: s.messages.map((m) => m.id !== ev.id ? m : ev.type === "message.delta"
            ? { ...m, content: m.content + ev.delta } : { ...m, meta: { ...m.meta, model: ev.model } }) });
        }
        break;
      case "message.end":
        touch(ev.conv_id);
        if (ev.conv_id === s.activeId) {
          const exists = s.messages.some((m) => m.id === ev.id);
          set({ messages: exists ? s.messages.map((m) => (m.id === ev.id ? ev.message : m)) : [...s.messages, ev.message] });
        }
        break;
      case "task":
        set({ tasks: { ...s.tasks, [ev.task.id]: normTask(ev.task) } });
        break;
      case "approval.request":
        set({ approvals: queueOrder([...s.approvals.filter((a) => a.id !== ev.approval.id), ev.approval]) });
        if (document.hidden) get().toast({ title: "Approval needed", body: ev.approval.label, tone: "warn" });
        break;
      case "approval.updated":
        set({ approvals: queueOrder(s.approvals.map((a) => (a.id === ev.approval.id ? ev.approval : a)), ev.queue),
              approvalFocus: ev.approval.parked && s.approvalFocus === ev.approval.id ? null : s.approvalFocus });
        break;
      case "approval.focus":
        set({ approvalFocus: ev.id });
        break;
      case "approval.resolved":
        set({ approvals: s.approvals.filter((a) => a.id !== ev.id), approvalFocus: s.approvalFocus === ev.id ? null : s.approvalFocus });
        break;
      case "vitals":
        set({ vitals: ev.vitals });
        break;
      case "voice.state":
        set({ voice: { ...s.voice, state: ev.state, detail: ev.detail, muted: ev.muted, mode: ev.mode } });
        if (ev.state === "listening" && ev.conv_id && s.view === "chat" && s.activeId !== ev.conv_id && s.messages.length === 0) {
          get().openConversation(ev.conv_id);
        }
        break;
      case "voice.heard":
        set({ heard: { phrase: ev.phrase, confidence: ev.confidence, accepted: ev.accepted, at: Date.now() } });
        break;
      case "voice.transcript":
        set({ transcript: { role: ev.role, text: ev.text, tentative: !!ev.tentative } });
        break;
      case "audio.level":
        if (ev.source === "mic") levels.mic = ev.level;
        else levels.out = ev.level;
        break;
      case "notify":
        get().toast({ title: ev.title, body: ev.message, tone: "info" });
        break;
      case "settings.changed":
        if (s.engine) set({ engine: { ...s.engine, settings: ev.settings } });
        break;
      case "brain.models":
        set({ models: ev.models ?? [] });
        if (s.engine) set({ engine: { ...get().engine!, live_model: ev.live_model } });
        break;
      case "alert":  // non-quiet alerts also arrive as "notify"; quiet-hour ones only show in the app
        if (ev.alert.quiet) get().toast({ title: ev.alert.title, body: ev.alert.message, tone: "info" });
        bump("alert");
        break;
      case "update.available":
        if (s.engine) set({ engine: { ...s.engine, update: ev.update } });
        break;
      case "brain.backup":
        if (s.engine?.backup) set({ engine: { ...s.engine, backup: { ...s.engine.backup, active: ev.active } } });
        get().toast(ev.active
          ? { title: "Using the backup brain", body: ev.reason ? `${ev.reason} ORION switches back to Gemini when it can.` : "Gemini is out of free quota for now.", tone: "warn" }
          : { title: "Back on Gemini", body: "Gemini has free quota again.", tone: "info" });
        break;
      case "setup.required":
        set({ onboarding: true });
        break;
      case "stopped":
        get().toast({ title: "Stopped", body: ev.count ? `Halted ${ev.count} running task(s).` : "Nothing was running.", tone: "warn" });
        break;
      case "memory.changed":
      case "missions.changed":
      case "remote.changed":
      case "ecosystem.changed":
      case "routines.changed":
        bump(ev.type);
        break;
    }

    function touch(convId: string) {
      const list = get().conversations;
      const c = list.find((x) => x.id === convId);
      if (c) set({ conversations: [{ ...c, updated: Date.now() / 1000 }, ...list.filter((x) => x.id !== convId)] });
    }
  },
}));

export function quotaSummary(rows: QuotaRow[] | undefined) {
  if (!rows?.length) return null;
  const used = rows.reduce((a, r) => a + r.rpd_used, 0);
  const total = rows.reduce((a, r) => a + r.rpd, 0);
  return { used, total, pct: total ? used / total : 0 };
}
