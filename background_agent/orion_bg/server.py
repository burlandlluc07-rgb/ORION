"""Localhost JSON API, so any UI (ORION's WebView2 window, the CLI, the tray,
the history page) is only a client. Closing a client never stops a task.

Every request needs the token from daemon.json, in the X-Orion-Token header
or a ?token= query parameter. The server only listens on 127.0.0.1.

    GET    /api/status
    GET    /api/tasks?status=&limit=
    POST   /api/tasks                 {"prompt", "kind"?, "timeout"?, "source"?}
    GET    /api/tasks/<id>
    POST   /api/tasks/<id>/cancel
    GET    /api/approvals
    POST   /api/approvals/<id>/approve
    POST   /api/approvals/<id>/deny
    GET    /api/schedules
    POST   /api/schedules             {"prompt", "spec", "kind"?, "name"?}
    DELETE /api/schedules/<id>
    POST   /api/pause | /api/resume | /api/shutdown
    GET    /                          task history page
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .webui import PAGE

log = logging.getLogger("orion_bg.server")

MAX_BODY = 1_000_000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ApiServer:
    def __init__(self, agent: Any, host: str, port: int, token: str,
                 on_shutdown: Callable[[], None] | None = None):
        self.agent = agent
        self.token = token
        self.on_shutdown = on_shutdown
        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[:2]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="api", daemon=True)
        self._thread.start()
        log.info("API listening on %s", self.url)

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- routing -----------------------------------------------------------

    def handle(self, method: str, path: str, query: dict, body: dict) -> Any:
        agent = self.agent
        parts = [p for p in path.split("/") if p]
        if parts[:1] != ["api"]:
            raise ApiError(404, "Not found")
        parts = parts[1:]

        if parts == ["status"] and method == "GET":
            return agent.status()
        if parts == ["tasks"] and method == "GET":
            return agent.list(status=query.get("status") or None, limit=int(query.get("limit") or 50))
        if parts == ["tasks"] and method == "POST":
            try:
                timeout = body.get("timeout")
                return agent.submit(
                    str(body.get("prompt", "")), kind=body.get("kind", "agent"),
                    source=str(body.get("source", "api"))[:20],
                    timeout=float(timeout) if timeout else None,
                )
            except ValueError as exc:
                raise ApiError(400, str(exc))
        if len(parts) == 2 and parts[0] == "tasks" and method == "GET":
            task = agent.get(parts[1])
            if task is None:
                raise ApiError(404, "No such task")
            task["approvals"] = agent.store.approvals(None, task_id=parts[1])
            return task
        if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel" and method == "POST":
            task = agent.cancel(parts[1])
            if task is None:
                raise ApiError(404, "No such task")
            return task
        if parts == ["approvals"] and method == "GET":
            return agent.pending_approvals()
        if len(parts) == 3 and parts[0] == "approvals" and parts[2] in ("approve", "deny") and method == "POST":
            try:
                task = agent.approve(parts[1]) if parts[2] == "approve" else agent.deny(parts[1])
            except KeyError as exc:
                raise ApiError(404, str(exc.args[0]))
            return {"ok": True, "requeued": task}
        if parts and parts[0] == "schedules":
            sched = agent.scheduler
            if sched is None:
                raise ApiError(409, "The scheduler is disabled or APScheduler is not installed")
            if len(parts) == 1 and method == "GET":
                return sched.list()
            if len(parts) == 1 and method == "POST":
                try:
                    return sched.add(str(body.get("prompt", "")), str(body.get("spec", "")),
                                     kind=body.get("kind", "agent"), name=body.get("name"))
                except ValueError as exc:
                    raise ApiError(400, str(exc))
            if len(parts) == 2 and method == "DELETE":
                if not sched.remove(parts[1]):
                    raise ApiError(404, "No such schedule")
                return {"ok": True}
        if parts == ["pause"] and method == "POST":
            agent.pause()
            return agent.status()
        if parts == ["resume"] and method == "POST":
            agent.resume()
            return agent.status()
        if parts == ["shutdown"] and method == "POST":
            if self.on_shutdown:
                threading.Thread(target=self.on_shutdown, daemon=True).start()
            return {"ok": True}
        raise ApiError(404, "Not found")

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "OrionBackground/1"

            def log_message(self, fmt: str, *args: Any) -> None:  # keep stderr quiet
                log.debug("%s " + fmt, self.address_string(), *args)

            def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
                data = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self, query: dict) -> bool:
                given = self.headers.get("X-Orion-Token") or query.get("token") or ""
                return hmac.compare_digest(given, server.token)

            def _dispatch(self, method: str) -> None:
                url = urlparse(self.path)
                query = {k: v[-1] for k, v in parse_qs(url.query).items()}
                if not self._authorised(query):
                    self._send(401, {"error": "Missing or wrong token"})
                    return
                if url.path in ("/", "/index.html") and method == "GET":
                    self._send(200, PAGE, "text/html")
                    return
                body: dict = {}
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._send(413, {"error": "Body too large"})
                    return
                if length:
                    try:
                        body = json.loads(self.rfile.read(length) or b"{}")
                    except json.JSONDecodeError:
                        self._send(400, {"error": "Body is not JSON"})
                        return
                    if not isinstance(body, dict):
                        self._send(400, {"error": "Body must be a JSON object"})
                        return
                try:
                    self._send(200, server.handle(method, url.path, query, body))
                except ApiError as exc:
                    self._send(exc.status, {"error": str(exc)})
                except Exception as exc:
                    log.exception("API error on %s %s", method, url.path)
                    self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_DELETE(self) -> None:
                self._dispatch("DELETE")

        return Handler

