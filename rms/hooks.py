"""Claude Code hooks: how RMS learns about sessions and guards their writes.

Claude Code runs ``rms hook <event>`` with a JSON object on stdin
(session_id, cwd, tool_name, tool_input, ...). Configure them in the
project's ``.claude/settings.json`` (``rms install-hooks``).

- session-start: register the session; tell Claude (added to its context)
  who it is, which threads are free, and how to claim one.
- pre-tool-use: refresh "last seen"; deny writes the session may not make.
- session-end: mark the session ended and release its claims.

A hook never breaks Claude Code: on an internal error it logs and lets the
call through (exit 0, no decision).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from rms import claims, procs, sessions, store, threads
from rms.config import Project, find_project

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Commands a session without a thread may still run (they only read).
READ_ONLY = {"ls", "cat", "head", "tail", "grep", "rg", "wc", "pwd", "echo",
             "tree", "stat", "file", "du", "df", "which", "date", "cd", "less",
             "find", "git", "realpath", "basename", "dirname", "sort", "uniq"}
GIT_READ_ONLY = {"status", "log", "diff", "show", "branch", "rev-parse",
                 "ls-files", "blame", "remote", "describe", "shortlog"}
SEGMENT_SPLIT = re.compile(r"\|\||&&|\|")
UNSAFE = re.compile(r"[;<>`]|\$\(|(?<![&|])&(?![&|])")


def rms_command() -> str:
    """How a session should call RMS (the running executable)."""
    exe = Path(sys.argv[0])
    if exe.name == "rms":
        return str(exe.resolve())
    return f"{sys.executable} -m rms"


# --- context for Claude -----------------------------------------------------

def context_text(project: Project, rec: dict, on_path: bool = True) -> str:
    rms = "rms" if on_path else rms_command()
    lines = [
        f"RMS (Research Manager) manages this project: \"{project.name}\" at {project.root}.",
        f"This Claude Code session is {rec['short']} (session id {rec['id']}).",
        f"Call RMS as `{rms} <command>`, one command per Bash call, nothing around it.",
        "",
    ]
    if rec.get("side"):
        lines.append(
            f"This session started in the side folder {project.rel(project.side_dir / rec['side'])}/: "
            "it may write only inside that folder. It needs no thread claim.")
    if rec.get("thread"):
        t = threads.discover(project).get(rec["thread"])
        name = t.name if t else ""
        lines.append(
            f"This session holds thread {rec['thread']} ({name}). It may write only in its "
            f"thread folder{' and side folder' if rec.get('side') else ''}. "
            f"`{rms} status` shows the thread's State and How to resume.")
    elif not rec.get("side"):
        lines += [
            "This session holds NO thread yet, so RMS blocks file edits and commands that "
            "change anything. Before working:",
            "1. Ask the owner which thread to work on (AskUserQuestion; options = the threads "
            "below that are not done, free ones first).",
            f"2. Run `{rms} claim <ID>`. RMS refuses a thread another live session holds; "
            "tell the owner who holds it. A stale claim may be taken over only after the "
            f"owner says yes: `{rms} claim <ID> --take-over`.",
            f"3. Read what `{rms} claim` prints (the thread's State and How to resume).",
        ]
    lines += ["", "Threads:"]
    lines += thread_lines(project)
    lines += ["", f"Other commands: `{rms} whoami`, `{rms} threads`, `{rms} status`, "
              f"`{rms} release` (frees this session's thread)."]
    return "\n".join(lines)


def thread_lines(project: Project) -> list[str]:
    cl = claims.all_claims(project)
    out = []
    for t in threads.discover(project).values():
        out.append(f"  {t.id}  {t.name}  [{t.status}]  {claims.describe(project, cl.get(t.id))}")
    return out or ["  (no threads found)"]


# --- command classification -------------------------------------------------

def _first_words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return []


HARMLESS_REDIRECTS = re.compile(r"\s(?:2>&1|[12]?>\s*/dev/null)(?=\s|$|\))")


def _segments(cmd: str) -> list[str] | None:
    """Simple commands joined by |, || or &&; None if anything else is in it."""
    cmd = HARMLESS_REDIRECTS.sub(" ", " " + cmd).strip()
    while cmd.startswith("(") and cmd.endswith(")"):
        cmd = cmd[1:-1].strip()
    if UNSAFE.search(cmd) or "(" in cmd or ")" in cmd:
        return None
    segs = [s.strip() for s in SEGMENT_SPLIT.split(cmd)]
    return segs if all(segs) else None


def _is_rms(words: list[str]) -> bool:
    if not words:
        return False
    if Path(words[0]).name == "rms":
        return True
    return (len(words) >= 3 and Path(words[0]).name.startswith("python")
            and words[1:3] == ["-m", "rms"])


def _is_read_only(words: list[str]) -> bool:
    if not words:
        return False
    name = Path(words[0]).name
    if name not in READ_ONLY:
        return False
    if name == "find" and any(w in ("-delete", "-exec", "-execdir", "-fprint", "-fls") for w in words):
        return False
    if name == "git":
        sub = next((w for w in words[1:] if not w.startswith("-")), "")
        if sub not in GIT_READ_ONLY:
            return False
        if sub == "branch" and any(w in ("-d", "-D", "-m", "-M", "--delete", "--move") for w in words):
            return False
        if sub == "remote" and len(words) > 2 and words[2] not in ("-v", "show", "get-url"):
            return False
    return True


def allowed_without_thread(cmd: str) -> bool:
    """True if every part of the command is an `rms` call or only reads."""
    segs = _segments(cmd)
    if segs is None:
        return False
    return all(_is_rms(w) or _is_read_only(w) for w in map(_first_words, segs))


# --- hook handlers ----------------------------------------------------------

def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def session_start(project: Project, data: dict) -> dict:
    sid = data["session_id"]
    rec = sessions.register(project, sid, data.get("cwd") or os.getcwd(),
                            procs.claude_pid(), source=data.get("source", "startup"),
                            transcript=data.get("transcript_path"))
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    on_path = bool(env_file)
    if env_file:
        bin_dir = Path(rms_command().split()[0]).parent
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f'export PATH="{bin_dir}:$PATH"\n')
            f.write(f'export RMS_PROJECT="{project.root}"\n')
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": context_text(project, rec, on_path)}}


def pre_tool_use(project: Project, data: dict) -> dict | None:
    sid = data["session_id"]
    cwd = data.get("cwd") or os.getcwd()
    rec = sessions.ensure(project, sid, cwd)
    sessions.touch(project, rec)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    areas = claims.writable_areas(project, rec)
    no_thread = (f"RMS: session {rec['short']} holds no thread, so it may not change files. "
                 "Ask the owner which thread to work on, then run `rms claim <ID>` "
                 "(`rms threads` lists them).")

    if tool in WRITE_TOOLS:
        target = ti.get("file_path") or ti.get("notebook_path")
        if not target:
            return None
        path = Path(target) if os.path.isabs(target) else Path(cwd) / target
        if claims.may_write(project, rec, path):
            return None
        if areas:
            where = ", ".join(project.rel(a) + "/" for a in areas)
            reason = (f"RMS: {project.rel(path)} is outside session {rec['short']}'s area "
                      f"({where}).")
        else:
            reason = no_thread
        store.append_event(project.state_dir, "denied", session=rec["short"],
                           tool=tool, path=project.rel(path))
        return _deny(reason)

    if tool == "Bash" and not areas:
        cmd = ti.get("command", "")
        if allowed_without_thread(cmd):
            return None
        store.append_event(project.state_dir, "denied", session=rec["short"],
                           tool=tool, command=cmd[:200])
        return _deny(no_thread + " Read-only commands (ls, cat, grep, git log, ...) "
                     "and `rms` commands still run.")
    return None


def session_end(project: Project, data: dict) -> None:
    sid = data["session_id"]
    reason = data.get("reason", "")
    if sessions.get(project, sid) is None:
        return None
    claims.release_all(project, sid, by="session-end", reason=reason or None)
    sessions.end(project, sid, reason)
    return None


HANDLERS = {
    "session-start": session_start,
    "pre-tool-use": pre_tool_use,
    "session-end": session_end,
}


def run(event: str, stdin=None) -> int:
    stdin = stdin or sys.stdin
    try:
        raw = stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    handler = HANDLERS.get(event)
    if handler is None:
        print(f"rms: unknown hook event {event!r}", file=sys.stderr)
        return 0
    project = find_project(data.get("cwd"))
    if project is None or "session_id" not in data:
        return 0
    try:
        out = handler(project, data)
    except Exception as e:  # never break Claude Code
        try:
            store.append_event(project.state_dir, "hook-error", hook=event, error=repr(e))
        except OSError:
            pass
        print(f"rms hook {event}: {e!r}", file=sys.stderr)
        return 0
    if out:
        print(json.dumps(out))
    return 0


def settings_for(rms_cmd: str) -> dict:
    """The hooks block for a project's .claude/settings.json."""
    def h(event: str) -> list[dict]:
        return [{"type": "command", "command": f"{rms_cmd} hook {event}"}]
    return {"hooks": {
        "SessionStart": [{"hooks": h("session-start")}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                        "hooks": h("pre-tool-use")}],
        "SessionEnd": [{"hooks": h("session-end")}],
    }}
