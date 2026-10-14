"""Small cross-platform helpers shared by tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Hide console windows spawned from a GUI app on Windows.
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0


def run(cmd: list[str] | str, timeout: float = 15, shell: bool = False, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, shell=shell, check=check,
        creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace",
    )


def powershell(script: str, timeout: float = 20, legacy: bool = False) -> str:
    """Run a PowerShell script. ``legacy`` forces Windows PowerShell 5.1, which WinRT interop needs."""
    exe = (shutil.which("powershell") or "powershell") if legacy else \
        (shutil.which("pwsh") or shutil.which("powershell") or "powershell")
    res = run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=timeout)
    if res.returncode != 0 and res.stderr.strip():
        raise RuntimeError(res.stderr.strip()[:500])
    return res.stdout


def open_target(target: str) -> None:
    """Open a file, folder, URL or URI with the OS default handler."""
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    elif IS_MAC:
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def home() -> Path:
    return Path.home()


def known_folder(name: str) -> Path | None:
    """Resolve friendly folder names: desktop, documents, downloads, pictures, music, videos, home."""
    name = name.strip().lower()
    mapping = {
        "home": home(),
        "desktop": home() / "Desktop",
        "documents": home() / "Documents",
        "document": home() / "Documents",
        "downloads": home() / "Downloads",
        "download": home() / "Downloads",
        "pictures": home() / "Pictures",
        "picture": home() / "Pictures",
        "photos": home() / "Pictures",
        "music": home() / "Music",
        "videos": home() / "Videos",
        "video": home() / "Videos",
    }
    if IS_WIN:
        one = os.environ.get("OneDrive")
        if one:
            for key in ("desktop", "documents", "document", "pictures", "picture"):
                cand = Path(one) / mapping[key].name
                if cand.exists() and not mapping[key].exists():
                    mapping[key] = cand
    return mapping.get(name)


def resolve_path(raw: str) -> Path:
    """Expand ~, env vars and leading friendly names like 'desktop/notes.txt'."""
    raw = os.path.expandvars(os.path.expanduser(raw.strip().strip('"')))
    p = Path(raw)
    if not p.is_absolute():
        first, *rest = Path(raw).parts or ("",)
        base = known_folder(first)
        p = base.joinpath(*rest) if base else home() / p
    return p.resolve()
