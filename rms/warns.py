"""Warnings for the owner: mismatches RMS found that nobody has settled yet.

RMS never stops work for these. It notes them (at handover, close and
`rms scan`) in <state_dir>/warnings.json; the owner settles each one:

- resolved:  the cause is gone (RMS marks this itself when a later scan no
             longer finds it, e.g. the build was recorded or ignored);
- dismissed: the owner accepts it as it is (`rms warnings dismiss <id>`).

Kinds: unrecorded-build (a build folder no thread recorded), missing-build (a
recorded build not on disk and not archived), unknown-data (data of no known
thread).
"""

from __future__ import annotations

import hashlib

from rms import store
from rms.config import Project

FILE = "warnings.json"


def _path(project: Project):
    return project.state_dir / FILE


def _id(kind: str, subject: str) -> str:
    return "W-" + hashlib.sha1(f"{kind}:{subject}".encode()).hexdigest()[:6].upper()


def load(project: Project) -> list[dict]:
    return store.read_json(_path(project), []) or []


def _save(project: Project, rows: list[dict]) -> None:
    store.write_json(_path(project), rows)


def open_rows(project: Project, thread: str | None = None) -> list[dict]:
    return [w for w in load(project) if w["status"] == "open"
            and (thread is None or w.get("thread") == thread)]


def from_scan(scan: dict) -> list[dict]:
    """The warnings a scan result stands for (not yet stored)."""
    out = []
    for u in scan.get("unrecorded", []):
        out.append({"kind": "unrecorded-build", "subject": u["path"], "thread": u["thread"],
                    "text": f"{u['path']}: build folder no thread recorded"
                            + (f"; looks like {u['thread']}'s ({u['why']})" if u["thread"] else
                               "; thread unknown")
                            + (f"; source {u['source']}" if u.get("source") else "")})
    for b in scan.get("missing", []):
        out.append({"kind": "missing-build", "subject": b["path"], "thread": b["thread"],
                    "text": f"{b['name']} ({b['path']}): recorded by {b['thread']} but not on "
                            f"disk and not archived"})
    for d in scan.get("unknown_data", []):
        out.append({"kind": "unknown-data", "subject": d["path"], "thread": None,
                    "text": f"{d['path']}: data of no known thread"})
    return out


def sync(project: Project, found: list[dict], scope_thread: str | None = None,
         by: str = "scan") -> dict:
    """Store new warnings; mark open ones whose cause is gone as resolved.

    scope_thread: only warnings of this thread are re-checked (handover/close);
    None: all (a full scan).
    Returns {"new": [...], "resolved": [...], "open": [...]}.
    """
    with store.locked(project.state_dir):
        rows = load(project)
        by_id = {w["id"]: w for w in rows}
        now = store.now()
        seen, new = set(), []
        for f in found:
            wid = _id(f["kind"], f["subject"])
            seen.add(wid)
            w = by_id.get(wid)
            if w is None:
                w = {"id": wid, **f, "status": "open", "first_seen": now, "last_seen": now,
                     "by": by}
                rows.append(w)
                by_id[wid] = w
                new.append(w)
                store.append_event(project.state_dir, "warning", id=wid, kind=f["kind"],
                                   thread=f.get("thread"), subject=f["subject"])
            else:
                w["last_seen"] = now
                w["text"], w["thread"] = f["text"], f.get("thread")
        resolved = []
        for w in rows:
            if w["status"] != "open" or w["id"] in seen:
                continue
            if scope_thread is not None and w.get("thread") != scope_thread:
                continue
            w["status"], w["settled"], w["settled_by"] = "resolved", now, "RMS (cause gone)"
            resolved.append(w)
            store.append_event(project.state_dir, "warning-resolved", id=w["id"])
        _save(project, rows)
    return {"new": new, "resolved": resolved,
            "open": [w for w in rows if w["status"] == "open"
                     and (scope_thread is None or w.get("thread") == scope_thread)]}


def settle(project: Project, wid: str, status: str, by: str = "owner", note: str = "") -> dict:
    """The owner settles a warning: dismissed (accepted as is) or resolved."""
    if status not in ("dismissed", "resolved"):
        raise ValueError("status is dismissed or resolved")
    with store.locked(project.state_dir):
        rows = load(project)
        for w in rows:
            if w["id"] == wid.upper():
                w["status"], w["settled"], w["settled_by"] = status, store.now(), by
                if note:
                    w["note"] = note
                _save(project, rows)
                store.append_event(project.state_dir, f"warning-{status}", id=w["id"], by=by)
                return w
    raise KeyError(f"no warning {wid}")


def check(project: Project, thread: str | None = None, by: str = "scan") -> dict:
    """Scan (whole project or one thread's finds) and sync the warnings."""
    from rms import inventory
    result = inventory.scan(project, sizes=False)
    found = from_scan(result)
    if thread is not None:
        found = [f for f in found if f.get("thread") == thread]
    return sync(project, found, scope_thread=thread, by=by)
