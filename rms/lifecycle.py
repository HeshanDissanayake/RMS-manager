"""Thread lifecycle: create, checkouts, handover, close, reopen, archive, restore.

Model (LLVM-regsw PLAN-sustainable-workflow, section 5; owner 2026-11-11):

- each thread has a branch ``thread/T<NN>`` in the root repo and one checkout
  ``<worktrees_dir>/T<NN>/``; the session works and writes only there;
- the source repos (submodules) get the same branch name and their own
  checkout inside the thread's checkout (``<worktree>/src/<repo>``);
- ``main`` holds T00, the INDEX and every thread's records, merged in at
  handover; the source pointers on main move only at a milestone;
- the handover note is THREAD.md (State, How to resume, Where things are,
  Open items), written by the session; RMS checks it and does the git work.
"""

from __future__ import annotations

import csv
import hashlib
import re
import subprocess
import tarfile
from datetime import date
from pathlib import Path
from typing import Callable

from rms import claims, index, store, threads
from rms.config import Project
from rms.git import (commit, current_branch, dirty, git, has_branch, head, message,
                     ok, pointer, subjects, submodule_paths)


class LifecycleError(Exception):
    pass


def today() -> str:
    return date.today().isoformat()


def uses_git(project: Project) -> bool:
    """True if the project root is the top of its own git repo."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=project.root,
                       capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == project.root


def _run(cwd: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def _merge_no_commit(repo: Path, branch: str, pointers: dict[str, str], what: str) -> None:
    """git merge --no-ff --no-commit; then set each submodule path to pointers[path].

    Conflicts on submodule pointers are expected (pointers move on different
    rules than files) and are settled from ``pointers``; any other conflict
    aborts the merge and raises.
    """
    rc, out = _run(repo, "merge", "--no-ff", "--no-commit", branch)
    if rc != 0:
        conflicted = set(git(repo, "diff", "--name-only", "--diff-filter=U").split())
        if not conflicted or not conflicted <= set(pointers):
            _run(repo, "merge", "--abort")
            raise LifecycleError(f"merging {what} failed; nothing changed:\n{out}")
    for path, sha in pointers.items():
        if sha:
            git(repo, "update-index", "--cacheinfo", f"160000,{sha},{path}")


# --- names ----------------------------------------------------------------------

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "thread"


def next_id(project: Project) -> str:
    nums = [int(t[1:]) for t in threads.discover(project)]
    for b in git(project.root, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split():
        m = re.search(r"\bT(\d+)\b", b)
        if m:
            nums.append(int(m.group(1)))
    return f"T{(max(nums) + 1) if nums else 0:02d}"


def folder_name(project: Project, tid: str) -> str | None:
    """The thread's folder name (T<NN>-<name>) on its branch, else on main."""
    rel = project.rel(project.threads_dir)
    for rev in (project.thread_branch(tid), project.main_branch):
        if not has_branch(project.root, rev):
            continue
        for line in git(project.root, "ls-tree", "--name-only", rev, rel + "/").splitlines():
            name = Path(line).name
            if name == tid or name.startswith(tid + "-"):
                return name
    return None


def worktree(project: Project, tid: str) -> Path:
    return project.worktrees_dir / tid


def thread_dir(project: Project, tid: str) -> Path | None:
    """The thread's folder inside its checkout (where the session writes)."""
    fn = folder_name(project, tid)
    return worktree(project, tid) / project.rel(project.threads_dir) / fn if fn else None


def thread_file(project: Project, tid: str) -> Path | None:
    """THREAD.md: in the thread's checkout if there is one, else on main."""
    d = thread_dir(project, tid)
    if d and (d / "THREAD.md").is_file():
        return d / "THREAD.md"
    t = threads.discover(project).get(tid)
    return t.thread_file if t and t.thread_file and t.thread_file.is_file() else None


# --- checkouts ------------------------------------------------------------------

def ensure_worktree(project: Project, tid: str) -> Path:
    """The thread's checkout, created from its branch if missing (sources too)."""
    wt = worktree(project, tid)
    br = project.thread_branch(tid)
    if not (wt / ".git").exists():
        if not has_branch(project.root, br):
            raise LifecycleError(f"{tid} has no branch {br} in the root repo")
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(project.root, "worktree", "add", "-q", str(wt), br)
        store.append_event(project.state_dir, "worktree", thread=tid, path=project.rel(wt))
    _ensure_sources(project, tid, wt)
    return wt


