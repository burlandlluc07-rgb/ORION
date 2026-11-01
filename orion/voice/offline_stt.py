"""Offline speech-to-text: NVIDIA Parakeet (TDT 0.6B v3, int8) running on the CPU through sherpa-onnx.

Optional. Nothing is downloaded until you press Download in Settings → Voice → Offline speech recognition, which
shows the size first. The model comes from sherpa-onnx's GitHub releases, is checked against a pinned SHA-256
before anything is unpacked, and lives in ORION's data folder (``models/``). Audio never leaves the PC.

What it's used for (classic voice mode, wake word and spoken approvals):

* **What you say** is transcribed here first; Gemini is only asked when this gives nothing (or isn't installed).
* **Spoken yes/no approvals** are decided here, so they work with no quota and no internet.
* **The wake word**: near-misses from Windows' recogniser get a second, more accurate listen; or, with the
  "Offline model" wake engine, every short utterance is transcribed and checked for a wake phrase.

Parakeet v3 understands 25 European languages (English, Spanish, French, German, Italian, Portuguese, Dutch,
Polish, Russian, Ukrainian…) and detects the language by itself. It needs about 0.9 GB of RAM while loaded and
transcribes a few seconds of speech in well under a second on a recent CPU.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger("orion.stt")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    url: str
    sha256: str
    download_bytes: int
    files: dict[str, int]  # file name → exact size, checked after unpacking and before every load
    ram_mb: int
    languages: str

    @property
    def disk_bytes(self) -> int:
        return sum(self.files.values())


PARAKEET = ModelSpec(
    id="parakeet-tdt-0.6b-v3-int8",
    label="NVIDIA Parakeet TDT 0.6B v3 (int8)",
    url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
    sha256="5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf",
    download_bytes=487_170_055,
    files={"encoder.int8.onnx": 652_184_281, "decoder.int8.onnx": 11_845_275, "joiner.int8.onnx": 6_355_277,
           "tokens.txt": 93_939},
    ram_mb=900,
    languages="25 European languages, including English, Spanish, French, German, Italian and Portuguese",
)


def runtime_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401  # type: ignore
    except Exception:
        return False
    return True


class ModelStore:
    """Download (with consent), verify, unpack, remove: one model, one folder under ``<data>/models``."""

    def __init__(self, root: Path | None = None, spec: ModelSpec = PARAKEET) -> None:
        self._root = root
        self.spec = spec
        self.state = "idle"  # idle | downloading | verifying | unpacking | error
        self.done_bytes = 0
        self.error: str | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.on_change: Callable[[dict], None] = lambda status: None

    @property
    def root(self) -> Path:
        if self._root is None:
            from ..config import data_dir

            self._root = data_dir() / "models"
        return self._root

    @property
    def folder(self) -> Path:
        return self.root / self.spec.id

    def installed(self) -> bool:
        try:
            return all((self.folder / n).stat().st_size == size for n, size in self.spec.files.items())
        except OSError:
            return False

    def status(self) -> dict:
        s = self.spec
        return {"model": s.id, "label": s.label, "installed": self.installed(), "runtime": runtime_available(),
                "state": self.state, "done_bytes": self.done_bytes, "download_bytes": s.download_bytes,
                "disk_bytes": s.disk_bytes, "ram_mb": s.ram_mb, "languages": s.languages, "source": s.url,
                "folder": str(self.folder), "error": self.error}

    def _changed(self) -> None:
        try:
            self.on_change(self.status())
        except Exception:  # pragma: no cover - a UI callback must never break the download
            log.debug("offline speech status callback failed", exc_info=True)

    # ── Download ───────────────────────────────────────────────────────────
    def start_download(self) -> None:
        """Start downloading in the background. Only call this after the user agreed to the download size."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self.installed():
                return
            self._cancel.clear()
            self.state, self.error, self.done_bytes = "downloading", None, 0
            self._thread = threading.Thread(target=self._download_safe, name="offline-stt-download", daemon=True)
            self._thread.start()
        self._changed()

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _download_safe(self) -> None:
        try:
            self._download()
            self.state = "idle"
        except _Cancelled:
            self.state, self.error = "idle", None
            log.info("Offline speech model download cancelled")
        except Exception as exc:
            self.state, self.error = "error", str(exc) or exc.__class__.__name__
            log.warning("Offline speech model download failed: %s", self.error)
        self._changed()

    def _download(self) -> None:
        s = self.spec
        self.root.mkdir(parents=True, exist_ok=True)
        need = s.download_bytes + s.disk_bytes + 50_000_000
        free = shutil.disk_usage(self.root).free
        part = self.root / f"{s.id}.tar.bz2.part"
        have = part.stat().st_size if part.exists() else 0
        if free + have < need:
            raise RuntimeError(f"Not enough free disk space: needs about {need / 1e9:.1f} GB, "
                               f"{free / 1e9:.1f} GB free on that drive.")
        self._fetch(part)
        self.state = "verifying"
        self._changed()
        digest = _sha256(part, self._cancel)
        if digest != s.sha256:
            part.unlink(missing_ok=True)
            raise RuntimeError("The download didn't match its published checksum, so it was deleted and nothing was "
                               "installed. Try again later.")
        self.state = "unpacking"
        self._changed()
        try:
            _unpack(part, self.folder, s.files)
        finally:
            part.unlink(missing_ok=True)
        (self.folder / "model.json").write_text(json.dumps({"id": s.id, "sha256": s.sha256, "source": s.url,
                                                            "installed": time.strftime("%Y-%m-%d %H:%M")}), "utf-8")
        log.info("Offline speech model installed in %s", self.folder)

    def _fetch(self, part: Path) -> None:
        """Stream the archive to ``part``, resuming a partial download when the server allows it."""
        import httpx

        s = self.spec
        have = part.stat().st_size if part.exists() else 0
        if have >= s.download_bytes:
            self.done_bytes = have
            return
        headers = {"Range": f"bytes={have}-"} if have else {}
        last = 0.0
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=60)) as client:
            with client.stream("GET", s.url, headers=headers) as r:
                if r.status_code == 200:
                    have = 0  # the server ignored the range: start over
                elif r.status_code != 206:
                    raise RuntimeError(f"Download failed (HTTP {r.status_code}).")
                with open(part, "ab" if have else "wb") as fh:
                    self.done_bytes = have
                    for chunk in r.iter_bytes(1 << 20):
                        if self._cancel.is_set():
                            raise _Cancelled()
                        fh.write(chunk)
                        self.done_bytes += len(chunk)
                        if self.done_bytes > s.download_bytes:
                            raise RuntimeError("The download is larger than expected; stopped.")
                        if time.monotonic() - last > 0.5:
                            last = time.monotonic()
                            self._changed()
        if self.done_bytes != s.download_bytes:
            raise RuntimeError("The download stopped early. Press Download again to carry on where it left off.")

    def remove(self) -> None:
        self.cancel()
        self.wait(10)
        shutil.rmtree(self.folder, ignore_errors=True)
        (self.root / f"{self.spec.id}.tar.bz2.part").unlink(missing_ok=True)
        self.state, self.error, self.done_bytes = "idle", None, 0
        self._changed()


