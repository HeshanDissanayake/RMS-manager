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
    # --- thread lifecycle ---
    # The root repo's main branch (T00 and the INDEX live there).
    "main_branch": "main",
    # Each thread's branch, in the root repo and in every source repo it uses.
    "thread_branch": "thread/{id}",
    # One checkout per thread: <worktrees_dir>/T<NN>/ (sources inside it too).
    "worktrees_dir": "store/worktrees",
    # Milestones table (M<N> entries), on main.
    "milestones_file": "threads/T00-main/milestones.md",
    # A thread's large data: <data_dir>/T<NN>/ (archived when done).
    "data_dir": "store/data",
    # Cold storage for archived thread data (.tar.gz + catalog.csv with sha256).
    "archive_dir": "store/archive",
    # Builds: <builds_dir>/<tool>/<name>/ (a thread's own: <tool>/T<NN>-<name>).
    "builds_dir": "store/builds",
    # Clean-up: builds/data changed within this many weeks are kept.
    "gc_weeks": 4,
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
    def main_branch(self) -> str:
        return self._get("main_branch")

    def thread_branch(self, thread_id: str) -> str:
        return self._get("thread_branch").format(id=thread_id)

    @property
    def worktrees_dir(self) -> Path:
        return self.root / self._get("worktrees_dir")

    @property
    def milestones_file(self) -> Path:
        return self.root / self._get("milestones_file")

    @property
    def data_dir(self) -> Path:
        return self.root / self._get("data_dir")

    @property
    def archive_dir(self) -> Path:
        return Path(os.path.expanduser(self._get("archive_dir"))) if self._get(
            "archive_dir").startswith(("/", "~")) else self.root / self._get("archive_dir")

    @property
    def builds_dir(self) -> Path:
        return self.root / self._get("builds_dir")

    @property
    def gc_weeks(self) -> int:
        return int(self._get("gc_weeks"))

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
            return load(_main_checkout(d))
    return None


def _main_checkout(d: Path) -> Path:
    """A thread's checkout (a git worktree) belongs to the root it came from.

    A linked worktree's .git is a file "gitdir: <root>/.git/worktrees/<name>";
    the project is <root>, not the checkout (which has its own copy of rms.json).
    """
    git_file = d / ".git"
    if not git_file.is_file():
        return d
    text = git_file.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return d
    gitdir = Path(text.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (d / gitdir).resolve()
    if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
        root = gitdir.parent.parent.parent
        if (root / CONFIG_NAME).is_file():
            return root
    return d


def init_project(root: Path, name: str | None = None) -> Path:
    root = Path(root).resolve()
    path = root / CONFIG_NAME
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    cfg = dict(DEFAULTS)
    cfg["name"] = name or root.name
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return path
