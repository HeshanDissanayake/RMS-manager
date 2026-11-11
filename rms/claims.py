"""Thread claims: at most one live Claude Code session holds a thread.

A session claims a thread itself (after asking the owner which one). While
the holder is alive, every other claim is refused. When the holder's process
is gone the claim is *stale*: it can be taken over only on purpose
(``take_over=True``, after the owner said yes), or released by the owner.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from rms import sessions, store, threads
from rms.config import Project


class ClaimError(Exception):
    pass


def _path(project: Project, thread_id: str) -> Path:
    return project.claims_dir / f"{thread_id}.json"


def get(project: Project, thread_id: str) -> dict | None:
    return store.read_json(_path(project, thread_id))


def all_claims(project: Project) -> dict[str, dict]:
    if not project.claims_dir.is_dir():
        return {}
    out = {}
    for p in project.claims_dir.glob("T*.json"):
        c = store.read_json(p)
        if c:
            out[c["thread"]] = c
    return out


def state(project: Project, claim: dict | None) -> tuple[str, dict | None]:
    """('free' | 'live' | 'stale', holder session record)."""
    if not claim:
        return "free", None
    holder = sessions.get(project, claim["session"])
    return ("live" if sessions.is_alive(holder) else "stale"), holder


def describe(project: Project, claim: dict | None) -> str:
    st, holder = state(project, claim)
    if st == "free":
        return "free"
    who = claim.get("short", "?")
    if st == "live":
        seen = holder.get("last_seen", "?")[11:19] if holder else "?"
        return f"held by {who} (live, last active {seen})"
    return f"stale claim by {who} (session gone)"


def claim(project: Project, session_id: str, thread_id: str,
          take_over: bool = False, allow_done: bool = False) -> dict:
    thread_id = thread_id.upper()
    known = threads.discover(project)
    t = known.get(thread_id)
    if t is None:
        raise ClaimError(f"no thread {thread_id} (known: {', '.join(known) or 'none'})")
    if t.done and not allow_done:
        raise ClaimError(f"{thread_id} is done; to work on it again, ask the owner, "
                         f"then: rms claim {thread_id} --allow-done")
    with store.locked(project.state_dir):
        s = sessions.get(project, session_id)
        if s is None:
            raise ClaimError("this session is not registered with RMS")
        held = s.get("thread")
        if held and (get(project, held) or {}).get("session") != session_id:
            s["thread"] = held = None  # its claim was released or taken over
            sessions.save(project, s)
        if held and held != thread_id:
            raise ClaimError(f"this session already holds {s['thread']}; "
                             f"release it first (rms release)")
        cur = get(project, thread_id)
        if cur and cur["session"] == session_id:
            return cur
        prev = None
        if cur:
            st, _ = state(project, cur)
            if st == "live":
                raise ClaimError(f"{thread_id} is {describe(project, cur)}. "
                                 f"Only one session may work on a thread.")
            if not take_over:
                raise ClaimError(f"{thread_id} has a {describe(project, cur)}. "
                                 f"Ask the owner; with their yes: "
                                 f"rms claim {thread_id} --take-over")
            prev = cur
            old = sessions.get(project, cur["session"])
            if old and old.get("thread") == thread_id:
                old["thread"] = None
                sessions.save(project, old)
        new = {"thread": thread_id, "session": session_id, "short": s["short"],
               "since": store.now(), "cwd": s["cwd"]}
        store.write_json(_path(project, thread_id), new)
        s["thread"] = thread_id
        sessions.save(project, s)
        store.append_event(project.state_dir, "take-over" if prev else "claim",
                           session=s["short"], thread=thread_id,
                           previous=prev["short"] if prev else None)
    return new


def update(project: Project, thread_id: str, **fields) -> dict:
    """Add fields to a thread's claim (checkout, areas, handover_due, ...)."""
    with store.locked(project.state_dir):
        c = get(project, thread_id)
        if c is None:
            raise ClaimError(f"{thread_id} is not claimed")
        c.update(fields)
        store.write_json(_path(project, thread_id), c)
    return c


def release(project: Project, thread_id: str, session_id: str | None = None,
            by: str = "session", reason: str | None = None) -> dict:
    """Free a claim. ``session_id`` given: only if that session holds it."""
    thread_id = thread_id.upper()
    with store.locked(project.state_dir):
        cur = get(project, thread_id)
        if cur is None:
            raise ClaimError(f"{thread_id} is not claimed")
        if session_id and cur["session"] != session_id:
            raise ClaimError(f"{thread_id} is held by {cur['short']}, not this session")
        _path(project, thread_id).unlink(missing_ok=True)
        holder = sessions.get(project, cur["session"])
        if holder and holder.get("thread") == thread_id:
            holder["thread"] = None
            sessions.save(project, holder)
        store.append_event(project.state_dir, "release", session=cur["short"],
                           thread=thread_id, by=by, reason=reason)
    return cur


def release_all(project: Project, session_id: str, by: str, reason: str) -> list[str]:
    freed = []
    with store.locked(project.state_dir):
        for tid, c in all_claims(project).items():
            if c["session"] == session_id:
                release(project, tid, by=by, reason=reason)
                freed.append(tid)
    return freed


# --- where a session may write -------------------------------------------

def writable_areas(project: Project, rec: dict) -> list[Path]:
    """Folders the session may write in: its side folder and/or its thread."""
    areas = []
    if rec.get("side"):
        areas.append((project.side_dir / rec["side"]).resolve())
    tid = rec.get("thread")
    if tid:
        c = get(project, tid) or {}
        if c.get("areas"):  # the thread's own checkout (lifecycle.open_for_claim)
            areas += [Path(a).resolve() for a in c["areas"]]
        else:
            t = threads.discover(project).get(tid)
            if t and t.folder:
                areas.append(t.folder.resolve())
    return areas


def may_write(project: Project, rec: dict, path: Path) -> bool:
    path = path.resolve()
    areas = writable_areas(project, rec)
    if any(path.is_relative_to(a) for a in areas):
        return True
    if any(fnmatch.fnmatch(str(path), g) for g in project.extra_writable):
        return True
    if rec.get("thread"):
        rel = project.rel(path)
        if any(fnmatch.fnmatch(rel, g) for g in project.shared_writable):
            return True
    return False
