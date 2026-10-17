"""Execution routing: which executor runs each action, and what it can do to the user's desktop.

Routes, safest first (the order from the background-execution design):

1. ``api_mcp``          an API or MCP connector (web, email, smart home, connectors)
2. ``cli``              a command-line or script worker with no window (PowerShell, winget, shell, Python)
3. ``headless_browser`` ORION's own browser, running without a window
4. ``app_automation``   a supported application interface (Windows UI Automation patterns, no mouse)
5. ``gui_env``          a dedicated GUI environment (none is configured yet)
6. ``foreground``       the user's own desktop: launches visible windows, or uses the mouse, keys or focus

``local`` marks in-process work on files and ORION's own data (documents written with file libraries,
notes, memory), which is as background as it gets.

Anything with ``desktop`` other than ``none`` goes through the desktop permission gate in
:meth:`orion.brain.agent.Agent.run_tool` before it runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .lifecycle import Capabilities, ToolExecutor

if TYPE_CHECKING:
    from ..config import Settings
    from ..tools.registry import Tool

ROUTE_ORDER = ("api_mcp", "cli", "headless_browser", "app_automation", "gui_env", "foreground")

# Real mouse, keyboard or focus: the user's input is taken over.
INPUT = {"type_text", "press_keys", "mouse_click", "scroll", "window_action"}
# Opens, moves or covers a visible window, or locks/ends the session, without injecting input.
VISIBLE = {"open_app", "open_settings_page", "open_website", "search_in_browser", "play_youtube", "play_spotify",
           "open_path", "arrange_window", "calendar_add_event", "lock_pc", "power", "browser_show"}
# The browser tools: background while ORION's browser runs hidden, visible while it is shown.
BROWSER = {"browser_open", "browser_view", "browser_click", "browser_type", "browser_select", "browser_press",
           "browser_scroll", "browser_back", "browser_tabs", "browser_look", "browser_close", "browser_hide"}
APP_AUTOMATION = {"list_windows", "inspect_window", "click_control", "set_control_text"}
CLI = {"run_command", "run_python", "wifi", "bluetooth", "install_app", "uninstall_app", "app_updates",
       "find_app_to_install", "check_windows_updates", "power_plan", "drive_health", "battery_health",
       "network_info", "internet_speed_test", "ping", "startup_apps", "clean_temp_files", "empty_recycle_bin",
       "kill_process", "close_app", "list_processes", "system_status"}
API = {"web_search", "read_webpage", "get_weather", "convert_currency", "download_file"}
EXTERNAL = {"email_send", "email_forward", "email_move", "email_mark", "home_control", "home_scene", "home_security",
            "whatsapp_send"}
LOCAL_CHANGES = {"write_file", "create_folder", "move_or_copy", "delete_path", "undo_last_change", "create_document",
                 "edit_document", "create_pdf", "create_spreadsheet", "update_spreadsheet", "create_presentation",
                 "combine_pdfs", "extract_pdf_pages", "fill_pdf_form", "edit_images", "zip_files", "extract_archive",
                 "download_file", "set_theme", "set_wallpaper", "set_volume", "change_volume", "mute",
                 "set_brightness", "take_screenshot", "email_save_attachments", "write_clipboard", "create_routine",
                 "forget", "remember", "add_note", "email_importance_rules",
                 "email_importance_feedback", "todo", "set_timer", "set_reminder", "cancel_scheduled"}
# Tools that already read their own change back and fail loudly when it didn't stick.
SELF_CHECKED = {"write_file": "the file was read back and compared", "create_folder": "the folder was checked",
                "move_or_copy": "the destination was checked", "set_theme": "the registry value was read back",
                "set_wallpaper": "Windows reported the new wallpaper", "startup_apps": "the entry was read back",
                "set_volume": "the volume level was read back"}
COOPERATIVE = {"run_command", "run_python", "search_files", "download_file", "clean_temp_files", "internet_speed_test",
               "install_app", "app_updates", "start_mission"}
# Hands work to the background agent (orion/background), which has its own allowlist and approval gate.
BACKGROUND_AGENT = {"queue_background_task", "cancel_background_task"}
ROLLBACK = {"write_file", "move_or_copy", "delete_path", "create_document", "edit_document", "create_pdf",
            "create_spreadsheet", "update_spreadsheet", "create_presentation"}


def capabilities_for(tool: "Tool", settings: "Settings | None" = None, browser_visible: bool = False) -> Capabilities:
    name = tool.name
    source = tool.source
    if source.startswith("mcp:"):
        external = tool.risk != "safe"
        return Capabilities(route="api_mcp", changes_external=external, evidence="the connector's response")
    if source.startswith("plugin:"):
        # Plugin code is the user's own and can do anything; it is recorded but not classified.
        return Capabilities(route="local", changes_local=tool.risk != "safe", evidence="the plugin's own result")
    if name in INPUT:
        return Capabilities(route="foreground", desktop="input", needs_desktop=True, changes_local=True,
                            evidence="none possible: input sent to the active window")
    if name in VISIBLE:
        return Capabilities(route="foreground", desktop="visible", needs_desktop=True, changes_local=True,
                            changes_external=name == "calendar_add_event",
                            evidence="the launched process or window" if name == "open_app" else "launch only")
    if name in BROWSER:
        visible = browser_visible
        return Capabilities(route="foreground" if visible else "headless_browser",
                            desktop="visible" if visible and name not in ("browser_close", "browser_hide") else "none",
                            changes_external=tool.risk != "safe", cancellable=False,
                            evidence="the page state after the action")
    if name in APP_AUTOMATION:
        return Capabilities(route="app_automation", changes_local=tool.risk != "safe",
                            evidence="UI Automation reports the control state")
    if name in EXTERNAL or (source.startswith(("account:", "home")) and tool.risk != "safe"):
        return Capabilities(route="api_mcp", changes_external=True, evidence="the service's response")
    if name in BACKGROUND_AGENT:
        return Capabilities(route="local", changes_local=True, evidence="the background agent's task record")
    route = "cli" if name in CLI else "api_mcp" if name in API or source.startswith(("account:", "home")) else "local"
    return Capabilities(route=route, changes_local=name in LOCAL_CHANGES or tool.risk != "safe",
                        cancellable=name in COOPERATIVE, rollback=name in ROLLBACK,
                        evidence=SELF_CHECKED.get(name, ""))


def executor_for(tool: "Tool", settings: "Settings | None" = None) -> ToolExecutor:
    """The router: pick the executor for a tool and declare what it can do (see ROUTE_ORDER)."""
    from ..tools.browser import BROWSER as SESSION
    from .verify import verifier_for

    caps = capabilities_for(tool, settings, browser_visible=tool.name in BROWSER and SESSION.visible)
    return ToolExecutor(tool, caps, verifier_for(tool.name, caps))