def _ensure_sources(project: Project, tid: str, wt: Path) -> None:
    br = project.thread_branch(tid)
    for path in submodule_paths(wt):
        dst = wt / path
        if (dst / ".git").exists():
            continue
        src = project.root / path
        if not (src / ".git").exists():
            raise LifecycleError(f"source repo {path} is not checked out in the root")
        sha = pointer(wt, "HEAD", path)
        if dst.is_dir() and not any(dst.iterdir()):
            dst.rmdir()
        if has_branch(src, br):  # the thread changed this repo before
            git(src, "worktree", "add", "-q", str(dst), br)
        else:  # unchanged so far: no branch until it changes (owner 2026-11-11)
            git(src, "worktree", "add", "-q", "--detach", str(dst), sha)


def source_of(project: Project, tid: str, path: Path) -> str | None:
    """The submodule path (e.g. src/fastlib) a file in the thread's checkout is in."""
    wt = worktree(project, tid).resolve()
    path = Path(path).resolve()
    for p in submodule_paths(wt) if wt.is_dir() else []:
        if path.is_relative_to(wt / p):
            return p
    return None


def ensure_source_branch(project: Project, tid: str, path: str) -> bool:
    """Give a source repo in the thread's checkout the thread's branch, now that it changes.

    Returns True if the branch was made here. Commits already made on the
    detached HEAD are kept (the branch starts at HEAD).
    """
    repo = worktree(project, tid) / path
    if not (repo / ".git").exists() or current_branch(repo) != "HEAD":
        return False
    br = project.thread_branch(tid)
    if has_branch(repo, br):
        raise LifecycleError(f"{path}: branch {br} exists but this checkout is detached")
    git(repo, "switch", "-q", "-c", br)
    store.append_event(project.state_dir, "source-branch", thread=tid, repo=path, branch=br)
    return True


def _source_changed(project: Project, tid: str, path: str) -> bool:
    wt = worktree(project, tid)
    repo = wt / path
    return bool(dirty(repo)) or git(repo, "rev-parse", "HEAD") != pointer(wt, "HEAD", path)


def areas(project: Project, tid: str) -> list[str]:
    """Where a session holding the thread may write: its folder + its sources."""
    wt = worktree(project, tid)
    out = []
    d = thread_dir(project, tid)
    if d:
        out.append(str(d))
    out += [str(wt / p) for p in submodule_paths(wt)] if wt.is_dir() else []
    return out


def open_for_claim(project: Project, tid: str) -> dict:
    """Make sure the thread's checkout exists; record it in the claim."""
    wt = ensure_worktree(project, tid)
    info = {"worktree": str(wt), "areas": areas(project, tid),
            "claim_note_hash": note_hash(project, tid)}
    claims.update(project, tid, **info)
    return info


# --- the root (main) ---------------------------------------------------------------

def _root_ready(project: Project) -> None:
    br = current_branch(project.root)
    if br != project.main_branch:
        raise LifecycleError(f"the root checkout is on {br}, not {project.main_branch}")
    if not ok(project.root, "diff", "--cached", "--quiet"):
        raise LifecycleError("the root has staged changes; the owner commits or unstages them first")


def _write_and_add(project: Project, files: dict[Path, str]) -> None:
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        git(project.root, "add", "--", str(path))


def commit_on_main(project: Project, files: dict[Path, str], subject: str,
                   trailers: dict[str, str]) -> str:
    _root_ready(project)
    _write_and_add(project, files)
    sha = commit(project.root, message(subject, trailers=trailers))
    if sha is None:
        raise LifecycleError("nothing to commit on main")
    return sha


