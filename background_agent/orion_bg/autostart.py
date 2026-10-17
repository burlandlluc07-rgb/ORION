"""Start with Windows: a Task Scheduler task that runs at logon, has no window,
and restarts on failure. It runs the supervisor (`run`), which in turn
restarts the daemon if it crashes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths

TASK_NAME = "Orion Background Agent"


def launch_command() -> tuple[str, str, str]:
    """(program, arguments, working folder) that start the supervisor without a console."""
    if getattr(sys, "frozen", False):
        return sys.executable, "run", str(Path(sys.executable).parent)
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    # Run from the folder that contains orion_bg, so it imports without pip install.
    source_root = Path(__file__).resolve().parent.parent
    return str(pythonw if pythonw.exists() else exe), "-m orion_bg run", str(source_root)


def task_xml(user: str, program: str, arguments: str, workdir: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Runs ORION's background agent at logon. No window, restarts on failure.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT15S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _user() -> str:
    domain = os.environ.get("USERDOMAIN")
    name = os.environ.get("USERNAME") or os.getlogin()
    return f"{domain}\\{name}" if domain else name


def install() -> str:
    if sys.platform != "win32":
        raise RuntimeError("Autostart uses Windows Task Scheduler and only works on Windows")
    program, arguments, workdir = launch_command()
    xml_path = paths.data_dir() / "autostart-task.xml"
    xml_path.write_text(task_xml(_user(), program, arguments, workdir), encoding="utf-16")
    proc = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True, creationflags=0x08000000,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return f"Installed scheduled task '{TASK_NAME}': {program} {arguments}"


def uninstall() -> str:
    if sys.platform != "win32":
        raise RuntimeError("Autostart only works on Windows")
    proc = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True, creationflags=0x08000000,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return f"Removed scheduled task '{TASK_NAME}'"
