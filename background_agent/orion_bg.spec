# PyInstaller spec: builds dist/orion-bg/orion-bg.exe (windowed, no console).
#   pyinstaller orion_bg.spec
# Run the exe with "run" (supervisor), "daemon", or any CLI command.
# CLI output needs a console, so build the separate console exe below for that.

hidden = [
    "apscheduler.triggers.cron", "apscheduler.triggers.interval", "apscheduler.triggers.date",
    "apscheduler.jobstores.sqlalchemy", "apscheduler.executors.pool",
    "sqlalchemy.dialects.sqlite",
    "google.genai", "winotify", "pystray._win32", "keyring.backends.Windows", "PIL.Image", "PIL.ImageDraw",
]

a = Analysis(["orion_bg_entry.py"], pathex=["."], hiddenimports=hidden, excludes=["tkinter"])
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="orion-bg", console=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="orion-bg-cli", console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="orion-bg")
