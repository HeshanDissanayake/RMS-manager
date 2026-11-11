"""Processes: is a Claude Code session still running? (Linux /proc)."""

from __future__ import annotations

import os


def _stat(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            text = f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # The command name (field 2) may contain spaces; fields after the last ')'.
    return text[text.rindex(")") + 2:].split()


def start_time(pid: int | None) -> int | None:
    """Kernel start time of the process (clock ticks since boot), or None."""
    if not pid:
        return None
    fields = _stat(pid)
    if fields is None:
        return None
    return int(fields[19])  # field 22 overall


def alive(pid: int | None, started: int | None = None) -> bool:
    """True if the process runs and (when given) is the same one as before.

    Comparing start times guards against a reused PID.
    """
    if not pid:
        return False
    fields = _stat(pid)
    if fields is None or fields[0] in ("Z", "X"):
        return False
    return started is None or int(fields[19]) == started


def _ppid(pid: int) -> int | None:
    fields = _stat(pid)
    return int(fields[1]) if fields else None


def _comm(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def claude_pid() -> int | None:
    """PID of the Claude Code process this command runs under.

    Claude Code sets $CLAUDE_PID for the commands it runs; otherwise walk up
    the parent processes to one named ``claude``.
    """
    env = os.environ.get("CLAUDE_PID", "")
    if env.isdigit() and alive(int(env)):
        return int(env)
    pid = os.getppid()
    for _ in range(32):
        if not pid or pid <= 1:
            return None
        if _comm(pid) == "claude":
            return pid
        pid = _ppid(pid)
    return None
