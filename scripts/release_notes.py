"""Write dist/RELEASE_NOTES.md from the newest CHANGELOG section (used by the release workflow)."""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orion import __version__  # noqa: E402

text = Path("CHANGELOG.md").read_text("utf-8")
m = re.search(r"^## .*?(?=^## )", text, re.S | re.M)
notes = (m.group(0) if m else text).strip()
notes += (f"\n\n### Install\nDownload **ORION-Setup-{__version__}.exe** below and run it (Windows 10/11, 64-bit). "
          "You'll need a free Gemini API key from https://aistudio.google.com.\n")
Path("dist").mkdir(exist_ok=True)
Path("dist/RELEASE_NOTES.md").write_text(notes, "utf-8")
print(__version__)
