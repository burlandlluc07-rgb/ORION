"""Documents toolkit, part 2: images, archives, downloads and searching inside files."""

from __future__ import annotations

import fnmatch
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Literal

from .files import SKIP_DIRS, _backup, _check_writable, _human, _record, read_text_any
from .office import IMAGE_EXTS
from .platform import resolve_path
from .registry import ToolContext, ToolError, tool

KIT = "documents"
FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF", "tiff": "TIFF",
           "pdf": "PDF", "ico": "ICO"}


def _image_inputs(path: str) -> list[Path]:
    """A file, a folder (all images in it) or a wildcard like "pictures/*.png"."""
    raw = path.strip()
    if any(ch in raw for ch in "*?"):
        base = resolve_path(os.path.dirname(raw) or ".")
        pat = os.path.basename(raw).lower()
        files = [p for p in base.iterdir() if p.is_file() and fnmatch.fnmatch(p.name.lower(), pat)]
    else:
        p = resolve_path(raw)
        if p.is_dir():
            files = [f for f in p.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS]
        elif p.is_file():
            files = [p]
        else:
            raise ToolError(f"Not found: {p}")
    files = [f for f in files if f.suffix.lower() in IMAGE_EXTS | {".heic"}]
    if not files:
        raise ToolError("No images found there.")
    return sorted(files)[:500]


@tool(risk="confirm", category="documents", kit=KIT,
      summary=lambda a: f"Edit image(s): {a.get('path')}")
