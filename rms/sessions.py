"""Claude Code sessions known to RMS: one JSON file per session.

A session is registered by the SessionStart hook (or lazily, the first time
it calls RMS). It is identified by Claude Code's session id; it is alive
while its Claude Code process runs.
"""

from __future__ import annotations

import os
from pathlib import Path

from rms import procs, store
from rms.config import Project


def short_id(session_id: str) -> str:
    return "S-" + session_id.replace("-", "")[:4].upper()


def _path(project: Project, session_id: str) -> Path:
    return project.sessions_dir / f"{session_id}.json"


def get(project: Project, session_id: str) -> dict | None:
    return store.read_json(_path(project, session_id))


def save(project: Project, rec: dict) -> None:
    store.write_json(_path(project, rec["id"]), rec)


def all_sessions(project: Project) -> list[dict]:
    if not project.sessions_dir.is_dir():
        return []
    out = [store.read_json(p) for p in project.sessions_dir.glob("*.json")]
    return sorted((r for r in out if r), key=lambda r: r.get("started", ""))


def side_of(project: Project, cwd: str | Path) -> str | None:
    """Name of the side folder ``cwd`` is in (side/<name>/...), or None."""
    try:
        rel = Path(cwd).resolve().relative_to(project.side_dir.resolve())
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def is_alive(rec: dict | None) -> bool:
    return bool(rec) and rec.get("state") != "ended" and procs.alive(
        rec.get("pid"), rec.get("pid_start"))


def status(rec: dict) -> str:
    """'live', 'gone' (process vanished without ending) or 'ended'."""
    if rec.get("state") == "ended":
        return "ended"
    return "live" if is_alive(rec) else "gone"


def register(project: Project, session_id: str, cwd: str, pid: int | None,
             source: str = "startup", transcript: str | None = None) -> dict:
    with store.locked(project.state_dir):
        rec = get(project, session_id)
        new = rec is None
        if new:
            rec = {"id": session_id, "short": short_id(session_id),
                   "started": store.now(), "thread": None}
        rec.update({
            "cwd": str(Path(cwd).resolve()),
            "side": side_of(project, cwd),
            "pid": pid,
            "pid_start": procs.start_time(pid),
            "source": source,
            "state": "active",
            "last_seen": store.now(),
        })
        if transcript:
            rec["transcript"] = transcript
        rec.pop("ended", None)
        save(project, rec)
        store.append_event(project.state_dir,
                           "session-start" if new else f"session-{source}",
                           session=rec["short"], cwd=project.rel(rec["cwd"]),
                           side=rec["side"], thread=rec.get("thread"))
    return rec


def current_id() -> str | None:
    """Session id of the Claude Code session running this command."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def ensure(project: Project, session_id: str, cwd: str | None = None) -> dict:
    """The session's record, registering it if the start hook missed it."""
    rec = get(project, session_id)
    if rec is None or rec.get("state") == "ended":
        rec = register(project, session_id, cwd or os.getcwd(),
                       procs.claude_pid(), source="lazy" if rec is None else "resume")
    return rec


def touch(project: Project, rec: dict) -> None:
    with store.locked(project.state_dir):
        cur = get(project, rec["id"]) or rec
        cur["last_seen"] = store.now()
        save(project, cur)
        rec.update(cur)


def end(project: Project, session_id: str, reason: str = "") -> dict | None:
    with store.locked(project.state_dir):
        rec = get(project, session_id)
        if rec is None:
            return None
        rec["state"] = "ended"
        rec["ended"] = store.now()
        save(project, rec)
        store.append_event(project.state_dir, "session-end",
                           session=rec["short"], reason=reason or None)
    return rec
