"""What each thread built, uses and stored, and what is on disk that nobody recorded.

Records (in git, one file per thread, written by RMS):

    threads/T<NN>-<name>/store.json
    {
      "builds": [ {name, tool, path, source: {repo, branch, commit, dirty}, recipe,
                   cost: cheap|expensive, env, shared, milestones: [], note,
                   added, by, how: registered|found} ],
      "uses":   ["llvm/xreg-v7", ...],      builds of other threads this one uses
      "ignore": ["store/builds/tmp/x"]      found folders that are not builds
    }

The file of a thread is read from its checkout when there is one (current
work), else from its branch, else from main. Data needs no record: every
folder under <data_dir>/T<NN>/ belongs to T<NN>.

RMS never builds and never runs recipes. It records what sessions tell it
(`rms build add`) and, with `rms scan`, finds what they did not tell it: a
folder under <builds_dir>/<tool>/<name> that no thread recorded. For such a
folder it guesses the thread (the name's T<NN>- prefix, a logged command that
names the path, or the one thread claimed when its files were written) and
the source (CMakeCache.txt, config.log).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from rms import claims, index, lifecycle, sessions, store, threads
from rms.config import Project
from rms.git import current_branch, dirty, git, has_branch

STORE_FILE = "store.json"
EMPTY = {"builds": [], "uses": [], "ignore": []}
COMMANDS = "commands.jsonl"


class InventoryError(Exception):
    pass


# --- reading the records ------------------------------------------------------------

def _rel_file(project: Project, tid: str) -> str | None:
    fn = lifecycle.folder_name(project, tid) if lifecycle.uses_git(project) else None
    if fn is None:
        t = threads.discover(project).get(tid)
        fn = t.folder.name if t and t.folder else None
    return f"{project.rel(project.threads_dir)}/{fn}/{STORE_FILE}" if fn else None


def read_record(project: Project, tid: str) -> dict:
    """A thread's store.json: checkout > its branch > main > empty."""
    rel = _rel_file(project, tid)
    if rel is None:
        return json.loads(json.dumps(EMPTY))
    wt_file = lifecycle.worktree(project, tid) / rel
    if wt_file.is_file():
        data = store.read_json(wt_file, None)
    else:
        data = None
        if lifecycle.uses_git(project):
            for rev in (project.thread_branch(tid), project.main_branch):
                if has_branch(project.root, rev):
                    out = git(project.root, "show", f"{rev}:{rel}", check=False)
                    if out:
                        try:
                            data = json.loads(out)
                            break
                        except json.JSONDecodeError:
                            pass
        if data is None and (project.root / rel).is_file():
            data = store.read_json(project.root / rel, None)
    data = data or {}
    return {k: list(data.get(k, [])) for k in EMPTY}


def _write_record(project: Project, tid: str, data: dict) -> Path:
    """Write a thread's store.json in its checkout (committed at its handover)."""
    rel = _rel_file(project, tid)
    if rel is None:
        raise InventoryError(f"{tid} has no folder")
    base = lifecycle.worktree(project, tid) if lifecycle.uses_git(project) else project.root
    if lifecycle.uses_git(project) and not (base / ".git").exists():
        raise InventoryError(f"{tid} has no checkout; claim it first")
    path = base / rel
    store.write_json(path, data)
    return path


def all_builds(project: Project) -> dict[str, dict]:
    """name -> build record (with its owner thread), over every thread."""
    out: dict[str, dict] = {}
    for tid in threads.discover(project):
        for b in read_record(project, tid)["builds"]:
            out[b["name"]] = {**b, "thread": tid}
    return out


# --- sizes and times ---------------------------------------------------------------

def size_of(path: Path) -> int:
    r = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True)
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def newest_mtime(path: Path, limit: int = 20000) -> float:
    """Newest file modification time in a folder (looks at most `limit` files)."""
    newest, n = path.stat().st_mtime if path.exists() else 0.0, 0
    for dp, _dn, fns in os.walk(path):
        for f in fns:
            try:
                newest = max(newest, os.lstat(os.path.join(dp, f)).st_mtime)
            except OSError:
                pass
            n += 1
            if n >= limit:
                return newest
    return newest


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# --- recording (a session that holds the thread) -------------------------------------

def _source_info(project: Project, tid: str, repo: str) -> dict:
    """Branch and commit of a source repo in the thread's checkout, right now."""
    path = lifecycle.worktree(project, tid) / repo
    if not (path / ".git").exists():
        path = project.root / repo
    if not (path / ".git").exists():
        raise InventoryError(f"no source repo {repo}")
    br = current_branch(path)
    return {"repo": repo, "branch": None if br == "HEAD" else br,
            "commit": git(path, "rev-parse", "HEAD"), "dirty": bool(dirty(path))}


