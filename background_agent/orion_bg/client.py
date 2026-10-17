"""Client for the daemon's localhost API. ORION's UI (or anything else) can
use this to talk to a daemon running in another process.

    from orion_bg.client import Client
    c = Client.from_runtime()          # reads port and token from daemon.json
    task = c.submit("Check my Downloads folder for duplicate PDFs")
    c.task(task["id"])
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import paths


class DaemonNotRunning(Exception):
    pass


class ClientError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status


class Client:
    def __init__(self, url: str, token: str, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_runtime(cls, timeout: float = 10.0) -> "Client":
        path = paths.runtime_file()
        if not path.exists():
            raise DaemonNotRunning("The background agent is not running (no daemon.json)")
        info = json.loads(path.read_text(encoding="utf-8"))
        return cls(f"http://{info['host']}:{info['port']}", info["token"], timeout=timeout)

    def ui_url(self) -> str:
        return f"{self.url}/?token={self.token}"

    def _req(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Orion-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        # Never route localhost through a system proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.timeout) as res:
                return json.loads(res.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error", exc.reason)
            except Exception:
                message = exc.reason
            raise ClientError(exc.code, message) from None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            raise DaemonNotRunning(f"Cannot reach the background agent at {self.url}: {exc}") from None

    # -- API ---------------------------------------------------------------

    def status(self) -> dict:
        return self._req("GET", "/api/status")

    def ping(self) -> bool:
        try:
            self.status()
            return True
        except (DaemonNotRunning, ClientError):
            return False

    def submit(self, prompt: str, kind: str = "agent", source: str = "api", timeout: float | None = None) -> dict:
        return self._req("POST", "/api/tasks", {"prompt": prompt, "kind": kind, "source": source, "timeout": timeout})

    def tasks(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = f"?limit={limit}" + (f"&status={status}" if status else "")
        return self._req("GET", "/api/tasks" + q)

    def task(self, task_id: str) -> dict:
        return self._req("GET", f"/api/tasks/{task_id}")

    def cancel(self, task_id: str) -> dict:
        return self._req("POST", f"/api/tasks/{task_id}/cancel")

    def approvals(self) -> list[dict]:
        return self._req("GET", "/api/approvals")

    def approve(self, approval_id: str) -> dict:
        return self._req("POST", f"/api/approvals/{approval_id}/approve")

    def deny(self, approval_id: str) -> dict:
        return self._req("POST", f"/api/approvals/{approval_id}/deny")

    def schedules(self) -> list[dict]:
        return self._req("GET", "/api/schedules")

    def add_schedule(self, prompt: str, spec: str, kind: str = "agent", name: str | None = None) -> dict:
        return self._req("POST", "/api/schedules", {"prompt": prompt, "spec": spec, "kind": kind, "name": name})

    def remove_schedule(self, schedule_id: str) -> dict:
        return self._req("DELETE", f"/api/schedules/{schedule_id}")

    def pause(self) -> dict:
        return self._req("POST", "/api/pause")

    def resume(self) -> dict:
        return self._req("POST", "/api/resume")

    def shutdown(self) -> dict:
        return self._req("POST", "/api/shutdown")


def env_client() -> Client:
    """Client from ORION_BG_URL / ORION_BG_TOKEN, else from daemon.json."""
    url, token = os.environ.get("ORION_BG_URL"), os.environ.get("ORION_BG_TOKEN")
    if url and token:
        return Client(url, token)
    return Client.from_runtime()
