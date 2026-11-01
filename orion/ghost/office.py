"""Typing into Word and Excel ghosts through Office's own automation (COM): no keys, no mouse, no focus.

Word and Excel draw their documents themselves, so posted key messages don't reach them reliably. Their COM
interface does: Word's ``Selection.TypeText`` and Excel's cell values write straight into the document, and the
text appears live in the ghost window while you keep working."""

from __future__ import annotations

import queue
import threading

OFFICE_APPS = {"winword": "Word.Application", "excel": "Excel.Application"}


def office_kind(exe_stem: str) -> str:
    """'winword' / 'excel' for an Office program ORION can type into, else ''."""
    s = (exe_stem or "").lower()
    return s if s in OFFICE_APPS else ""


def word_lines(text: str) -> list[str]:
    """Text split into Word paragraphs (each line after the first starts a new paragraph)."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def excel_rows(text: str) -> list[list[str]]:
    """Text as cells: lines go down the rows, tabs across the columns, starting at the selected cell."""
    return [line.split("\t") for line in word_lines(text)]


# ORION's own Word / Excel, started through automation so they never touch documents you have open. They live on
# one COM thread; hwnd → (kind, application object).
SESSIONS: dict[int, tuple[str, object]] = {}
_jobs: "queue.Queue" = None  # type: ignore[assignment]
_lock = threading.Lock()


def _worker() -> None:
    import comtypes

    comtypes.CoInitialize()
    while True:
        fn, box, done = _jobs.get()
        try:
            box["v"] = fn()
        except BaseException as exc:  # handed back to the caller
            box["e"] = exc
        finally:
            done.set()


def _call(fn, timeout: float = 60.0):
    """Run ``fn`` on the Office COM thread (COM objects must stay on the thread that made them)."""
    global _jobs
    with _lock:
        if _jobs is None:
            _jobs = queue.Queue()
            threading.Thread(target=_worker, name="orion-office", daemon=True).start()
    box: dict = {}
    done = threading.Event()
    _jobs.put((fn, box, done))
    if not done.wait(timeout):
        raise TimeoutError("Office didn't answer in time.")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def open_office(kind: str) -> int:
    """Start ORION's own Word (with a blank document) or Excel (with a blank workbook); returns its window."""
    def job():
        import comtypes.client

        app = comtypes.client.CreateObject(OFFICE_APPS[kind], dynamic=True)
        app.Visible = True
        if kind == "winword":
            app.Documents.Add()
            hwnd = int(app.ActiveWindow.Hwnd)
        else:
            app.Workbooks.Add()
            hwnd = int(app.Hwnd)
        SESSIONS[hwnd] = (kind, app)
        return hwnd

    return _call(job)


def close_office(hwnd: int) -> bool:
    """Quit ORION's own Word/Excel without saving (he saves on purpose, never by accident). False if not his."""
    if hwnd not in SESSIONS:
        return False

    def job():
        kind, app = SESSIONS.pop(hwnd)
        if kind == "winword":
            app.Quit(0)  # wdDoNotSaveChanges
        else:
            app.DisplayAlerts = False
            app.Quit()
        return True

    return bool(_call(job))


def _items(collection) -> list:
    return [collection.Item(i) for i in range(1, int(collection.Count) + 1)]


def type_into(kind: str, hwnd: int, text: str) -> str:
    """Write ``text`` into the Word or Excel window ``hwnd``. Raises on any COM failure."""
    return _call(lambda: _type_into(kind, hwnd, text))


def _type_into(kind: str, hwnd: int, text: str) -> str:
    if hwnd in SESSIONS:
        app = SESSIONS[hwnd][1]
    else:  # a Word/Excel that was already running: Office's running-object entry
        import comtypes.client

        app = comtypes.client.GetActiveObject(OFFICE_APPS[kind], dynamic=True)
    if kind == "winword":
        if int(app.Documents.Count) == 0:
            app.Documents.Add()  # Word started on its start screen: give it a blank document
        win = next((w for w in _items(app.Windows) if int(w.Hwnd) == int(hwnd)), None) or app.ActiveWindow
        sel = win.Selection
        for i, line in enumerate(word_lines(text)):
            if i:
                sel.TypeParagraph()
            if line:
                sel.TypeText(line)
        return f"Typed {len(text)} characters into the Word document “{win.Caption}”."
    if int(app.Workbooks.Count) == 0:
        app.Workbooks.Add()
    start = app.ActiveCell
    sheet = start.Worksheet
    r0, c0 = int(start.Row), int(start.Column)
    rows = excel_rows(text)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.Cells(r0 + r, c0 + c).Value = value
    return f"Filled {sum(len(r) for r in rows)} cells in Excel from {start.Address(False, False)}."
