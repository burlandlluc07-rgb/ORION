export type Role = "user" | "assistant";

export interface Message {
  id: string;
  conv_id: string;
  role: Role;
  content: string;
  meta: { model?: string; latency_ms?: number; task_id?: string; tier?: string; source?: string; attachments?: string[] };
  ts: number;
  streaming?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  created: number;
  updated: number;
  pinned: boolean;
}

export type StepStatus = "running" | "waiting" | "done" | "failed" | "denied" | "stopped" | "attention";

/** The lifecycle record every step carries (orion/tasks/lifecycle.py). */
export interface Lifecycle {
  stage: string;
  executor: string;
  target: string;
  origin: string;
  history: { stage: string; at: number; note?: string }[];
  approval: { granted: boolean; by: string; scope: string; desktop: boolean } | null;
  logs: string[];
  verification: { status: "passed" | "failed" | "unverified" | "not_applicable"; reason?: string; evidence: string[] } | null;
  capabilities: { route: string; desktop: "none" | "visible" | "input" };
}

export interface Step {
  id: string;
  tool: string;
  label: string;
  risk: "safe" | "confirm" | "danger";
  status: StepStatus;
  result: string | null;
  started: number;
  ended: number | null;
  lifecycle?: Lifecycle;
}

export interface PlanItem {
  text: string;
  status: "pending" | "active" | "done";
}

export interface Task {
  id: string;
  title: string;
  conv_id: string | null;
  status: "running" | "waiting" | "done" | "failed" | "stopped";
  plan: PlanItem[];
  steps: Step[];
  created: number;
  updated: number;
}

export interface Approval {
  id: string;
  tool: string;
  label: string;
  risk: "safe" | "confirm" | "danger";
  args: Record<string, unknown>;
  task_id: string | null;
  expires: number;
  desktop?: boolean;
}

export interface QuotaRow {
  model: string;
  rpm_used: number;
  rpm: number;
  rpd_used: number;
  rpd: number;
  cooldown: number;
}

export interface VoiceSettings {
  mode: "live" | "classic" | "off";
  live_voice: string;
  tts_voice: string;
  tts_rate: string;
  wake_word: boolean;
  wake_phrases: string[];
  wake_sensitivity: number;
  wake_language: string;
  wake_engine: "windows" | "openwakeword";
  wake_word_model: string;
  input_device: number | null;
  output_device: number | null;
  speak_replies: boolean;
  instant_commands: boolean;
  follow_up: boolean;
  follow_up_seconds: number;
  barge_in: boolean;
  chime: boolean;
}

export interface Settings {
  user_name: string;
  assistant_name: string;
  persona: string;
  theme: string;
  accent: string;
  model_fast: string;
  model_smart: string;
  model_live: string;
  model_limits: Record<string, { rpm: number; rpd: number }>;
  thinking_budget: number;
  fast_path: boolean;
  extra_models: boolean;
  backup_provider: "off" | "omniroute" | "openrouter" | "groq" | "custom";
  backup_base_url: string;
  backup_model: string;
  approval_mode: "ask" | "auto_safe" | "trust";
  desktop_actions: "ask" | "allow";
  browser_mode: "background" | "visible";
  allowed_roots: string[];
  voice: VoiceSettings;
  hotkey_quickbar: string;
  hotkey_voice: string;
  start_minimized: boolean;
  launch_on_startup: boolean;
  close_to_tray: boolean;
  history_turns: number;
  onboarded: boolean;
  check_updates: boolean;
  proactive: boolean;
  proactive_voice: boolean;
  proactive_downloads: boolean;
  quiet_start: string;
  quiet_end: string;
  auto_memory: boolean;
  vault_path: string;
  memory_review_day: number;
  memory_review_hour: number;
  remote_enabled: boolean;
}

export interface VoiceStatus {
  state: string;
  detail?: string;
  muted?: boolean;
  mode?: string;
  wake_word?: boolean;
  wake_phrases?: string[] | null;
  wake_language?: string;
  live_model?: string | null;
  error?: string | null;
}

export interface BackupStatus {
  provider: Settings["backup_provider"];
  base_url: string;
  model: string;
  has_key: boolean;
  ready: boolean;
  active: boolean;
}

export interface EngineState {
  version: string;
  settings: Settings;
  has_key: boolean;
  backup?: BackupStatus;
  voice: VoiceStatus;
  quota: QuotaRow[];
  tasks: Task[];
  approvals: Approval[];
  live_model: string | null;
  data_dir: string;
  update?: { version: string; url: string; notes: string; page: string } | null;
}

export interface Vitals {
  cpu: number;
  ram: number;
  ram_used_gb: number;
  ram_total_gb: number;
  disk: number;
  disk_free_gb: number;
  uptime_h: number;
  battery?: number;
  plugged?: boolean;
}

export interface Skill {
  name: string;
  description: string;
  builtin: boolean;
  tier: string;
  icon: string;
  enabled: boolean;
  path: string;
  body?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  risk: "safe" | "confirm" | "danger";
  category: string;
  source: string;
  enabled: boolean;
}

export interface Routine {
  id: string;
  name: string;
  prompt: string;
  schedule: string;
  schedule_text: string;
  kind: "routine" | "reminder";
  enabled: boolean;
  next_run: number | null;
  last_run: number | null;
  last_result: string | null;
}

export interface Memory {
  id: number;
  text: string;
  kind: string;
  created: number;
  tier: "permanent" | "short";
  note: string | null;
}

export interface VaultStatus {
  path: string;
  permanent: number;
  short: number;
  last_review: number | null;
  next_review: number;
  obsidian: boolean;
}

export interface ConnectorInfo {
  name: string;
  state: "stopped" | "starting" | "ready" | "error";
  error: string | null;
  tools: string[];
  enabled: boolean;
  config: Record<string, unknown> & { env_keys?: string[] };
}

export interface CatalogEntry {
  id: string;
  name: string;
  description: string;
  transport: "stdio" | "http";
  command?: string;
  args?: string[];
  url?: string;
  needs: string[];
  requires: string;
}

export interface PluginInfo {
  name: string;
  ok: boolean;
  tools: string[];
  error: string | null;
  path: string;
}