def edit_images(path: str, output_folder: str = "", width: int = 0, height: int = 0, scale_percent: int = 0,
                convert_to: Literal["", "jpg", "png", "webp", "bmp", "gif", "tiff", "pdf", "ico"] = "",
                quality: int = 85, rotate: int = 0, crop: str = "", grayscale: bool = False,
                ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Resize, convert, compress, rotate, crop or grayscale one image or a whole batch. Results are saved as new
    files (originals are kept) with "-edited" added, or into output_folder.

    Args:
        path: An image, a folder of images, or a wildcard like "pictures/holiday/*.jpg".
        output_folder: Where to save the results (default: next to each original).
        width: Target width in pixels (keeps aspect ratio if height is 0).
        height: Target height in pixels (keeps aspect ratio if width is 0).
        scale_percent: Resize by percentage instead, e.g. 50.
        convert_to: New format. JPG/WEBP with a lower quality also compresses.
        quality: 1-100 for JPG/WEBP (lower = smaller file).
        rotate: Degrees clockwise (90, 180, 270).
        crop: "left,top,right,bottom" in pixels.
        grayscale: Convert to black and white.
    """
    from PIL import Image, ImageOps

    files = _image_inputs(path)
    out_dir = resolve_path(output_folder) if output_folder else None
    if out_dir:
        _check_writable(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    done, before, after = [], 0, 0
    for i, src in enumerate(files):
        if ctx is not None:
            ctx.check_cancelled()
            ctx.progress(f"{i + 1}/{len(files)} {src.name}")
        ext = (convert_to or src.suffix.lstrip(".")).lower()
        fmt = FORMATS.get(ext)
        if not fmt:
            raise ToolError(f"Can't save .{ext} images.")
        dest_dir = out_dir or src.parent
        stem = src.stem if out_dir else f"{src.stem}-edited"
        dest = dest_dir / f"{stem}.{ 'jpg' if ext == 'jpeg' else ext}"
        _check_writable(dest)
        try:
            im = Image.open(src)
        except Exception as exc:
            raise ToolError(f"Can't open {src.name}: {exc}") from exc
        with im:
            im = ImageOps.exif_transpose(im)
            if crop:
                box = [int(float(v)) for v in re.split(r"[,\s]+", crop.strip()) if v]
                if len(box) != 4:
                    raise ToolError("crop must be \"left,top,right,bottom\".")
                im = im.crop(tuple(box))
            if rotate % 360:
                im = im.rotate(-(rotate % 360), expand=True)
            if scale_percent:
                im = im.resize((max(1, im.width * scale_percent // 100), max(1, im.height * scale_percent // 100)),
                               Image.LANCZOS)
            elif width or height:
                w = width or round(im.width * height / im.height)
                h = height or round(im.height * width / im.width)
                im = im.resize((max(1, w), max(1, h)), Image.LANCZOS)
            if grayscale:
                im = ImageOps.grayscale(im)
            if fmt in ("JPEG", "PDF", "BMP") and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            existed = dest.exists()
            backup = _backup(dest) if existed else None
            opts = {"quality": max(1, min(quality, 100)), "optimize": True} if fmt in ("JPEG", "WEBP") else {}
            im.save(dest, fmt, **opts)
            size = im.size
        before += src.stat().st_size
        after += dest.stat().st_size
        _record({"op": "write", "path": str(dest), "backup": backup} if existed else {"op": "create", "path": str(dest)})
        done.append(f"{dest.name} ({size[0]}×{size[1]}, {_human(dest.stat().st_size)})")
    where = out_dir or files[0].parent
    head = f"Saved {len(done)} image(s) in {where}: " + ", ".join(done[:8]) + (" …" if len(done) > 8 else "")
    return head + f". Total {_human(before)} → {_human(after)}."


@tool(risk="confirm", category="documents", kit=KIT, summary=lambda a: f"Zip into {a.get('output')}")
def zip_files(paths: list[str], output: str) -> str:
    """Compress files and/or folders into a .zip archive.

    Args:
        paths: Files and folders to include.
        output: The .zip to create, e.g. "desktop/Project backup.zip".
    """
    dest = resolve_path(output)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    _check_writable(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    existed = dest.exists()
    backup = _backup(dest) if existed else None
    count, raw = 0, 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in paths:
            src = resolve_path(item)
            if not src.exists():
                raise ToolError(f"Not found: {src}")
            if src.resolve() == dest.resolve():
                continue
            if src.is_dir():
                for root, dirs, files in os.walk(src):
                    dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
                    for f in files:
                        fp = Path(root) / f
                        if fp.resolve() == dest.resolve():
                            continue
                        zf.write(fp, Path(src.name) / fp.relative_to(src))
                        count += 1
                        raw += fp.stat().st_size
            else:
                zf.write(src, src.name)
                count += 1
                raw += src.stat().st_size
    with zipfile.ZipFile(dest) as zf:
        if zf.testzip() is not None:
            raise ToolError("The archive failed its integrity check.")
    _record({"op": "write", "path": str(dest), "backup": backup} if existed else {"op": "create", "path": str(dest)})
    return f"Zipped {count} file(s) into {dest} ({_human(raw)} → {_human(dest.stat().st_size)})."


@tool(risk="confirm", category="documents", kit=KIT, summary=lambda a: f"Extract {a.get('path')}")
def extract_archive(path: str, destination: str = "") -> str:
    """Extract a .zip (also .tar, .tar.gz) archive into a folder.

    Args:
        path: The archive.
        destination: Folder to extract into (default: a folder named after the archive, next to it).
    """
    import shutil
    import tarfile

    src = resolve_path(path)
    if not src.is_file():
        raise ToolError(f"File not found: {src}")
    name = re.sub(r"\.(zip|tar|tgz|tar\.gz|tar\.bz2|tar\.xz)$", "", src.name, flags=re.I)
    dest = resolve_path(destination) if destination else src.parent / name
    _check_writable(dest)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as zf:
            members = zf.infolist()
            for m in members:  # refuse "zip slip" paths that would escape the destination
                if not (root / m.filename).resolve().is_relative_to(root):
                    raise ToolError(f"Unsafe path inside the archive: {m.filename}")
            zf.extractall(dest)
            count = len([m for m in members if not m.is_dir()])
    elif tarfile.is_tarfile(src):
        with tarfile.open(src) as tf:
            tf.extractall(dest, filter="data")
            count = len([m for m in tf.getmembers() if m.isfile()])
    else:
        raise ToolError(f"{src.name} isn't a zip or tar archive. (.rar/.7z need 7-Zip: ask to install it.)")
    if not any(dest.iterdir()):
        shutil.rmtree(dest, ignore_errors=True)
        raise ToolError("Nothing was extracted.")
    _record({"op": "mkdir", "path": str(dest)})
    return f"Extracted {count} file(s) to {dest}."


@tool(risk="confirm", category="documents", kit=KIT, summary=lambda a: f"Download {a.get('url')}")
def download_file(url: str, folder: str = "downloads", filename: str = "", ctx: ToolContext = None) -> str:  # type: ignore[assignment]
    """Download a file from the internet (a PDF, image, installer, dataset…) into a folder.

    Args:
        url: Direct link to the file (http/https).
        folder: Where to save it.
        filename: Optional file name (default: from the link).
    """
    import httpx

    if not re.match(r"^https?://", url.strip(), re.I):
        raise ToolError("Only http(s) links can be downloaded.")
    out_dir = resolve_path(folder)
    _check_writable(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = 4 * 2**30
    with httpx.stream("GET", url.strip(), follow_redirects=True, timeout=30,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ORION"}) as r:
        if r.status_code >= 400:
            raise ToolError(f"The server answered {r.status_code}.")
        name = filename
        if not name:
            cd = r.headers.get("content-disposition", "")
            m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd, re.I)
            name = m.group(1) if m else (Path(r.url.path).name or "download")
        name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "download"
        dest = out_dir / name
        if dest.exists():
            dest = out_dir / f"{dest.stem} ({int(time.time())}){dest.suffix}"
        total = int(r.headers.get("content-length") or 0)
        if total > limit:
            raise ToolError(f"That file is {_human(total)}; downloads are limited to {_human(limit)}.")
        got, last = 0, 0.0
        tmp = dest.with_name(dest.name + ".part")
        try:
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    fh.write(chunk)
                    got += len(chunk)
                    if got > limit:
                        raise ToolError("Download too large.")
                    if ctx is not None:
                        ctx.check_cancelled()
                        if time.monotonic() - last > 0.5:
                            ctx.progress(f"{_human(got)}" + (f" of {_human(total)}" if total else ""))
                            last = time.monotonic()
            # Content-Length counts bytes on the wire, which are compressed when the server gzips the response.
            if total and r.num_bytes_downloaded < total:
                raise ToolError(f"Download incomplete ({_human(r.num_bytes_downloaded)} of {_human(total)}).")
            tmp.replace(dest)
        finally:
            if tmp.exists():
                tmp.unlink()
    _record({"op": "create", "path": str(dest)})
    return f"Downloaded {dest} ({_human(dest.stat().st_size)})."


TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".css", ".xml", ".yml", ".yaml",
             ".ini", ".cfg", ".rtf", ".pdf", ".docx", ".xlsx", ".pptx"}


@tool(category="documents", kit=KIT)
def search_inside_files(text: str, folder: str = "documents", file_types: str = "", limit: int = 20,
                        ctx: ToolContext = None) -> list[dict] | str:  # type: ignore[assignment]
    """Find files whose *contents* mention something (Word, PDF, Excel, PowerPoint, text and code files),
    with a snippet around the match. Use search_files to find files by name instead.

    Args:
        text: Words or a phrase to look for (case-insensitive).
        folder: Folder to search, including subfolders.
        file_types: Optional comma-separated extensions, e.g. "pdf,docx".
        limit: Max files to return.
    """
    root = resolve_path(folder)
    if not root.is_dir():
        raise ToolError(f"Folder not found: {root}")
    exts = {("." + e.strip().lstrip(".").lower()) for e in file_types.split(",") if e.strip()} or TEXT_EXTS
    needle = text.lower().strip()
    if not needle:
        raise ToolError("Give some text to look for.")
    deadline = time.monotonic() + 40
    hits: list[dict] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            fp = Path(dirpath) / name
            if fp.suffix.lower() not in exts:
                continue
            try:
                if fp.stat().st_size > 25 * 2**20:
                    continue
                body = read_text_any(fp)
            except Exception:
                continue
            scanned += 1
            idx = body.lower().find(needle)
            if idx >= 0:
                snippet = re.sub(r"\s+", " ", body[max(0, idx - 90): idx + len(needle) + 120]).strip()
                hits.append({"file": str(fp), "snippet": snippet})
                if len(hits) >= limit:
                    return hits
            if ctx is not None:
                ctx.check_cancelled()
            if time.monotonic() > deadline:
                return hits or f"Searched {scanned} files for 40s without a match; try a narrower folder."
    return hits or f"No file in {root} mentions “{text}” ({scanned} files checked)."
