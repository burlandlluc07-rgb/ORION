"""ORION's second brain: an Obsidian vault of plain Markdown notes on this PC.

Two tiers:

* **Permanent**: notes that last: ``About me`` (profile + preferences), ``People/<name>``, ``Projects/<name>``,
  ``Plans & dates`` and ``Facts``, linked from a ``Home`` note so Obsidian's graph shows how it all connects.
* **Short-term**: ``Short-term/This week.md``, a temporary file for passing details. Once a week ORION reviews
  it item by item: worth keeping → moved into the right permanent note; still coming up → carried over;
  otherwise deleted. Every review is logged in ``ORION/Review log.md``.

The vault is the source of truth: edit or delete anything in Obsidian and ORION picks it up. SQLite only holds
a search index (FTS5) mirrored from the notes, so recall stays instant.
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine import Engine

log = logging.getLogger("orion.vault")

KINDS = {"profile", "preference", "person", "project", "plan", "fact"}
SHORT_NOTE = "Short-term/This week.md"
LOG_NOTE = "ORION/Review log.md"
HOME_NOTE = "Home.md"
ABOUT_NOTE = "About me.md"
FLAT_NOTES = {"plan": "Plans & dates.md", "fact": "Facts.md"}
DATE_PREFIX = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\]\s*")
MAX_ENTRY = 500

REVIEW_PROMPT = """You are reviewing the SHORT-TERM memory of a personal assistant. Today is {today}.
Decide for every item below:
- "keep": worth remembering for months (who the user is, people in their life, ongoing projects, lasting
  preferences, important dates). Rewrite it as one clear, self-contained statement.
