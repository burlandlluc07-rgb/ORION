// ORION phone app service worker: offline shell, push notifications, approve/deny from the lock screen
// (Approve opens the app instead when the phone uses the fingerprint check).
const SHELL = "orion-shell-v1";
const AUTH = "orion-auth";

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["./", "./manifest.webmanifest", "./icon-192.png"])).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// Network first for the app shell (so updates arrive immediately); never cache the API.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && url.origin === location.origin) caches.open(SHELL).then((c) => c.put(e.request, r.clone()));
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("./"))));
});

// The page hands us its device token so notification buttons can approve/deny without opening the app.
self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "token") caches.open(AUTH).then((c) => c.put("/__token", new Response(e.data.token)));
});

self.addEventListener("push", (e) => {
  let msg = {};
  try { msg = e.data.json(); } catch { msg = { title: "ORION", body: e.data ? e.data.text() : "" }; }
  const data = msg.data || {};
  const opts = { body: msg.body, icon: "icon-192.png", badge: "icon-192.png", tag: msg.tag || undefined, data, renotify: !!msg.tag };
  if (data.approval_id) {
    opts.actions = [{ action: "approve", title: "Approve" }, { action: "deny", title: "Deny" }];
    opts.requireInteraction = true;
  }
  e.waitUntil(self.registration.showNotification(msg.title || "ORION", opts));
});

self.addEventListener("notificationclick", (e) => {
  const data = e.notification.data || {};
  e.notification.close();
  if (data.approval_id && (e.action === "approve" || e.action === "deny")) {
    // Approve may need the fingerprint check, which only the app page can ask for: open the app on it then.
    e.waitUntil(caches.open(AUTH).then((c) => c.match("/__token")).then((r) => r && r.text()).then((token) =>
      fetch(`/api/approvals/${data.approval_id}`, {
        method: "POST", headers: { "content-type": "application/json", "x-orion-token": token || "" },
        body: JSON.stringify({ decision: e.action === "approve" ? "approve" : "deny" }),
      })).then((res) => {
        if (res && res.status === 403) return self.clients.openWindow("./#/remote");
      }));
    return;
  }
  const target = data.url || "./#/remote";
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((list) => {
    for (const c of list) if (!data.url && "focus" in c) return c.focus();
    return self.clients.openWindow(target);
  }));
});
