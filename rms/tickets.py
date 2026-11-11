"""Tickets for RMS itself: notes, bugs and ideas sent from any project to the
RMS-manager repo, to be picked up later in a session there.

One RMS install serves every project on a machine, and the install knows its own
repo (this package's parent folder). So `rms ticket "…"` from LLVM-regsw, the
sandbox or anywhere else writes ``<RMS-manager>/tickets/RMS-<NNN>.md``, with the
context attached: project, thread, session, folder, RMS commit, recent events.
Plain files; a session in RMS-manager commits them with its work. Owner 2026-11-11.

File layout (Markdown, a field block on top so it reads well and parses simply):

    # RMS-007: band hides on narrow terminal
    - status: open
    - kind: bug
    - filed: 2026-11-11T20:01:00+11:00
    - by: owner | Claude session S-7D01
    - project: LLVM-regsw (/home/…/LLVM-regsw)
    - thread: T03
    ...
    ## Text
    ## Context
    ## Closed      (when closed: date, note)
"""

from __future__ import annotations

import fcntl
import os
import re
import subprocess
from pathlib import Path

from rms import store

KINDS = ("bug", "idea", "question", "note")
FIELD = re.compile(r"^- (\w[\w ]*):\s*(.*)$", re.M)
HEAD = re.compile(r"^# (RMS-\d+):\s*(.*)$", re.M)


class TicketError(Exception):
    pass


def home() -> Path:
    """The RMS-manager repo this rms runs from ($RMS_HOME overrides)."""
    if os.environ.get("RMS_HOME"):
        return Path(os.environ["RMS_HOME"])
    return Path(__file__).resolve().parent.parent


def tickets_dir() -> Path:
    return home() / "tickets"


def _rms_commit() -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(home()), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _next_id(d: Path) -> str:
    nums = [int(m.group(1)) for p in d.glob("RMS-*.md") if (m := re.match(r"RMS-(\d+)", p.name))]
    return f"RMS-{max(nums, default=0) + 1:03d}"


def file(text: str, body: str = "", kind: str = "note", by: str = "owner",
         context: dict | None = None, events: list[str] | None = None) -> Path:
    """Write a new ticket; returns its path."""
    text = " ".join(text.split())
    if not text:
        raise TicketError("say what the ticket is about: rms ticket \"…\"")
    if kind not in KINDS:
        raise TicketError(f"kind is one of {', '.join(KINDS)}")
    d = tickets_dir()
    d.mkdir(parents=True, exist_ok=True)
    title = text if len(text) <= 80 else text[:77] + "…"
    with open(d / ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tid = _next_id(d)
        fields = {"status": "open", "kind": kind, "filed": store.now(), "by": by,
                  **{k: v for k, v in (context or {}).items() if v}, "rms commit": _rms_commit()}
        lines = [f"# {tid}: {title}", ""]
        lines += [f"- {k}: {v}" for k, v in fields.items() if v]
        lines += ["", "## Text", "", text + ("\n\n" + body.strip() if body.strip() else ""), ""]
        if events:
            lines += ["## Context (recent RMS events)", "", "```", *events, "```", ""]
        path = d / f"{tid}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="ignore")
    h = HEAD.search(text)
    top = text.split("\n## ", 1)[0]
    rec = {k: v.strip() for k, v in FIELD.findall(top)}
    rec.update(id=h.group(1) if h else path.stem, title=h.group(2) if h else path.stem,
               path=str(path), text=text)
    return rec


def all_tickets(include_closed: bool = False) -> list[dict]:
    d = tickets_dir()
    if not d.is_dir():
        return []
    out = [parse(p) for p in sorted(d.glob("RMS-*.md"))]
    return [t for t in out if include_closed or t.get("status") != "closed"]


def get(tid: str) -> dict:
    tid = tid.upper()
    if re.fullmatch(r"\d+", tid):
        tid = f"RMS-{int(tid):03d}"
    p = tickets_dir() / f"{tid}.md"
    if not p.is_file():
        raise TicketError(f"no ticket {tid}")
    return parse(p)


def close(tid: str, note: str, by: str = "owner") -> dict:
    t = get(tid)
    if t.get("status") == "closed":
        raise TicketError(f"{t['id']} is already closed")
    text = t["text"].replace("- status: open", "- status: closed", 1)
    text = text.rstrip() + f"\n\n## Closed\n\n{store.now()} by {by}: {note.strip() or '(no note)'}\n"
    Path(t["path"]).write_text(text, encoding="utf-8")
    return get(t["id"])


def context_of(project, rec: dict | None, cwd: str | None = None) -> tuple[dict, list[str]]:
    """Fields and recent events for a ticket filed from inside a project."""
    ctx: dict = {"folder": cwd or os.getcwd()}
    events: list[str] = []
    if project is not None:
        ctx["project"] = f"{project.name} ({project.root})"
        if rec:
            ctx.update(thread=rec.get("thread"), session=f"{rec['short']} ({rec['id']})")
        for e in store.read_events(project.state_dir, limit=200)[-12:]:
            rest = "  ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "event"))
            events.append(f"{e['t'][11:19]}  {e['event']:<15} {rest}")
    return ctx, events
