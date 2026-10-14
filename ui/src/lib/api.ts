// Thin client for the local engine API.
// Desktop window: a per-launch token arrives in the URL (?token=…) and lives in sessionStorage.
// Phone app: a long-lived per-device token from pairing lives in localStorage.

export const IS_PHONE = location.hash.startsWith("#/remote") || location.hash.startsWith("#/pair");
const params = new URLSearchParams(location.search);
const fromUrl = params.get("token");
if (fromUrl) {
  sessionStorage.setItem("orion.token", fromUrl);
  params.delete("token");
  const clean = location.pathname + (params.toString() ? `?${params}` : "") + location.hash;
  history.replaceState(null, "", clean);
}
export const TOKEN = IS_PHONE ? localStorage.getItem("orion.device") ?? "" : sessionStorage.getItem("orion.token") ?? "";

export function saveDeviceToken(token: string) {
  localStorage.setItem("orion.device", token);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { "x-orion-token": TOKEN, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail ?? j.error ?? msg;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T,>(p: string, b: unknown) => request<T>("PATCH", p, b),
  put: <T,>(p: string, b: unknown) => request<T>("PUT", p, b),
  del: <T,>(p: string) => request<T>("DELETE", p),
};

export type EventHandler = (ev: { type: string; [k: string]: any }) => void;

/** WebSocket event stream with automatic reconnect. */
export function connectEvents(onEvent: EventHandler, onStatus: (connected: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 500;
  let timer: number | undefined;

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
    ws.onopen = () => {
      retry = 500;
      onStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        onEvent(JSON.parse(m.data));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      onStatus(false);
      if (!closed) {
        timer = window.setTimeout(open, retry);
        retry = Math.min(retry * 2, 5000);
      }
    };
  };
  open();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    ws?.close();
  };
}

export function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}