def add_build(project: Project, rec: dict, path: str, name: str | None = None,
              tool: str | None = None, source: str | None = None, recipe: str = "",
              cost: str = "cheap", env: str | None = None, shared: bool = False,
              note: str = "", how: str = "registered") -> dict:
    tid = rec.get("thread")
    if not tid:
        raise InventoryError("record builds from the session that holds the thread")
    p = (Path(path) if os.path.isabs(path) else project.root / path).resolve()
    if not p.exists():
        raise InventoryError(f"{path} does not exist")
    relp = project.rel(p)
    parts = Path(relp).parts
    bd = Path(project.rel(project.builds_dir)).parts
    if parts[:len(bd)] == bd and len(parts) >= len(bd) + 2:
        tool = tool or parts[len(bd)]
        name = name or f"{parts[len(bd)]}/{parts[len(bd) + 1]}"
    if not name or not tool:
        raise InventoryError("give --name and --tool for a build outside the builds folder")
    known = all_builds(project)
    if name in known and known[name]["thread"] != tid:
        raise InventoryError(f"build {name} is already recorded by {known[name]['thread']}")
    if source is None:
        guess = guess_source(project, p)
        source = guess.get("repo") if guess.get("thread") in (tid, None) else None
    entry = {"name": name, "tool": tool, "path": relp,
             "source": _source_info(project, tid, source) if source else None,
             "recipe": recipe, "cost": cost, "env": env, "shared": shared,
             "milestones": [], "note": note, "added": store.now(), "by": rec["short"],
             "how": how}
    data = read_record(project, tid)
    data["builds"] = [b for b in data["builds"] if b["name"] != name] + [entry]
    data["ignore"] = [x for x in data["ignore"] if x != relp]
    _write_record(project, tid, data)
    store.append_event(project.state_dir, "build-add", session=rec["short"], thread=tid,
                       build=name, how=how)
    return entry


def use_build(project: Project, rec: dict, name: str, drop: bool = False) -> None:
    tid = rec.get("thread")
    if not tid:
        raise InventoryError("this session holds no thread")
    if not drop and name not in all_builds(project):
        raise InventoryError(f"no recorded build {name} (rms builds lists them)")
    data = read_record(project, tid)
    data["uses"] = [u for u in data["uses"] if u != name] + ([] if drop else [name])
    _write_record(project, tid, data)
    store.append_event(project.state_dir, "build-unuse" if drop else "build-use",
                       session=rec["short"], thread=tid, build=name)


def ignore_path(project: Project, rec: dict, path: str) -> None:
    tid = rec.get("thread")
    if not tid:
        raise InventoryError("this session holds no thread")
    relp = project.rel((project.root / path).resolve() if not os.path.isabs(path) else Path(path))
    data = read_record(project, tid)
    if relp not in data["ignore"]:
        data["ignore"].append(relp)
    _write_record(project, tid, data)
    store.append_event(project.state_dir, "build-ignore", session=rec["short"], thread=tid,
                       path=relp)


def pin_milestone(project: Project, tid: str, milestone: str) -> None:
    """At a milestone: the thread's builds and the builds it uses are named by it."""
    data = read_record(project, tid)
    for b in data["builds"]:
        b.setdefault("milestones", [])
        if milestone not in b["milestones"]:
            b["milestones"].append(milestone)
    data["milestone_uses"] = sorted(set(data.get("milestone_uses", [])) | set(data["uses"]))
    _write_record(project, tid, data)


# --- the command log (who ran what, where) ----------------------------------------------

def log_command(project: Project, rec: dict, cwd: str, command: str) -> None:
    line = {"t": store.now(), "session": rec["short"], "thread": rec.get("thread"),
            "cwd": cwd, "command": command[:1000]}
    with open(project.state_dir / COMMANDS, "a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")


