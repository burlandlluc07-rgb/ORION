"""Safety rules: the path allowlist and shell command classification.

Every tool call is checked before it runs and gets one of three verdicts:
    allow    run it now
    approve  pause the task as needs_approval until the user says OK
    deny     refuse; the model is told why
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ALLOW = "allow"
APPROVE = "approve"
DENY = "deny"


@dataclass(frozen=True)
class Verdict:
    action: str
    reason: str = ""


class PathNotAllowed(Exception):
    pass


def resolve_path(raw: str, roots: list[Path]) -> Path:
    """Resolve `raw` and make sure it sits inside one of `roots`.

    Relative paths are taken from the first root. Symlinks and ".." are
    resolved first, so they cannot escape the allowlist.
    """
    if not roots:
        raise PathNotAllowed("No folders are allowed (safety.allowed_paths is empty)")
    expanded = os.path.expandvars(os.path.expanduser(str(raw)))
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve()
    for root in roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    allowed = ", ".join(str(r) for r in roots)
    raise PathNotAllowed(f"{resolved} is outside the allowed folders ({allowed})")


# Characters that chain, redirect or substitute commands in cmd.exe, PowerShell or sh.
_UNSAFE_SHELL = re.compile(r"[&|;<>`\r\n]|\$\(")


def classify_command(command: str, safe_commands: list[str]) -> Verdict:
    cmd = " ".join(command.strip().split())
    if not cmd:
        return Verdict(DENY, "Empty command")
    if _UNSAFE_SHELL.search(cmd):
        return Verdict(APPROVE, "Command chains, redirects or substitutes other commands")
    lowered = cmd.lower()
    for safe in safe_commands:
        s = " ".join(safe.lower().split())
        if lowered == s or lowered.startswith(s + " "):
            return Verdict(ALLOW)
    return Verdict(APPROVE, "Command is not on the safe list")
