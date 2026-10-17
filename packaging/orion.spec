# PyInstaller spec — builds dist/ORION/ORION.exe (one-folder: fast start-up, AV-friendly).
#   pip install -e .[dev,docs]  &&  pyinstaller packaging/orion.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

hidden = (
    collect_submodules("orion")
    + collect_submodules("uvicorn")
    + collect_submodules("webview")
    + collect_submodules("pystray")
    + collect_submodules("keyring.backends")
    + ["pycaw.pycaw", "comtypes.stream", "pywinauto", "screen_brightness_control", "miniaudio", "edge_tts", "webrtcvad", "ddgs",
       "mcp.client.stdio", "mcp.client.streamable_http", "qrcode.image.svg", "pywebpush", "py_vapid", "icalendar", "recurring_ical_events", "openwakeword", "pypdf", "docx", "openpyxl", "pptx", "fpdf"]
    + collect_submodules("playwright")
)
datas = [
    (str(ROOT / "orion" / "web"), "orion/web"),
    (str(ROOT / "orion" / "assets"), "orion/assets"),
    (str(ROOT / "orion" / "builtin_skills"), "orion/builtin_skills"),
]
for pkg in ("_sounddevice_data", "webview", "certifi", "edge_tts", "pptx", "docx", "fpdf"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# Playwright's driver (node + its JS package) drives the installed Microsoft Edge; no browser is bundled.
try:
    datas += collect_data_files("playwright", include_py_files=False)
except Exception:
    pass

a = Analysis(
    [str(ROOT / "orion" / "__main__.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="ORION", console=False,
    icon=str(ROOT / "orion" / "assets" / "orion.ico"), version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ORION")