def _commands(project: Project) -> list[dict]:
    out = []
    try:
        with open(project.state_dir / COMMANDS, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


# --- guessing ----------------------------------------------------------------------------

def guess_source(project: Project, build: Path) -> dict:
    """Source repo (and thread, if it is a thread's checkout) a build came from."""
    src_dir = None
    cache = build / "CMakeCache.txt"
    if cache.is_file():
        m = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$", cache.read_text(errors="ignore"), re.M)
        if m:
            src_dir = m.group(1).strip()
    log = build / "config.log"
    if src_dir is None and log.is_file():
        m = re.search(r"^\s*\$ (\S*)/configure", log.read_text(errors="ignore"), re.M)
        if m:
            src_dir = m.group(1)
    if not src_dir:
        return {}
    sp = Path(src_dir).resolve()
    out = {"source_dir": str(sp)}
    wtd = project.worktrees_dir.resolve()
    if sp.is_relative_to(wtd):
        rel = sp.relative_to(wtd).parts
        out["thread"] = rel[0]
        out["repo"] = str(Path(*rel[1:3])) if len(rel) >= 3 else None
    elif sp.is_relative_to(project.root):
        rel = sp.relative_to(project.root).parts
        out["repo"] = str(Path(*rel[:2])) if len(rel) >= 2 else None
    return out


def _claims_at(project: Project, when: float) -> set[str]:
    """Threads that were claimed at a moment, from the event log."""
    held: dict[str, float] = {}
    spans: list[tuple[str, float, float]] = []
    for e in store.read_events(project.state_dir):
        t = store.parse_time(e.get("t"))
        if not t or not e.get("thread"):
            continue
        ts = t.timestamp()
        if e["event"] in ("claim", "take-over"):
            held[e["thread"]] = ts
        elif e["event"] == "release" and e["thread"] in held:
            spans.append((e["thread"], held.pop(e["thread"]), ts))
    now = time.time()
    spans += [(tid, start, now) for tid, start in held.items()]
    return {tid for tid, a, b in spans if a - 60 <= when <= b + 60}


def guess_thread(project: Project, build: Path, mtime: float) -> tuple[str | None, str]:
    """(thread, why) for a folder nobody recorded."""
    m = re.match(r"(T\d+)-", build.name)
    if m:
        return m.group(1), "name"
    src = guess_source(project, build)
    if src.get("thread"):
        return src["thread"], "built from its checkout"
    relp = project.rel(build)
    hits = [c for c in _commands(project) if c.get("thread") and
            (relp in c["command"] or str(build) in c["command"])]
    if hits:
        return hits[-1]["thread"], f"command by {hits[-1]['session']}"
    held = _claims_at(project, mtime)
    if len(held) == 1:
        return next(iter(held)), "only thread claimed when it was written"
    return None, "unknown"


def guess_recipe(project: Project, build: Path) -> str:
    relp = project.rel(build)
    cmds = [c["command"] for c in _commands(project)
            if relp in c["command"] or str(build) in c["command"]
            or (c.get("cwd") and Path(c["cwd"]).resolve() == build.resolve())]
    return " && ".join(dict.fromkeys(cmds))[:500]


# --- the scan ------------------------------------------------------------------------------

def build_folders(project: Project) -> list[Path]:
    """Candidate builds on disk: <builds_dir>/<tool>/<name>/."""
    out = []
    if project.builds_dir.is_dir():
        for tool in sorted(project.builds_dir.iterdir()):
            if tool.is_dir():
                out += sorted(d for d in tool.iterdir() if d.is_dir())
    return out


def scan(project: Project, sizes: bool = True) -> dict:
    """Compare the records with the disk.

    unrecorded: build folders no thread recorded (with guesses);
    missing:    recorded builds whose folder is gone and that are not archived;
    unknown_data: data folders that belong to no known thread.
    """
    known = all_builds(project)
    by_path = {b["path"]: b for b in known.values()}
    ignored = {p for tid in threads.discover(project) for p in read_record(project, tid)["ignore"]}
    archived = {r.get("name") for r in lifecycle.catalog_rows(project) if r.get("kind") == "build"}
    unrecorded = []
    for d in build_folders(project):
        relp = project.rel(d)
        if relp in by_path or relp in ignored:
            continue
        mt = newest_mtime(d)
        tid, why = guess_thread(project, d, mt)
        src = guess_source(project, d)
        unrecorded.append({
            "path": relp, "thread": tid, "why": why, "source": src.get("repo"),
            "source_dir": src.get("source_dir"), "recipe": guess_recipe(project, d),
            "written": datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds"),
            "bytes": size_of(d) if sizes else None})
    missing = [b for b in known.values()
               if not (project.root / b["path"]).exists() and b["name"] not in archived]
    tids = set(threads.discover(project))
    unknown_data = []
    if project.data_dir.is_dir():
        for d in sorted(project.data_dir.iterdir()):
            if d.is_dir() and d.name not in tids:
                unknown_data.append({"path": project.rel(d), "bytes": size_of(d) if sizes else None})
    return {"unrecorded": unrecorded, "missing": missing, "unknown_data": unknown_data}


def unrecorded_for(project: Project, tid: str) -> list[dict]:
    return [u for u in scan(project, sizes=False)["unrecorded"] if u["thread"] == tid]


# --- a thread's view -----------------------------------------------------------------------

def thread_view(project: Project, tid: str, sizes: bool = True) -> dict:
    data = read_record(project, tid)
    known = all_builds(project)
    own = [{**b, "exists": (project.root / b["path"]).exists()} for b in data["builds"]]
    used = [{**known[n], "exists": (project.root / known[n]["path"]).exists()} if n in known
            else {"name": n, "missing_record": True} for n in data["uses"]]
    if sizes:
        for b in own + used:
            if b.get("exists"):
                b["bytes"] = size_of(project.root / b["path"])
    exps = []
    d = project.data_dir / tid
    if d.is_dir():
        for e in sorted(d.iterdir()):
            exps.append({"path": project.rel(e), "bytes": size_of(e) if sizes else None})
    return {"thread": tid, "builds": own, "uses": used, "data": exps}


def view_text(project: Project, tid: str) -> str:
    v = thread_view(project, tid)
    lines = []

    def b_line(b: dict) -> str:
        if b.get("missing_record"):
            return f"  {b['name']}  (no such recorded build)"
        size = human(b["bytes"]) if b.get("bytes") is not None else "missing on disk"
        src = b.get("source") or {}
        s = f"{src.get('repo')}@{(src.get('commit') or '')[:9]}" if src else "source unknown"
        env = f"  ${b['env']}" if b.get("env") else ""
        return f"  {b['name']:<28} {b['path']}  ({size}, {b.get('cost', '?')}, {s}){env}"

    lines.append(f"Builds {tid} made:" if v["builds"] else f"Builds {tid} made: none")
    lines += [b_line(b) for b in v["builds"]]
    lines.append("Builds it uses:" if v["uses"] else "Builds it uses: none")
    lines += [b_line(b) for b in v["uses"]]
    lines.append("Data:" if v["data"] else "Data: none")
    lines += [f"  {e['path']}  ({human(e['bytes'] or 0)})" for e in v["data"]]
    found = unrecorded_for(project, tid)
    if found:
        lines.append("Found on disk, not recorded (looks like this thread's):")
        lines += [f"  {u['path']}  ({u['why']}; source {u['source'] or '?'})" for u in found]
    return "\n".join(lines)


def env_lines(project: Project, tid: str) -> list[str]:
    v = thread_view(project, tid, sizes=False)
    out = []
    for b in v["builds"] + v["uses"]:
        if b.get("env") and b.get("path"):
            out.append(f'export {b["env"]}="{project.root / b["path"]}"')
    return out


# --- clean-up candidates (the owner decides; nothing is removed here) -----------------------

def gc(project: Project, weeks: int = 4) -> list[dict]:
    """Builds and data no open thread or milestone needs.

    Kept: owned or used by a thread that is not done; named by a milestone;
    a thread that stopped less than `weeks` ago. The rest: cheap -> the owner
    may remove (recipe kept); expensive or data -> archive first.
    """
    text = project.index_file.read_text(encoding="utf-8") if project.index_file.is_file() else ""
    status = {tid: (index.get(text, tid) or {}).get("status", "") for tid in threads.discover(project)}
    open_t = {tid for tid, s in status.items() if not s.lower().startswith("done")}
    live = {c["thread"] for c in claims.all_claims(project).values()}
    records = {tid: read_record(project, tid) for tid in threads.discover(project)}
    needed = set()
    for tid, r in records.items():
        if tid in open_t or tid in live:
            needed |= {b["name"] for b in r["builds"]} | set(r["uses"])
        needed |= set(r.get("milestone_uses", []))
    cutoff = time.time() - weeks * 7 * 86400
    archived = {(r.get("kind"), r.get("name")) for r in lifecycle.catalog_rows(project)}
    out = []
    for name, b in all_builds(project).items():
        p = project.root / b["path"]
        if not p.exists() or name in needed or b.get("milestones"):
            continue
        if newest_mtime(p) > cutoff:
            continue  # changed in the last `weeks`: keep
        exp = b.get("cost") == "expensive"
        out.append({"kind": "build", "name": name, "path": b["path"], "thread": b["thread"],
                    "bytes": size_of(p),
                    "action": ("archived; the owner may remove it" if ("build", name) in archived
                               else "archive first (expensive), then the owner removes" if exp
                               else "the owner may remove it (cheap: recipe kept)")})
    for tid in sorted(set(status) - open_t - live):
        d = project.data_dir / tid
        if d.is_dir() and any(d.iterdir()) and newest_mtime(d) <= cutoff:
            out.append({"kind": "data", "name": tid, "path": project.rel(d), "thread": tid,
                        "bytes": size_of(d),
                        "action": ("archived; the owner may remove it" if ("data", tid) in archived
                                   else "archive first, then the owner removes")})
    return out
