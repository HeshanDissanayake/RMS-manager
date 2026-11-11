"""The project: found by its ``rms.json`` (the project described as config)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "rms.json"

DEFAULTS: dict = {
    "name": None,
    # Folder holding one folder per thread, named T<NN>-<name>.
    "threads_dir": "threads",
    # Markdown file with the thread tables (ID | Thread | Status | Since ...).
    "index_file": "threads/INDEX.md",
    # Folder holding side sessions: side/<name>/ ; a session started there
    # may write only inside its own side folder and needs no thread claim.
    "side_dir": "side",
    # Where RMS keeps its own state (sessions, claims, events). Not in git.
    "state_dir": "store/rms",
    # Paths (globs, relative to the project root) any thread session may write.
    "shared_writable": [],
    # Paths outside the project any session may write (globs, ~ expanded).
    "extra_writable": ["/tmp/claude-*"],
}


@dataclass
class Project:
    root: Path
    cfg: dict = field(default_factory=dict)

    def _get(self, key: str):
        return self.cfg.get(key, DEFAULTS[key])

    @property
    def name(self) -> str:
        return self._get("name") or self.root.name

    @property
    def threads_dir(self) -> Path:
        return self.root / self._get("threads_dir")

    @property
    def index_file(self) -> Path:
        return self.root / self._get("index_file")

    @property
    def side_dir(self) -> Path:
        return self.root / self._get("side_dir")

    @property
    def state_dir(self) -> Path:
        return self.root / self._get("state_dir")

    @property
    def sessions_dir(self) -> Path:
        return self.state_dir / "sessions"

    @property
    def claims_dir(self) -> Path:
        return self.state_dir / "claims"

    @property
    def shared_writable(self) -> list[str]:
        return list(self._get("shared_writable"))

    @property
    def extra_writable(self) -> list[str]:
        return [os.path.expanduser(g) for g in self._get("extra_writable")]

    def rel(self, path: Path | str) -> str:
        """Path relative to the project root when inside it, else absolute."""
        p = Path(path)
        try:
            return str(p.resolve().relative_to(self.root))
        except ValueError:
            return str(p)


def load(root: Path) -> Project:
    root = Path(root).resolve()
    with open(root / CONFIG_NAME, encoding="utf-8") as f:
        cfg = json.load(f)
    unknown = set(cfg) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"{root / CONFIG_NAME}: unknown keys {sorted(unknown)}")
    return Project(root=root, cfg=cfg)


def find_project(start: Path | str | None = None) -> Project | None:
    """The project containing ``start`` (default: cwd), or $RMS_PROJECT."""
    env = os.environ.get("RMS_PROJECT")
    if env:
        return load(Path(env))
    p = Path(start or os.getcwd()).resolve()
    for d in (p, *p.parents):
        if (d / CONFIG_NAME).is_file():
            return load(d)
    return None


def init_project(root: Path, name: str | None = None) -> Path:
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    cfg = dict(DEFAULTS)
    cfg["name"] = name or root.name
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path
