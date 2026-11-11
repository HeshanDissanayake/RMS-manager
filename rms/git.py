"""Small git helpers (subprocess; no library)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def git(cwd: Path | str, *args: str, check: bool = True, strip: bool = True) -> str:
    r = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)} (in {cwd}): {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")


def ok(cwd: Path | str, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def has_branch(repo: Path, branch: str) -> bool:
    return ok(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def head(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", rev)


def submodule_paths(repo: Path) -> list[str]:
    """Paths of the submodules declared in repo/.gitmodules."""
    if not (repo / ".gitmodules").is_file():
        return []
    out = git(repo, "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$",
              check=False)
    return [line.split(None, 1)[1] for line in out.splitlines() if " " in line]


def pointer(repo: Path, rev: str, path: str) -> str | None:
    """The commit a submodule path points to in ``rev``."""
    out = git(repo, "ls-tree", rev, "--", path, check=False)
    parts = out.split()
    return parts[2] if len(parts) >= 3 and parts[1] == "commit" else None


def dirty(repo: Path, paths: list[str] | None = None) -> list[str]:
    """Changed or untracked files (porcelain lines), optionally limited to paths."""
    args = ["status", "--porcelain", "--untracked-files=all"]
    if paths:
        args += ["--", *paths]
    out = git(repo, *args, strip=False)
    return [line for line in out.splitlines() if line.strip()]


def message(subject: str, body: str = "", trailers: dict[str, str] | None = None) -> str:
    parts = [subject.strip()]
    if body.strip():
        parts.append(body.strip())
    if trailers:
        parts.append("\n".join(f"{k}: {v}" for k, v in trailers.items() if v))
    return "\n\n".join(parts) + "\n"


def commit(repo: Path, msg: str, allow_empty: bool = False) -> str | None:
    """Commit what is staged; None if nothing was staged."""
    if not allow_empty and ok(repo, "diff", "--cached", "--quiet"):
        return None
    _commit_stdin(repo, msg, allow_empty)
    return head(repo)


def _commit_stdin(repo: Path, msg: str, allow_empty: bool) -> None:
    args = ["git", "commit", "-q", "-F", "-"] + (["--allow-empty"] if allow_empty else [])
    r = subprocess.run(args, cwd=repo, input=msg, capture_output=True, text=True)
    if r.returncode != 0:
        raise GitError(f"git commit (in {repo}): {(r.stderr or r.stdout).strip()}")


def subjects(repo: Path, rev: str) -> list[str]:
    out = git(repo, "log", "--format=%s", rev, check=False)
    return out.splitlines()
