"""File tools with guard rails.

* Reads are allowed anywhere the user can read.
* Writes are limited to the user's home folder plus folders added in Settings.
* Deletes go to the Recycle Bin (send2trash) — never permanent.
* Writes are verified by reading the file back.
* Search uses Everything (es.exe) when installed — instant on any disk size —
  and falls back to a bounded directory walk.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import time
from pathlib import Path

from .platform import IS_WIN, home, open_target, resolve_path, run
from .registry import ToolContext, ToolError, tool

TEXT_LIMIT = 60_000
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "AppData", "$Recycle.Bin", ".venv", "venv", ".cache",
             "Library", "site-packages", "Windows", "Program Files", "Program Files (x86)"}

_extra_roots: list[Path] = []
_data_dir: Path | None = None
JOURNAL_MAX = 50


def set_data_dir(root: Path) -> None:
    """Where the change journal and backups for undo live."""
    global _data_dir
    _data_dir = root
    (root / "backups").mkdir(parents=True, exist_ok=True)


def _journal_path() -> Path | None:
    return _data_dir / "changes.json" if _data_dir else None


def _journal() -> list[dict]:
    import json

    p = _journal_path()
    try:
        return json.loads(p.read_text("utf-8")) if p and p.exists() else []
    except ValueError:
        return []


def _record(entry: dict) -> None:
    import json

    p = _journal_path()
    if not p:
        return
    items = (_journal() + [{**entry, "ts": time.time()}])[-JOURNAL_MAX:]
    p.write_text(json.dumps(items, indent=1), "utf-8")


def _backup(path: Path) -> str | None:
    if not (_data_dir and path.is_file()) or path.stat().st_size > 50 * 2**20:
        return None
    dest = _data_dir / "backups" / f"{int(time.time() * 1000)}_{path.name}"
    shutil.copy2(path, dest)
    return str(dest)


def set_allowed_roots(paths: list[str]) -> None:
    _extra_roots.clear()
    _extra_roots.extend(Path(p).expanduser().resolve() for p in paths if p)


def _check_writable(p: Path) -> None:
    roots = [home().resolve(), *_extra_roots]
    if not any(p == r or r in p.parents for r in roots):
        raise ToolError(
            f"{p} is outside the folders ORION may modify. Add its folder under Settings → Files to allow it."
        )


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


@tool(category="files")
def list_folder(path: str = "desktop", pattern: str = "*") -> dict:
    """List files and folders in a directory. Friendly names like "downloads" or "desktop/projects" work.

    Args:
        path: Folder path or friendly name.
        pattern: Optional glob filter such as "*.pdf".
    """
    p = resolve_path(path)
    if not p.is_dir():
        raise ToolError(f"{p} is not a folder.")
    entries = []
    for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        if not fnmatch.fnmatch(e.name.lower(), pattern.lower()):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        entries.append({
            "name": e.name + ("/" if e.is_dir() else ""),
            "size": "" if e.is_dir() else _human(st.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })
        if len(entries) >= 200:
            break
    return {"folder": str(p), "count": len(entries), "entries": entries}


def _everything_search(query: str, limit: int) -> list[str] | None:
    es = shutil.which("es") or shutil.which("es.exe")
    if not (IS_WIN and es):
        return None
    try:
        res = run([es, "-n", str(limit), query], timeout=6)
        return [line for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        return None


@tool(category="files")
def search_files(query: str, folder: str = "home", limit: int = 25, ctx: ToolContext = None) -> dict:  # type: ignore[assignment]
    """Find files or folders by name (supports wildcards like "*.pdf" or "budget*2025*").

    Args:
        query: Name fragment or wildcard pattern.
        folder: Where to look (path or friendly name). "home" searches the whole user folder.
        limit: Maximum results.
    """
    limit = max(1, min(limit, 100))
    root = resolve_path(folder) if folder != "home" else home()
    hits = _everything_search(f'"{root}" {query}', limit)
    if hits is not None:
        return {"engine": "everything", "results": hits}
    pattern = query.lower() if any(c in query for c in "*?[") else f"*{query.lower()}*"
    results: list[str] = []
    deadline = time.monotonic() + 12
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in dirnames + filenames:
            if fnmatch.fnmatch(name.lower(), pattern):
                results.append(os.path.join(dirpath, name))
                if len(results) >= limit:
                    return {"engine": "walk", "results": results}
        if time.monotonic() > deadline:
            return {"engine": "walk", "results": results, "note": "Search stopped after 12s; narrow the folder."}
        if ctx is not None:
            ctx.check_cancelled()
    return {"engine": "walk", "results": results}


def read_text_any(p: Path) -> str:
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise ToolError("PDF support needs the 'pypdf' package (pip install pypdf).") from exc
        reader = PdfReader(str(p))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages[:60])
    if suffix == ".docx":
        try:
            import docx  # type: ignore
        except ImportError as exc:
            raise ToolError("Word support needs 'python-docx' (pip install python-docx).") from exc
        return "\n".join(par.text for par in docx.Document(str(p)).paragraphs)
    data = p.read_bytes()[: TEXT_LIMIT * 4]
    if b"\x00" in data[:4096]:
        raise ToolError(f"{p.name} is a binary file ({_human(p.stat().st_size)}); it can't be read as text.")
    return data.decode("utf-8", errors="replace")


@tool(category="files")
def read_file(path: str) -> str:
    """Read a text, code, Markdown, CSV, PDF or Word file.

    Args:
        path: File path or friendly path like "desktop/todo.txt".
    """
    p = resolve_path(path)
    if not p.is_file():
        raise ToolError(f"File not found: {p}")
    text = read_text_any(p)
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT] + f"\n…[truncated, file is {_human(p.stat().st_size)}]"
    return text


@tool(risk="confirm", category="files", summary=lambda a: f"{'Append to' if a.get('append') else 'Write'} {a.get('path')}")
def write_file(path: str, content: str, append: bool = False) -> str:
    """Create or overwrite a text file (or append to it). The result is verified by reading it back.

    Args:
        path: Destination path or friendly path.
        content: Full text to write.
        append: Append instead of overwrite.
    """
    p = resolve_path(path)
    _check_writable(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    backup = _backup(p) if existed else None
    before = p.read_bytes() if (append and p.exists()) else b""
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(content)
    after = p.read_bytes()
    expected = before + content.encode("utf-8")
    if after != expected:
        raise ToolError(f"Wrote {p} but the read-back check did not match.")
    _record({"op": "write", "path": str(p), "backup": backup} if existed else {"op": "create", "path": str(p)})
    return f"Saved and verified {p} ({_human(len(after))})."


@tool(risk="confirm", category="files", summary=lambda a: f"Create folder {a.get('path')}")
def create_folder(path: str) -> str:
    """Create a folder (and any missing parents).

    Args:
        path: Folder path or friendly path.
    """
    p = resolve_path(path)
    _check_writable(p)
    existed = p.exists()
    p.mkdir(parents=True, exist_ok=True)
    if not existed:
        _record({"op": "mkdir", "path": str(p)})
    if not p.is_dir():
        raise ToolError("Folder creation could not be verified.")
    return f"Folder ready: {p}"


def _digest(p: Path) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@tool(risk="confirm", category="files", summary=lambda a: f"{a.get('operation', 'move').title()} {a.get('source')} → {a.get('destination')}")
def move_or_copy(source: str, destination: str, operation: str = "move") -> str:
    """Move, copy or rename a file or folder. Verifies the result.

    Args:
        source: Existing file/folder.
        destination: New path, or an existing folder to place it in.
        operation: "move", "copy" or "rename".
    """
    src, dst = resolve_path(source), resolve_path(destination)
    if not src.exists():
        raise ToolError(f"Source not found: {src}")
    if dst.is_dir():
        dst = dst / src.name
    _check_writable(dst)
    if operation != "copy":
        _check_writable(src)
    if dst.exists():
        raise ToolError(f"{dst} already exists; choose another name.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_hash = _digest(src) if src.is_file() else None
    if operation == "copy":
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    else:
        shutil.move(str(src), str(dst))
    if src_hash and _digest(dst) != src_hash:
        raise ToolError("Content check failed after the operation.")
    if operation != "copy" and src.exists():
        raise ToolError("The original is still present after the move.")
    _record({"op": "copy", "path": str(dst)} if operation == "copy" else {"op": "move", "src": str(src), "dst": str(dst)})
    return f"{operation.title()} complete and verified: {dst}"


@tool(risk="danger", category="files", summary=lambda a: f"Send {a.get('path')} to the Recycle Bin")
def delete_path(path: str) -> str:
    """Delete a file or folder by sending it to the Recycle Bin (recoverable).

    Args:
        path: File or folder to delete.
    """
    from send2trash import send2trash

    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"Not found: {p}")
    _check_writable(p)
    if p in (home().resolve(), *(Path(x).resolve() for x in ("/", "C:\\"))):
        raise ToolError("Refusing to delete a root or home folder.")
    send2trash(str(p))
    _record({"op": "trash", "path": str(p)})
    return f"Moved {p.name} to the Recycle Bin."


@tool(category="files", summary=lambda a: f"Open {a.get('path')}")
def open_path(path: str) -> str:
    """Open a file with its default app, or a folder in the file explorer.

    Args:
        path: File/folder path or friendly name like "downloads".
    """
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"Not found: {p}")
    open_target(str(p))
    return f"Opened {p}."


@tool(category="files")
def disk_usage(folder: str = "downloads", top: int = 15) -> dict:
    """Find what's taking up space: the biggest items inside a folder.

    Args:
        folder: Folder to analyse.
        top: How many of the largest items to list.
    """
    root = resolve_path(folder)
    sizes: list[tuple[int, str]] = []
    deadline = time.monotonic() + 15
    for child in root.iterdir():
        total = 0
        if child.is_file():
            total = child.stat().st_size
        else:
            for dp, _dn, fns in os.walk(child):
                for fn in fns:
                    try:
                        total += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        pass
                if time.monotonic() > deadline:
                    break
        sizes.append((total, child.name + ("/" if child.is_dir() else "")))
    sizes.sort(reverse=True)
    return {"folder": str(root), "largest": [{"name": n, "size": _human(s)} for s, n in sizes[:top]]}


def _describe(c: dict) -> str:
    return {"write": f"Overwrote {c.get('path')}", "create": f"Created {c.get('path')}", "mkdir": f"Created folder {c.get('path')}",
            "copy": f"Copied to {c.get('path')}", "move": f"Moved {c.get('src')} → {c.get('dst')}",
            "trash": f"Deleted {c.get('path')} (in the Recycle Bin)"}.get(c["op"], c["op"])


@tool(category="files")
def recent_file_changes(limit: int = 10) -> list[dict]:
    """List the most recent file changes ORION made (newest first), which undo_last_change can reverse.

    Args:
        limit: How many to show.
    """
    items = _journal()[-max(1, min(limit, JOURNAL_MAX)):]
    return [{"change": _describe(c), "when": time.strftime("%H:%M", time.localtime(c["ts"]))} for c in reversed(items)] \
        or [{"change": "No file changes recorded yet."}]


@tool(risk="confirm", category="files", summary=lambda a: "Undo ORION's last file change")
def undo_last_change() -> str:
    """Reverse the most recent file change ORION made (restore an overwritten file, move a file back, remove a file it created)."""
    import json

    from send2trash import send2trash

    items = _journal()
    if not items:
        raise ToolError("There's nothing to undo.")
    c = items.pop()
    op = c["op"]
    if op == "write":
        if not c.get("backup") or not Path(c["backup"]).exists():
            raise ToolError("No backup exists for that change (file was too large).")
        shutil.copy2(c["backup"], c["path"])
        msg = f"Restored the previous version of {c['path']}."
    elif op in ("create", "copy"):
        target = Path(c["path"])
        if target.exists():
            send2trash(str(target))
        msg = f"Removed {target.name} (it's in the Recycle Bin if you need it)."
    elif op == "mkdir":
        target = Path(c["path"])
        if target.exists() and any(target.iterdir()):
            raise ToolError(f"{target} isn't empty any more; not removing it.")
        if target.exists():
            target.rmdir()
        msg = f"Removed the folder {target}."
    elif op == "move":
        src, dst = Path(c["src"]), Path(c["dst"])
        if not dst.exists():
            raise ToolError(f"{dst} no longer exists.")
        if src.exists():
            raise ToolError(f"Something already exists at {src}; not overwriting it.")
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        msg = f"Moved it back to {src}."
    elif op == "trash":
        msg = f"{Path(c['path']).name} is in the Recycle Bin — open the Recycle Bin and choose Restore."
    else:
        raise ToolError(f"Can't undo '{op}'.")
    p = _journal_path()
    if p:
        p.write_text(json.dumps(items, indent=1), "utf-8")
    return msg