def merge_into_main(project: Project, tid: str, subject: str, trailers: dict[str, str],
                    keep_pointers: bool = True,
                    index_edit: Callable[[str], str] | None = None,
                    files: dict[Path, str] | None = None) -> str:
    """Merge thread/<tid> into main in the root checkout (one commit).

    keep_pointers: main's source pointers stay where they are (handover);
    False: they move to the thread's (milestone).
    """
    root = project.root
    _root_ready(project)
    br = project.thread_branch(tid)
    merging = not ok(root, "merge-base", "--is-ancestor", br, "HEAD")
    if merging:
        rev = "HEAD" if keep_pointers else br
        _merge_no_commit(root, br, {p: pointer(root, rev, p) for p in submodule_paths(root)},
                         f"{br} into {project.main_branch}")
    edits = dict(files or {})
    if index_edit and project.index_file.is_file():
        edits[project.index_file] = index_edit(project.index_file.read_text(encoding="utf-8"))
    _write_and_add(project, edits)
    msg = message(subject, trailers=trailers)
    if merging:
        sha = commit(root, msg, allow_empty=True)
    else:
        sha = commit(root, msg)
    return sha or head(root)


def _index_status(tid: str, status: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        return index.set_cells(text, tid, {"status": status}) if index.get(text, tid) else text
    return edit


def _index_done(tid: str, merged_into: str) -> Callable[[str], str]:
    def edit(text: str) -> str:
        if not index.get(text, tid):
            return text
        return index.move_row(text, tid, "done", {"status": "done", "merged into": merged_into,
                                                  "closed": today()})
    return edit


# --- create ---------------------------------------------------------------------

THREAD_TEMPLATE = """# {tid}: {name}

- **Status:** {status} ({date}). **Spawned from:** {origin} at `{short}`.

## Goal

{goal}

## State as of {date}

Created; nothing done yet.

## Where things are

{sources}

## How to resume

1. `rms claim {tid}`; work in `{worktree}/` (branch `{branch}`).

## Open items

## Spawned threads
"""


def create(project: Project, name: str, origin: str | None = None, status: str = "parked",
           goal: str = "", by: str = "owner") -> dict:
    root = project.root
    if not uses_git(project):
        raise LifecycleError(f"{root} is not the top of a git repo")
    _root_ready(project)
    with store.locked(project.state_dir):
        tid = next_id(project)
        origin = (origin or "T00").upper()
        if origin in ("T00", "MAIN"):
            start, origin = project.main_branch, "T00"
        elif re.fullmatch(r"M\d+", origin):
            start = f"milestone-{origin[1:]}"
        elif re.fullmatch(r"T\d+", origin):
            start = project.thread_branch(origin)
            if not has_branch(root, start):
                raise LifecycleError(f"{origin} has no branch {start}")
        else:
            raise LifecycleError(f"--from takes T<NN>, M<N> or main, not {origin!r}")
        start_sha = head(root, start)
        br = project.thread_branch(tid)
        git(root, "branch", br, start_sha)
        wt = ensure_worktree(project, tid)
        fn = f"{tid}-{slug(name)}"
        d = wt / project.rel(project.threads_dir) / fn
        d.mkdir(parents=True)
        (d / "experiments").mkdir()
        (d / "experiments" / ".gitkeep").write_text("")
        sources = "\n".join(f"- `{p}`: branch `{br}` from `{(pointer(wt, 'HEAD', p) or '')[:9]}`"
                            for p in submodule_paths(wt)) or "- (no source repos)"
        (d / "THREAD.md").write_text(THREAD_TEMPLATE.format(
            tid=tid, name=name, status=status, date=today(), origin=origin,
            short=start_sha[:9], goal=goal or "(to write)", sources=sources,
            worktree=project.rel(wt), branch=br), encoding="utf-8")
        (d / "decisions.md").write_text(f"# {tid} decisions\n")
        (d / "hypotheses.md").write_text(f"# {tid} hypotheses\n")
        git(wt, "add", "--", str(d))
        commit(wt, message(f"{tid} created: {name}",
                           trailers={"Thread": tid, "Spawned-from": f"{origin} at {start_sha[:9]}"}))
        row = {"id": f"[{tid}]({fn}/THREAD.md)", "thread": name, "status": status,
               "since": today(), "spawned from": f"{origin} at `{start_sha[:9]}`"}
        files = {}
        if project.index_file.is_file():
            files[project.index_file] = index.add_row(
                project.index_file.read_text(encoding="utf-8"), "open", row)
        if files:
            commit_on_main(project, files, f"{tid} created: {name} (from {origin} at {start_sha[:9]})",
                           {"Thread": origin if origin.startswith("T") else "T00", "Spawns": tid})
        store.append_event(project.state_dir, "create", thread=tid, origin=origin, by=by)
    return {"thread": tid, "folder": fn, "branch": br, "worktree": str(wt), "from": origin,
            "at": start_sha[:9]}


# --- handover -------------------------------------------------------------------------

STATE_RE = re.compile(r"^## State as of (\d{4}-\d{2}-\d{2})", re.M)


def handover_steps(project: Project, tid: str) -> str:
    d = thread_dir(project, tid)
    rel = project.rel(d) if d else f"<worktree>/threads/{tid}-*"
    srcs = ", ".join(f"`{project.rel(worktree(project, tid) / p)}`"
                     for p in submodule_paths(worktree(project, tid))) or "(none)"
    return f"""Handover of {tid} is due. Write the handover note in {rel}/THREAD.md:
1. Rewrite "## State as of {today()}" (heading with today's date) and "## How to resume" in full.
2. Update "## Where things are" (repo, branch, commit of everything the thread changed or used) and "## Open items".
3. An idea that branched off: `rms create "<name>" --from {tid}` (a new parked thread).
4. Source repos {srcs}: commit your changes there (RMS gives a changed repo the branch `{project.thread_branch(tid)}`).
5. Builds: record each build this thread made (`rms build add <path> --recipe "..."`) and each it used (`rms build use <name>`). {_found_line(project, tid)} Anything left unrecorded becomes a warning for the owner; it does not stop the handover.
Then run `rms handover -m "<one line: what this session did>"` again: RMS checks the note,
commits on `{project.thread_branch(tid)}`, merges it into `{project.main_branch}` and releases the thread."""


def note_hash(project: Project, tid: str) -> str | None:
    d = thread_dir(project, tid)
    f = d / "THREAD.md" if d else None
    return hashlib.sha256(f.read_bytes()).hexdigest() if f and f.is_file() else None


def _found_line(project: Project, tid: str) -> str:
    from rms import inventory
    found = inventory.unrecorded_for(project, tid)
    if not found:
        return "RMS found no unrecorded builds of this thread."
    return "RMS found these unrecorded: " + ", ".join(u["path"] for u in found) + "."


def check_note(project: Project, tid: str, before: str | None = None) -> list[str]:
    """What still stops a handover (empty list: ready).

    before: THREAD.md's hash when the handover was marked due; the note must
    have changed since.
    """
    problems = []
    wt = worktree(project, tid)
    d = thread_dir(project, tid)
    f = d / "THREAD.md" if d else None
    if not f or not f.is_file():
        return [f"no THREAD.md for {tid} in its checkout"]
    text = f.read_text(encoding="utf-8")
    if before and note_hash(project, tid) == before:
        problems.append("THREAD.md has not changed since the handover was asked for: "
                        "write the handover note")
    m = STATE_RE.search(text)
    if not m:
        problems.append('THREAD.md has no "## State as of <date>" section')
    elif m.group(1) != today():
        problems.append(f'"## State as of {m.group(1)}" is not today ({today()}): rewrite State')
    resume = re.search(r"^## How to resume\s*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not resume or not resume.group(1).strip():
        problems.append('"## How to resume" is missing or empty')
    br = project.thread_branch(tid)
    for p in submodule_paths(wt):
        repo = wt / p
        if not (repo / ".git").exists():
            continue
        if current_branch(repo) == "HEAD":
            if not _source_changed(project, tid, p):
                continue  # unchanged: no branch needed
            ensure_source_branch(project, tid, p)
        if current_branch(repo) != br:
            problems.append(f"{p} is on {current_branch(repo)}, not {br}")
        if dirty(repo):
            problems.append(f"{p} has uncommitted changes: commit them on {br}")
    allowed = [str(d.relative_to(wt)), *submodule_paths(wt)]
    stray = [line for line in dirty(wt) if not any(line[3:].startswith(a) for a in allowed)]
    if stray:
        problems.append("changes outside the thread folder (RMS commits only the thread folder "
                        "and source pointers): " + "; ".join(s[3:] for s in stray[:5]))
    return problems


def _note_warnings(project: Project, tid: str, by: str) -> str:
    """Record the thread's mismatches as warnings for the owner (never blocks)."""
    from rms import warns
    r = warns.check(project, thread=tid, by=by)
    if not r["open"]:
        return ""
    return ("\nWarnings for the owner (" + str(len(r["open"])) + " open, `rms warnings`):\n"
            + "\n".join(f"- {w['id']} {w['text']}" for w in r["open"]))


def _headline(project: Project, tid: str) -> str:
    f = thread_file(project, tid)
    if not f:
        return ""
    body = threads.section_text(f.read_text(encoding="utf-8"), "State") or ""
    for line in body.splitlines()[1:]:
        if line.strip():
            return line.strip()[:80]
    return ""


def _commit_thread(project: Project, tid: str, subject: str,
                   trailers: dict[str, str] | None = None) -> str | None:
    wt = worktree(project, tid)
    d = thread_dir(project, tid)
    paths = [str(d.relative_to(wt))] if d else []
    paths += submodule_paths(wt)
    if paths:
        git(wt, "add", "-A", "--", *paths)
    return commit(wt, message(subject, trailers={"Thread": tid, **(trailers or {})}))


def _holder_check(project: Project, rec: dict) -> tuple[str, dict]:
    tid = rec.get("thread")
    if not tid:
        raise LifecycleError("this session holds no thread")
    c = claims.get(project, tid)
    if not c or c["session"] != rec["id"]:
        raise LifecycleError(f"this session does not hold {tid}")
    return tid, c


def handover(project: Project, rec: dict, summary: str | None = None) -> tuple[str, str]:
    """First call: mark the handover due, return the steps. Next call: check, commit, merge,
    release. Returns (stage, text): stage in due / blocked / done."""
    tid, c = _holder_check(project, rec)
    ensure_worktree(project, tid)
    if not c.get("handover_due"):
        claims.update(project, tid, handover_due=store.now(), note_hash=note_hash(project, tid))
        store.append_event(project.state_dir, "handover-due", session=rec["short"], thread=tid)
        return "due", handover_steps(project, tid)
    problems = check_note(project, tid, before=c.get("note_hash"))
    if problems:
        store.append_event(project.state_dir, "handover-blocked", session=rec["short"],
                           thread=tid, problems=len(problems))
        return "blocked", "Handover not done yet:\n" + "\n".join(f"- {p}" for p in problems)
    line = summary or _headline(project, tid) or "handover"
    _commit_thread(project, tid, f"{tid} handover {today()}: {line}")
    sha = merge_into_main(project, tid, f"Merge {project.thread_branch(tid)}: {tid} handover {today()}",
                          {"Thread": tid}, keep_pointers=True,
                          index_edit=_index_status(tid, "paused"))
    warn_text = _note_warnings(project, tid, by=rec["short"])
    claims.release(project, tid, session_id=rec["id"], by=rec["short"], reason="handover")
    store.append_event(project.state_dir, "handover", session=rec["short"], thread=tid,
                       commit=sha[:9])
    return "done", (f"{tid} handed over: committed on {project.thread_branch(tid)}, merged into "
                    f"{project.main_branch} ({sha[:9]}); thread released." + warn_text)


# --- close / reopen ------------------------------------------------------------------

def next_milestone(project: Project) -> int:
    tags = git(project.root, "tag", "--list", "milestone-*").split()
    nums = [int(t.split("-")[1]) for t in tags if t.split("-")[1].isdigit()]
    return (max(nums) + 1) if nums else 1


def _approval_path(project: Project, tid: str) -> Path:
    return project.state_dir / "approvals" / f"close-{tid}.json"


def approve_close(project: Project, tid: str, by: str = "owner") -> dict:
    """The owner's yes to closing a thread (run by the owner, never by a session)."""
    tid = tid.upper()
    if tid not in threads.discover(project):
        raise LifecycleError(f"no thread {tid}")
    rec = {"thread": tid, "by": by, "at": store.now()}
    store.write_json(_approval_path(project, tid), rec)
    store.append_event(project.state_dir, "close-approved", thread=tid, by=by)
    return rec


def close_approval(project: Project, tid: str) -> dict | None:
    return store.read_json(_approval_path(project, tid))


def close(project: Project, rec: dict, how: str, summary: str, parent: str | None = None) -> str:
    """how: milestone | merge (into parent) | none. Returns what it was merged into."""
    tid, c = _holder_check(project, rec)
    if not close_approval(project, tid):
        store.append_event(project.state_dir, "close-refused", session=rec["short"], thread=tid)
        raise LifecycleError(f"closing {tid} needs the owner's yes: ask the owner to run "
                             f"`rms approve-close {tid}` in their own terminal (or press c on "
                             f"{tid} in `rms tui`), then run this again")
    ensure_worktree(project, tid)
    problems = check_note(project, tid, before=c.get("claim_note_hash"))
    if problems:
        raise LifecycleError("close needs the final State first:\n"
                             + "\n".join(f"- {p}" for p in problems))
    root = project.root
    br = project.thread_branch(tid)
    _root_ready(project)
    if how == "merge":
        parent = (parent or "").upper()
        pc = claims.get(project, parent)
        if pc and claims.state(project, pc)[0] == "live":
            raise LifecycleError(f"{parent} is in use by {pc['short']}; merge when it is free")
    if how == "milestone":
        from rms import inventory
        inventory.pin_milestone(project, tid, f"M{next_milestone(project)}")
    _commit_thread(project, tid, f"{tid} closed: {summary}")

    if how == "milestone":
        n = next_milestone(project)
        name = f"M{n}"
        mf = project.milestones_file
        text = mf.read_text(encoding="utf-8") if mf.is_file() else (
            "# Milestones\n\n| Milestone | Date | Thread | What |\n|---|---|---|---|\n")
        text = text.rstrip("\n") + f"\n| {name} | {today()} | {tid} | {summary} |\n"
        sha = merge_into_main(project, tid, f"{name}: {tid} merged into {project.main_branch}: {summary}",
                              {"Thread": tid, "Merges": tid, "Milestone": name},
                              keep_pointers=False, index_edit=_index_done(tid, f"**{name}**"),
                              files={mf: text})
        git(root, "tag", f"milestone-{n}", sha)
        for p in submodule_paths(root):
            psha = pointer(root, sha, p)
            if psha and (root / p / ".git").exists():
                git(root / p, "tag", f"milestone-{n}", psha)
        merged = name
    elif how == "merge":
        pwt = ensure_worktree(project, parent)
        if dirty(pwt):
            raise LifecycleError(f"{parent}'s checkout has uncommitted changes")
        n = 1 + sum(s.startswith(f"M-{parent}-") for s in subjects(root, project.thread_branch(parent)))
        merged = f"M-{parent}-{n:02d}"
        wt = worktree(project, tid)
        for p in submodule_paths(pwt):
            child, mine = pointer(wt, "HEAD", p), pointer(pwt, "HEAD", p)
            if child and child != mine:
                ensure_source_branch(project, parent, p)
                rc, out = _run(pwt / p, "merge", "--no-edit", child)
                if rc != 0:
                    _run(pwt / p, "merge", "--abort")
                    raise LifecycleError(f"merging {tid}'s {p} into {parent} failed:\n{out}")
        _merge_no_commit(pwt, br, {p: git(pwt / p, "rev-parse", "HEAD")
                                   for p in submodule_paths(pwt) if (pwt / p / ".git").exists()},
                         f"{br} into {project.thread_branch(parent)}")
        commit(pwt, message(f"{merged}: {tid} merged into {parent}: {summary}",
                            trailers={"Thread": parent, "Merges": tid}), allow_empty=True)
        merge_into_main(project, tid, f"Merge {br}: {tid} closed, merged into {parent} ({merged})",
                        {"Thread": tid}, keep_pointers=True,
                        index_edit=_index_done(tid, merged))
    else:
        merged = "nothing"
        merge_into_main(project, tid, f"Merge {br}: {tid} closed (nothing to merge)",
                        {"Thread": tid}, keep_pointers=True, index_edit=_index_done(tid, "nothing"))
    _note_warnings(project, tid, by=rec["short"])
    claims.release(project, tid, session_id=rec["id"], by=rec["short"], reason="close")
    _approval_path(project, tid).unlink(missing_ok=True)  # used once
    store.append_event(project.state_dir, "close", session=rec["short"], thread=tid, merged=merged)
    return merged


def reopen(project: Project, tid: str, by: str = "owner") -> None:
    tid = tid.upper()
    text = project.index_file.read_text(encoding="utf-8")
    row = index.get(text, tid)
    if not row:
        raise LifecycleError(f"{tid} is not in the index")
    if not row.get("status", "").lower().startswith("done"):
        raise LifecycleError(f"{tid} is not done (status: {row.get('status')})")
    new = index.move_row(text, tid, "open", {"status": "paused"})
    commit_on_main(project, {project.index_file: new}, f"{tid} reopened", {"Thread": tid})
    store.append_event(project.state_dir, "reopen", thread=tid, by=by)


# --- archive / restore ---------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _catalog(project: Project) -> Path:
    return project.archive_dir / "catalog.csv"


CATALOG_FIELDS = ["kind", "name", "thread", "file", "sha256", "bytes", "date", "source"]


def catalog_rows(project: Project) -> list[dict]:
    if not _catalog(project).is_file():
        return []
    with open(_catalog(project), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _pack(project: Project, src: Path, kind: str, name: str, tid: str, by: str) -> dict:
    """tar.gz src into the archive; sha256 + size in catalog.csv. Removes nothing."""
    project.archive_dir.mkdir(parents=True, exist_ok=True)
    base = f"{kind}-{name.replace('/', '_')}-{today()}"
    out = project.archive_dir / f"{base}.tar.gz"
    k = 1
    while out.exists():
        k += 1
        out = project.archive_dir / f"{base}-{k}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        tf.add(src, arcname=src.name)
    rec = {"kind": kind, "name": name, "thread": tid, "file": out.name, "sha256": _sha256(out),
           "bytes": out.stat().st_size, "date": today(), "source": project.rel(src)}
    new = not _catalog(project).exists()
    with open(_catalog(project), "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(rec)
    store.append_event(project.state_dir, "archive", kind=kind, name=name, file=out.name, by=by)
    return rec


def _status(project: Project, tid: str) -> str:
    row = index.get(project.index_file.read_text(encoding="utf-8"), tid) \
        if project.index_file.is_file() else None
    return (row or {}).get("status", "")


def archive(project: Project, tid: str | None = None, exp: str | None = None,
            build: str | None = None, by: str = "owner") -> dict:
    """Pack a done thread's data (all of it, or one experiment) or a build.

    Nothing is deleted: the owner removes the working copy afterwards.
    """
    if build:
        from rms import inventory
        b = inventory.all_builds(project).get(build)
        if not b:
            raise LifecycleError(f"no recorded build {build}")
        src = project.root / b["path"]
        if not src.is_dir():
            raise LifecycleError(f"{b['path']} is not on disk")
        return _pack(project, src, "build", build, b["thread"], by)
    tid = (tid or "").upper()
    if not _status(project, tid).lower().startswith("done"):
        raise LifecycleError(f"only done threads' data is archived ({tid}: "
                             f"{_status(project, tid) or 'not in the index'})")
    src = project.data_dir / tid / exp if exp else project.data_dir / tid
    if not src.is_dir() or not any(src.iterdir()):
        raise LifecycleError(f"no data to archive in {project.rel(src)}/")
    return _pack(project, src, "data", f"{tid}/{exp}" if exp else tid, tid, by)


def restore(project: Project, tid: str | None = None, exp: str | None = None,
            build: str | None = None, file: str | None = None, by: str = "owner") -> dict:
    """Unpack an archived copy back to where it came from, after checking its sha256."""
    if build:
        kind, name = "build", build
    else:
        kind, name = "data", f"{(tid or '').upper()}/{exp}" if exp else (tid or "").upper()
    rows = [r for r in catalog_rows(project) if r.get("kind") == kind and r.get("name") == name]
    if file:
        rows = [r for r in rows if r["file"] == file]
    if not rows:
        raise LifecycleError(f"no archive of {kind} {name}")
    rec = rows[-1]
    path = project.archive_dir / rec["file"]
    if _sha256(path) != rec["sha256"]:
        raise LifecycleError(f"{rec['file']}: sha256 does not match the catalog; not restored")
    dst = project.root / rec["source"]
    if dst.exists() and any(dst.iterdir()):
        raise LifecycleError(f"{rec['source']}/ is not empty; the owner moves it away first")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "r:gz") as tf:
        tf.extractall(dst.parent, filter="data")
    store.append_event(project.state_dir, "restore", kind=kind, name=name, file=rec["file"], by=by)
    return rec
