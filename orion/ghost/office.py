"""Typing into Word and Excel ghosts through Office's own automation (COM): no keys, no mouse, no focus.

Word and Excel draw their documents themselves, so posted key messages don't reach them reliably. Their COM
interface does: Word's ``Selection.TypeText`` and Excel's cell values write straight into the document, and the
text appears live in the ghost window while you keep working."""

from __future__ import annotations

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


def _items(collection) -> list:
    return [collection.Item(i) for i in range(1, int(collection.Count) + 1)]


def type_into(kind: str, hwnd: int, text: str) -> str:
    """Write ``text`` into the Word or Excel window ``hwnd``. Raises on any COM failure."""
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        app = comtypes.client.GetActiveObject(OFFICE_APPS[kind])
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
    finally:
        comtypes.CoUninitialize()
