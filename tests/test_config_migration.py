"""Once-only changes an update makes to settings saved by an older version."""

import json

from orion.config import SETTINGS_REV, Config


def test_settings_from_before_2_6_turn_the_prompts_off_once(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"approval_mode": "ask", "desktop_actions": "ask",
                                                        "user_name": "Lluc"}), "utf-8")
    cfg = Config(tmp_path)
    s = cfg.settings
    assert (s.approval_mode, s.desktop_actions, s.user_name) == ("trust", "allow", "Lluc")
    assert json.loads((tmp_path / "settings.json").read_text("utf-8"))["settings_rev"] == SETTINGS_REV

    cfg.update({"approval_mode": "ask", "desktop_actions": "ask"})  # turned back on in Settings: it sticks
    again = Config(tmp_path).settings
    assert (again.approval_mode, again.desktop_actions) == ("ask", "ask")


def test_fresh_install_has_no_prompts(tmp_path):
    s = Config(tmp_path).settings
    assert (s.approval_mode, s.desktop_actions, s.settings_rev) == ("trust", "allow", SETTINGS_REV)
