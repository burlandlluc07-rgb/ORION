from orion.desktop import titlebar


def test_theme_rule_matches_ui():
    assert titlebar.is_dark("dark")
    assert titlebar.is_dark(None)
    assert not titlebar.is_dark("light")


def test_colorref_is_bgr():
    assert titlebar._colorref("#0e0e0e") == 0x0E0E0E
    assert titlebar._colorref("#ff0080") == 0x8000FF


def test_noop_off_windows():
    assert titlebar.find_window("ORION") == 0
    assert titlebar.style(0, True) is False