- "carry": not lasting, but still relevant in the coming days (e.g. an appointment next week). Keep it short-term.
- "discard": done, trivial, outdated or already known.
Never keep passwords, keys, card numbers or similar secrets. Already in permanent memory (don't duplicate):
{known}

Short-term items:
{items}

Return JSON: {{"decisions": [{{"n": <item number>, "action": "keep|carry|discard", "text": "<statement, for keep>",
"kind": "profile|preference|person|project|plan|fact", "subject": "<person or project name, for person/project>"}}]}}"""


def is_duplicate(text: str, known: list[str]) -> bool:
    t = text.lower().strip(" .")
    return any(difflib.SequenceMatcher(None, t, k.lower().strip(" .")).ratio() > 0.82 for k in known)


def _safe_title(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", name).strip(" .")
    return re.sub(r"\s+", " ", name)[:80] or "Untitled"


def _key(note: str, text: str) -> str:
    return hashlib.sha1(f"{note}\n{text}".encode()).hexdigest()[:16]


def _clean_line(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
    s = re.sub(r"^\[[ xX]\]\s+", "", s)
    s = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", r"\2", s)  # [[People/Sarah|Sarah]] → Sarah
    return s.strip()


class Vault:
    def __init__(self, engine: "Engine") -> None:
        self.e = engine
        self._lock = threading.RLock()
        self._sig: tuple | None = None
        self._last_check = 0.0

    # ── location & layout ──────────────────────────────────────────────────
    @property
    def root(self) -> Path:
        from ..tools.platform import known_folder

        raw = (self.e.config.settings.vault_path or "").strip()
        if not raw:
            return (known_folder("documents") or Path.home() / "Documents") / "ORION Brain"
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        # Pointing at an existing Obsidian vault: ORION keeps to its own folder inside it.
        if (p / ".obsidian").is_dir() and p.name != "ORION Brain":
            p = p / "ORION Brain"
        return p

    def _note_for(self, kind: str, subject: str = "") -> str:
        if kind in ("profile", "preference"):
            return ABOUT_NOTE
        if kind == "person" and subject.strip():
            return f"People/{_safe_title(subject)}.md"
        if kind == "project" and subject.strip():
            return f"Projects/{_safe_title(subject)}.md"
        return FLAT_NOTES.get(kind, FLAT_NOTES["fact"])

    @staticmethod
    def _frontmatter(kind: str, extra: str = "") -> str:
        today = dt.date.today().isoformat()
        return f"---\ntype: {kind}\nupdated: {today}\ntags: [orion]{extra}\n---\n"

    def ensure(self) -> None:
        root = self.root
        (root / "People").mkdir(parents=True, exist_ok=True)
        (root / "Projects").mkdir(exist_ok=True)
        (root / "Short-term").mkdir(exist_ok=True)
        (root / "ORION").mkdir(exist_ok=True)
        about = root / ABOUT_NOTE
        if not about.exists():
            about.write_text(self._frontmatter("profile") + "# About me\n\n## Profile\n\n## Preferences\n", "utf-8")
        for kind, rel in FLAT_NOTES.items():
            if not (root / rel).exists():
                (root / rel).write_text(self._frontmatter(kind) + f"# {Path(rel).stem}\n\n", "utf-8")
        short = root / SHORT_NOTE
        if not short.exists():
            short.write_text(self._frontmatter("short-term", "\ntemporary: true") + "# This week\n\n"
                             "> Short-term memory. ORION reviews this every week: what matters moves into permanent "
                             "notes, the rest is cleared.\n\n", "utf-8")
        if not (root / HOME_NOTE).exists():
            self._write_home()

    def _write_home(self) -> None:
        root = self.root

        def links(folder: str) -> list[str]:
            d = root / folder
            return [f"- [[{folder}/{p.stem}|{p.stem}]]" for p in sorted(d.glob("*.md"))] if d.is_dir() else []

        people, projects = links("People"), links("Projects")
        text = ("# ORION Brain\n\nORION's long-term memory, kept on this PC as plain notes. Edit or delete anything; "
                "ORION re-reads changes. *This page is updated by ORION.*\n\n"
                "## You\n- [[About me]]\n- [[Plans & dates]]\n- [[Facts]]\n\n"
                f"## People\n{chr(10).join(people) or '- (none yet)'}\n\n"
                f"## Projects\n{chr(10).join(projects) or '- (none yet)'}\n\n"
                "## Short-term\n- [[Short-term/This week|This week]] (reviewed and cleared weekly)\n"
                "- [[ORION/Review log|Review log]]\n")
        (root / HOME_NOTE).write_text(text, "utf-8")

    # ── reading ────────────────────────────────────────────────────────────
    def _files(self) -> list[Path]:
        root = self.root
        if not root.is_dir():
            return []
        out = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "ORION"]
            for f in filenames:
                if f.endswith(".md"):
                    out.append(Path(dirpath) / f)
        return out

    def entries(self) -> list[dict]:
        root = self.root
        out: list[dict] = []
        for path in self._files():
            rel = path.relative_to(root).as_posix()
            if rel == HOME_NOTE:
                continue
            try:
                lines = path.read_text("utf-8", errors="replace").splitlines()
                mtime = path.stat().st_mtime
            except OSError:
                continue
            kind_fm = ""
            i = 0
            if lines and lines[0].strip() == "---":
                for j in range(1, min(len(lines), 40)):
                    if lines[j].strip() == "---":
                        for fm in lines[1:j]:
                            if fm.lower().startswith("type:"):
                                kind_fm = fm.split(":", 1)[1].strip().lower()
                        i = j + 1
                        break
            tier = "short" if rel == SHORT_NOTE or kind_fm == "short-term" else "permanent"
            folder = rel.split("/")[0] if "/" in rel else ""
            subject = path.stem if folder in ("People", "Projects") else ""
            base_kind = {"People": "person", "Projects": "project"}.get(folder) or \
                (kind_fm if kind_fm in KINDS else ("plan" if rel == FLAT_NOTES["plan"] else "fact"))
            section = ""
            in_code = False
            for line in lines[i:]:
                s = line.strip()
                if s.startswith("```"):
                    in_code = not in_code
                    continue
                if in_code or not s or s.startswith((">", "<!--", "|", "---")):
                    continue
                if s.startswith("#"):
                    section = s.lstrip("#").strip().lower()
                    continue
                if re.fullmatch(r"([-*+]\s+)?\[\[[^\]]+\]\]", s):
                    continue  # a bare link (index lists)
                text = _clean_line(s)
                created = mtime
                m = DATE_PREFIX.match(text)
                if m:
                    text = text[m.end():]
                    try:
                        created = dt.datetime.fromisoformat(m.group(1)).timestamp()
                    except ValueError:
                        pass
                if len(text) < 3:
                    continue
                kind = base_kind
                if rel == ABOUT_NOTE:
                    kind = "preference" if "prefer" in section else "profile"
                if subject and subject.split()[0].lower() not in text.lower():
                    text = f"{subject}: {text}"  # keep each memory self-contained
                elif not subject and tier == "permanent" and rel not in (ABOUT_NOTE, *FLAT_NOTES.values()) \
                        and path.stem.lower() not in text.lower():
                    text = f"{path.stem}: {text}"  # a note the user made themselves
                text = text[:MAX_ENTRY]
                out.append({"text": text, "kind": kind, "tier": tier, "note": rel, "created": created,
                            "key": _key(rel, text)})
                if len(out) >= 5000:
                    return out
        return out

    def _signature(self) -> tuple:
        sig = []
        for p in self._files():
            try:
                st = p.stat()
                sig.append((str(p), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        return tuple(sorted(sig))

    def sync(self, force: bool = False) -> bool:
        """Re-index the vault if any note changed (cheap mtime check, throttled to every few seconds)."""
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_check < 3:
                return False
            self._last_check = now
            sig = self._signature()
            if not force and sig == self._sig:
                return False
            self._sig = sig
            changed = self.e.store.sync_memories(self.entries())
        if changed:
            self.e.bus.emit("memory.changed")
        return changed

    # ── writing ────────────────────────────────────────────────────────────
    def _append(self, rel: str, line: str, section: str = "") -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        created = not path.exists()
        if created:
            kind = {"People": "person", "Projects": "project"}.get(rel.split("/")[0], "")
            kind = kind or ("plan" if rel == FLAT_NOTES["plan"] else "fact")
            path.write_text(self._frontmatter(kind) + f"# {Path(rel).stem}\n\n", "utf-8")
        lines = path.read_text("utf-8", errors="replace").splitlines()
        lines = [re.sub(r"^updated: .*$", f"updated: {dt.date.today().isoformat()}", ln) for ln in lines]
        if section:
            idx = next((i for i, ln in enumerate(lines) if ln.strip().lower() == f"## {section}".lower()), None)
            if idx is None:
                lines += ["", f"## {section}"]
                idx = len(lines) - 1
            end = next((j for j in range(idx + 1, len(lines)) if lines[j].startswith("#")), len(lines))
            while end > idx + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, line)
            if end == idx + 1:
                lines.insert(end, "")  # blank line after the heading
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(line)
        path.write_text("\n".join(lines) + "\n", "utf-8")
        if created:
            self._write_home()

    def add(self, text: str, kind: str = "fact", tier: str = "permanent", subject: str = "") -> dict:
        """Remember something. Returns the indexed memory (existing one if it's a duplicate)."""
        text = re.sub(r"\s+", " ", text).strip()[:MAX_ENTRY]
        if len(text) < 3:
            raise ValueError("Too short to remember.")
        kind = kind if kind in KINDS else "fact"
        tier = "short" if tier.startswith("short") or tier == "temporary" else "permanent"
        with self._lock:
            self.ensure()
            self.sync(force=True)
            current = self.e.store.list_memories()
            perm = [m for m in current if m["tier"] == "permanent"]
            if any(is_duplicate(text, [m["text"]]) for m in perm):
                return next(m for m in perm if is_duplicate(text, [m["text"]]))
            if tier == "short":
                dup = next((m for m in current if m["tier"] == "short" and is_duplicate(text, [m["text"]])), None)
                if dup:
                    return dup
                self._append(SHORT_NOTE, f"- [{dt.date.today().isoformat()}] {text}")
            else:
                rel = self._note_for(kind, subject)
                section = "Preferences" if kind == "preference" else ("Profile" if kind == "profile" else "")
                self._append(rel, f"- {text}", section)
                for m in current:  # promoted from short-term: drop the temporary copy
                    if m["tier"] == "short" and is_duplicate(text, [m["text"]]):
                        self._remove_line(m)
            self.sync(force=True)
            return next((m for m in self.e.store.list_memories() if text in m["text"]), {"text": text})

    def _remove_line(self, mem: dict) -> bool:
        path = self.root / (mem.get("note") or "")
        if not mem.get("note") or not path.is_file():
            return False
        lines = path.read_text("utf-8", errors="replace").splitlines()
        subject = path.stem
        for i, ln in enumerate(lines):
            t = _clean_line(ln)
            t = DATE_PREFIX.sub("", t)
            if t and (t == mem["text"] or f"{subject}: {t}" == mem["text"]):
                del lines[i]
                path.write_text("\n".join(lines) + "\n", "utf-8")
                return True
        return False

    def remove(self, memory_id: int) -> bool:
        with self._lock:
            mem = self.e.store.get_memory(memory_id)
            if not mem:
                return False
            ok = self._remove_line(mem)
            if not ok:
                self.e.store.forget(memory_id)  # index-only leftover
            self.sync(force=True)
            return True

    # ── migration from the pre-vault SQLite memory ──────────────────────────
    def migrate(self) -> int:
        if self.e.store.kv_get("vault_migrated"):
            return 0
        legacy = [m for m in self.e.store.list_memories() if not m.get("key")]
        self.ensure()
        for m in reversed(legacy):
            kind = m["kind"] if m["kind"] in ("profile", "preference", "plan") else "fact"
            rel = self._note_for(kind)
            section = "Preferences" if kind == "preference" else ("Profile" if kind == "profile" else "")
            self._append(rel, f"- {m['text']}", section)
            self.e.store.forget(m["id"])  # now lives in the vault; the index entry is recreated by sync
        self.e.store.kv_set("vault_migrated", True)
        self.sync(force=True)
        if legacy:
            log.info("Moved %d memories into the vault at %s", len(legacy), self.root)
        return len(legacy)

    # ── weekly review ──────────────────────────────────────────────────────
    def last_slot(self, now: dt.datetime | None = None) -> dt.datetime:
        s = self.e.config.settings
        now = now or dt.datetime.now()
        slot = now.replace(hour=s.memory_review_hour, minute=0, second=0, microsecond=0)
        slot -= dt.timedelta(days=(now.weekday() - s.memory_review_day) % 7)
        if slot > now:
            slot -= dt.timedelta(days=7)
        return slot

    def next_review(self) -> dt.datetime:
        return self.last_slot() + dt.timedelta(days=7)

    def review_due(self, now: dt.datetime | None = None) -> bool:
        last = self.e.store.kv_get("vault_last_review")
        if last is None:
            self.e.store.kv_set("vault_last_review", time.time())  # first run: start counting from now
            return False
        retry = self.e.store.kv_get("vault_review_retry", 0)
        return last < self.last_slot(now).timestamp() and time.time() >= retry

    def _short_items(self) -> list[tuple[str, str]]:
        path = self.root / SHORT_NOTE
        if not path.is_file():
            return []
        items = []
        for ln in path.read_text("utf-8", errors="replace").splitlines():
            if not re.match(r"^\s*[-*+]\s+", ln):
                continue
            t = _clean_line(ln)
            m = DATE_PREFIX.match(t)
            items.append((m.group(1) if m else "", t[m.end():] if m else t))
        return items

    async def review(self) -> dict:
        """Weekly clean-up of short-term memory, judged by Gemini. Nothing is deleted unless it was reviewed."""
        items = self._short_items()
        if not items:
            self.e.store.kv_set("vault_last_review", time.time())
            return {"kept": [], "carried": [], "discarded": [], "note": "Short-term memory was already empty."}
        perm = [m["text"] for m in self.e.store.list_memories() if m["tier"] == "permanent"][:80]
        listing = "\n".join(f"{n}. [{d or 'undated'}] {t}" for n, (d, t) in enumerate(items, 1))
        try:
            raw = await self.e.brain.generate_text(
                REVIEW_PROMPT.format(today=dt.date.today().isoformat(), items=listing,
                                     known="\n".join(f"- {k}" for k in perm) or "(nothing yet)"),
                tier="fast", json_mode=True, patient=True)
            data = json.loads(raw)
            rows = data if isinstance(data, list) else data.get("decisions", [])  # models sometimes drop the wrapper
            decisions = {int(d["n"]): d for d in rows if isinstance(d, dict) and str(d.get("n", "")).isdigit()}
        except Exception as exc:
            self.e.store.kv_set("vault_review_retry", time.time() + 3600)
            raise RuntimeError(f"Memory review couldn't run ({exc}); nothing was deleted, will retry.") from exc
        kept, carried, discarded, remaining = [], [], [], []
        for n, (day, text) in enumerate(items, 1):
            d = decisions.get(n)
            action = (d or {}).get("action", "")
            if action == "keep":
                statement = str(d.get("text") or text).strip()
                self.add(statement, str(d.get("kind") or "fact"), "permanent", str(d.get("subject") or ""))
                kept.append(statement)
            elif action == "discard":
                discarded.append(text)
            else:  # carry, or not judged: stays for next week
                remaining.append(f"- [{day or dt.date.today().isoformat()}] {text}")
                if action == "carry":
                    carried.append(text)
        with self._lock:
            path = self.root / SHORT_NOTE
            lines = path.read_text("utf-8", errors="replace").splitlines()
            head = [ln for ln in lines if not re.match(r"^\s*[-*+]\s+", ln)]
            while head and not head[-1].strip():
                head.pop()
            path.write_text("\n".join(head + [""] + remaining) + "\n", "utf-8")
            log_lines = [f"## {dt.date.today().isoformat()}"]
            log_lines += [f"- Kept: {t}" for t in kept] + [f"- Carried over: {t}" for t in carried]
            log_lines += [f"- Deleted: {t}" for t in discarded]
            self._append(LOG_NOTE, "\n".join(log_lines) + "\n")
        self.e.store.kv_set("vault_last_review", time.time())
        self.sync(force=True)
        result = {"kept": kept, "carried": carried, "discarded": discarded}
        log.info("Memory review: kept %d, carried %d, deleted %d", len(kept), len(carried), len(discarded))
        return result

    def status(self) -> dict:
        mems = self.e.store.list_memories()
        return {"path": str(self.root), "permanent": sum(m["tier"] == "permanent" for m in mems),
                "short": sum(m["tier"] == "short" for m in mems),
                "last_review": self.e.store.kv_get("vault_last_review"),
                "next_review": self.next_review().timestamp(),
                "obsidian": _obsidian_installed()}


def register_with_obsidian(root: Path) -> bool:
    """Add the vault to Obsidian's vault list (like "Open folder as vault") so obsidian:// links can open it."""
    cfg = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    if not cfg.parent.is_dir():
        return False
    try:
        data = json.loads(cfg.read_text("utf-8")) if cfg.exists() else {}
    except (OSError, ValueError):
        return False
    vaults = data.setdefault("vaults", {})
    target = str(root.resolve()).lower()
    if any(target == str(Path(v.get("path", ""))).lower() or target.startswith(str(Path(v.get("path", ""))).lower() + os.sep)
           for v in vaults.values() if v.get("path")):
        return True
    vaults[hashlib.sha1(target.encode()).hexdigest()[:16]] = {"path": str(root.resolve()), "ts": int(time.time() * 1000)}
    cfg.write_text(json.dumps(data), "utf-8")
    return True


def _obsidian_installed() -> bool:
    if os.name != "nt":
        return False
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Obsidian" / "Obsidian.exe"
    return local.exists() or (Path(os.environ.get("APPDATA", "")) / "obsidian").is_dir()