class _Cancelled(Exception):
    pass


def _sha256(path: Path, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            h.update(chunk)
    return h.hexdigest()


def _unpack(archive: Path, dest: Path, files: dict[str, int]) -> None:
    """Extract only the expected files, by base name, into ``dest`` (no paths from the archive are used)."""
    tmp = dest.with_name(dest.name + ".unpacking")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            for m in tar:
                name = m.name.rsplit("/", 1)[-1]
                if name not in files or not m.isfile():
                    continue
                src = tar.extractfile(m)
                if src is None:
                    continue
                with src, open(tmp / name, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        for name, size in files.items():
            p = tmp / name
            if not p.exists() or p.stat().st_size != size:
                raise RuntimeError(f"The model archive is missing {name} or it has the wrong size.")
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Recognizer:
    """The loaded model. ``transcribe`` is thread-safe; loading takes a few seconds, so it's done once, up front."""

    def __init__(self, folder: Path, threads: int | None = None) -> None:
        import sherpa_onnx  # type: ignore

        threads = threads or max(1, min(4, (os.cpu_count() or 2) // 2))
        self._rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"),
            num_threads=threads, model_type="nemo_transducer", decoding_method="greedy_search")
        self._lock = threading.Lock()

    def transcribe(self, pcm: bytes, rate: int = 16_000) -> str:
        """16-bit mono PCM → text ("" for silence)."""
        if len(pcm) < 2:
            return ""
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            stream = self._rec.create_stream()
            stream.accept_waveform(rate, samples)
            self._rec.decode_stream(stream)
            return stream.result.text.strip()


def load(store: ModelStore) -> Recognizer:
    if not runtime_available():
        raise RuntimeError("The offline speech engine (sherpa-onnx) isn't part of this ORION build.")
    if not store.installed():
        raise RuntimeError("The offline speech model isn't downloaded yet.")
    t = time.monotonic()
    rec = Recognizer(store.folder)
    log.info("Offline speech model loaded in %.1f s", time.monotonic() - t)
    return rec


# ── Wake phrases in a transcript ───────────────────────────────────────────
_FILLER = {"ok", "okay", "um", "uh", "erm", "so", "oh", "and"}


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def match_wake(text: str, phrases: list[str], min_ratio: float = 0.8) -> tuple[str, str] | None:
    """Does the transcript start with a wake phrase? → (phrase, what followed it) or None.

    Tolerant of punctuation and small spelling differences ("Hey Orien, …"), and of one filler word in front
    ("Okay, hey Orion"). The phrase must open the sentence: "a documentary about Orion" doesn't wake ORION.
    """
    raw = text.strip()
    tokens = list(re.finditer(r"[\w']+", raw))
    if not tokens:
        return None
    best: tuple[float, str, int] | None = None
    for phrase in phrases:
        want = _words(phrase)
        if not want:
            continue
        for skip in (0, 1):
            if skip and (len(tokens) <= len(want) or tokens[0].group().lower() not in _FILLER):
                continue
            got = [t.group().lower() for t in tokens[skip:skip + len(want)]]
            if len(got) < len(want):
                continue
            ratio = difflib.SequenceMatcher(None, " ".join(got), " ".join(want)).ratio()
            last = difflib.SequenceMatcher(None, got[-1], want[-1]).ratio()  # the name itself must be close
            if ratio >= min_ratio and last >= 0.7 and (best is None or ratio > best[0]):
                best = (ratio, phrase, tokens[skip + len(want) - 1].end())
    if best is None:
        return None
    rest = raw[best[2]:].lstrip(" ,.!?:;-—").strip()
    return best[1], rest


STORE = ModelStore()
