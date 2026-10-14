"""Skills: packaged know-how ORION loads on demand.

A skill is a folder containing ``SKILL.md``::

    ---
    name: morning-briefing
    description: Weather, calendar-free day plan, news headlines and PC status in one go.
    tier: smart            # optional: fast | smart
    icon: sunrise          # optional: UI icon name
    ---
    Step-by-step instructions for ORION…

Only the one-line descriptions sit in the system prompt (cheap); the full
instructions are loaded when ORION calls ``use_skill`` or the user types
``/skill-name``. Built-in skills ship with the app; user skills live in the
data folder's ``skills`` directory and override built-ins with the same name.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("orion.skills")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    builtin: bool
    tier: str = "fast"
    icon: str = "sparkles"
    enabled: bool = True

    def to_dict(self, include_body: bool = False) -> dict:
        d = {"name": self.name, "description": self.description, "builtin": self.builtin, "tier": self.tier,
             "icon": self.icon, "enabled": self.enabled, "path": str(self.path)}
        if include_body:
            d["body"] = self.body
        return d


def parse_skill(text: str, path: Path, builtin: bool) -> Skill:
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.split("#", 1)[0].strip().strip('"\'')
        body = m.group(2)
    name = meta.get("name") or path.parent.name
    if not NAME_RE.match(name):
        raise ValueError(f"Invalid skill name {name!r} (use lowercase letters, digits, dashes)")
    return Skill(
        name=name, description=meta.get("description", "").strip() or "(no description)", body=body.strip(),
        path=path, builtin=builtin, tier=meta.get("tier", "fast"), icon=meta.get("icon", "sparkles"),
    )


class SkillLibrary:
    def __init__(self, builtin_dir: Path, user_dir: Path) -> None:
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.skills: dict[str, Skill] = {}
        self.disabled: set[str] = set()

    def load(self) -> None:
        found: dict[str, Skill] = {}
        for root, builtin in ((self.builtin_dir, True), (self.user_dir, False)):
            if not root.exists():
                continue
            for md in sorted(root.glob("*/SKILL.md")):
                try:
                    s = parse_skill(md.read_text("utf-8"), md, builtin)
                    s.enabled = s.name not in self.disabled
                    found[s.name] = s
                except Exception as exc:
                    log.warning("Skipping skill %s: %s", md, exc)
        self.skills = found

    def get(self, name: str) -> Skill | None:
        s = self.skills.get(name.strip().lstrip("/").lower())
        return s if s and s.enabled else None

    def enabled(self) -> list[Skill]:
        return [s for s in self.skills.values() if s.enabled]

    def index_prompt(self) -> str:
        items = self.enabled()
        if not items:
            return ""
        lines = "\n".join(f"- {s.name}: {s.description}" for s in items)
        return ("SKILLS — packaged playbooks. When a request matches one, call use_skill(name) FIRST and follow "
                "its instructions:\n" + lines)

    def save_user_skill(self, name: str, description: str, body: str, tier: str = "fast", icon: str = "sparkles") -> Skill:
        if not NAME_RE.match(name):
            raise ValueError("Skill names use lowercase letters, digits and dashes (e.g. 'weekly-report').")
        folder = self.user_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        desc = description.replace("\n", " ").strip()
        text = f"---\nname: {name}\ndescription: {desc}\ntier: {tier}\nicon: {icon}\n---\n\n{body.strip()}\n"
        (folder / "SKILL.md").write_text(text, "utf-8")
        self.load()
        return self.skills[name]

    def delete_user_skill(self, name: str) -> bool:
        s = self.skills.get(name)
        if not s or s.builtin:
            return False
        import shutil

        shutil.rmtree(s.path.parent, ignore_errors=True)
        self.load()
        return True

    def set_enabled(self, name: str, enabled: bool) -> None:
        (self.disabled.discard if enabled else self.disabled.add)(name)
        if name in self.skills:
            self.skills[name].enabled = enabled
