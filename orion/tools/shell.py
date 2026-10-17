"""Shell and Python execution — always 'danger' risk so they require approval."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from typing import Literal

from .platform import CREATE_NO_WINDOW, IS_WIN, disown, home, own
from .registry import ToolContext, ToolError, tool


def _run(argv: list[str], timeout: int, ctx: ToolContext | None) -> str:
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", cwd=str(home()), creationflags=CREATE_NO_WINDOW,
    )
    if ctx is not None:
        own(ctx.cancel, proc)
    try:
        for _ in range(max(1, timeout) * 10):
            try:
                out, _ = proc.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if ctx is not None and ctx.cancel.is_set():
                    proc.kill()
                    raise ToolError("Cancelled.") from None
        else:
            proc.kill()
            out, _ = proc.communicate()
            out = (out or "") + f"\n[timed out after {timeout}s]"
    finally:
        if proc.poll() is None:
            proc.kill()
        if ctx is not None:
            disown(ctx.cancel, proc)
    out = (out or "").strip()
    return f"exit code {proc.returncode}\n{out[-6000:] if out else '(no output)'}"


@tool(risk="danger", category="power-user", summary=lambda a: f"Run: {a.get('command')}")
def run_command(command: str, shell: Literal["auto", "powershell", "cmd", "bash"] = "auto", timeout: int = 60,
                ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Run a terminal command and return its output (winget installs, git, pip, ipconfig, diagnostics…).

    Args:
        command: The command line.
        shell: Which shell; "auto" picks PowerShell on Windows and bash elsewhere.
        timeout: Seconds before the command is stopped (max 600).
    """
    timeout = max(1, min(timeout, 600))
    if shell == "auto":
        shell = "powershell" if IS_WIN else "bash"
    if shell == "powershell":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    elif shell == "cmd":
        argv = ["cmd", "/c", command]
    else:
        argv = ["bash", "-lc", command]
    return _run(argv, timeout, ctx)


@tool(risk="danger", category="power-user", summary=lambda a: "Run a Python script")
def run_python(code: str, timeout: int = 60, ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Execute a Python script (for calculations, data processing, file conversions). Print what you need back.

    Args:
        code: Python source code.
        timeout: Seconds before it is stopped.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
    exe = sys.executable if not getattr(sys, "frozen", False) else "python"
    return _run([exe, f.name], max(1, min(timeout, 600)), ctx)
