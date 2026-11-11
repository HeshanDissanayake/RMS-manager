"""Everything about one thread, for `rms thread T<NN>` and the TUI's Thread tab.

- sources: each source repo in the thread's checkout: branch, commit, how far
  from main's pointer, uncommitted changes, size;
- builds: made by the thread and used by it (inventory), with sizes;
- experiments: experiments/EXP-*/ in the thread's folder (LLVM-regsw layout:
  README.md with a "# EXP-…: title" line and a **Status:**, summary.csv for
  the numbers, large data in <data_dir>/T<NN>/<EXP>/), with sizes;
- disk: totals per kind;
- claude: tokens per model and the sessions that worked on it (rms/usage.py);
- processes: what runs for the thread now: children of the Claude session
  that holds it, and any process whose working folder is in the thread's
  checkout, data or builds.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from rms import claims, inventory, lifecycle, sessions, threads, usage
from rms.config import Project
from rms.git import current_branch, dirty, git, pointer, submodule_paths

EXP_RE = re.compile(r"^(EXP-(?:T\d+-)?\d+[a-z]?)")
TITLE_RE = re.compile(r"^#\s+(EXP-[^:]+):\s*(.+)$", re.M)
STATUS_RE = re.compile(r"\*\*Status:\*\*\s*([A-Za-z]+)")


def _size(path: Path, sizes: bool) -> int | None:
    return inventory.size_of(path) if sizes and path.exists() else (0 if sizes else None)


# --- sources -----------------------------------------------------------------------

def sources(project: Project, tid: str, sizes: bool = True) -> list[dict]:
    wt = lifecycle.worktree(project, tid)
    out = []
    if not wt.is_dir():  # no checkout yet: what the thread's branch records
        br = project.thread_branch(tid)
        for p in submodule_paths(project.root):
            rec = pointer(project.root, br, p)
            main = pointer(project.root, project.main_branch, p)
            out.append({"repo": p, "path": None, "branch": None, "commit": rec, "recorded": rec,
                        "main": main, "ahead": 0, "dirty": 0, "bytes": 0 if sizes else None,
                        "state": "no checkout yet; " + ("same as main" if rec == main
                                                         else "differs from main")})
        return out
    for p in submodule_paths(wt):
        repo = wt / p
        row = {"repo": p, "path": project.rel(repo), "branch": None, "commit": None,
               "recorded": pointer(wt, "HEAD", p), "main": pointer(project.root, project.main_branch, p),
               "ahead": 0, "dirty": 0, "state": "not checked out", "bytes": None}
        if (repo / ".git").exists():
            br = current_branch(repo)
            row["branch"] = None if br == "HEAD" else br
            row["commit"] = git(repo, "rev-parse", "HEAD")
            row["dirty"] = len(dirty(repo))
            if row["main"]:
                n = git(repo, "rev-list", "--count", f"{row['main']}..HEAD", check=False)
                row["ahead"] = int(n) if n.isdigit() else 0
            if row["dirty"]:
                row["state"] = f"{row['dirty']} uncommitted change(s)"
            elif row["ahead"]:
                row["state"] = f"changed: {row['ahead']} commit(s) on top of main's"
            else:
                row["state"] = "unchanged"
            if lifecycle.is_main(project, tid):
                row["state"] += " (root checkout: record only)"
            row["bytes"] = _size(repo, sizes)
        out.append(row)
    return out


# --- experiments ---------------------------------------------------------------------

def _thread_folder(project: Project, tid: str) -> Path | None:
    d = lifecycle.thread_dir(project, tid) if lifecycle.uses_git(project) else None
    if d and d.is_dir():
        return d
    t = threads.discover(project).get(tid)
    return t.folder if t and t.folder and t.folder.is_dir() else None


def experiments(project: Project, tid: str, sizes: bool = True) -> list[dict]:
    folder = _thread_folder(project, tid)
    out = []
    if not folder or not (folder / "experiments").is_dir():
        return out
    for d in sorted((folder / "experiments").iterdir()):
        m = EXP_RE.match(d.name)
        if not d.is_dir() or not m:
            continue
        exp = m.group(1)
        readme = d / "README.md"
        text = readme.read_text(encoding="utf-8", errors="ignore") if readme.is_file() else ""
        t = TITLE_RE.search(text)
        s = STATUS_RE.search(text)
        data = project.data_dir / tid / exp
        out.append({
            "id": exp, "folder": project.rel(d),
            "title": t.group(2).strip() if t else d.name[len(exp):].strip("-").replace("-", " "),
            "status": s.group(1).lower() if s else "?",
            "has_summary": (d / "summary.csv").is_file(),
            "data": project.rel(data) if data.exists() else None,
            "data_bytes": _size(data, sizes) if data.exists() else 0,
            "record_bytes": _size(d, sizes),
        })
    return out


# --- processes ------------------------------------------------------------------------

def _uptime() -> float:
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def _proc(pid: int) -> dict | None:
    try:
        with open(f"/proc/{pid}/stat") as f:
            st = f.read()
        fields = st[st.rindex(")") + 2:].split()
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except (OSError, ValueError):
        return None
    tck = os.sysconf("SC_CLK_TCK")
    return {"pid": pid, "ppid": int(fields[1]), "state": fields[0], "cmd": cmd, "cwd": cwd,
            "seconds": max(0, int(_uptime() - int(fields[19]) / tck))}


def _all_procs() -> dict[int, dict]:
    out = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            p = _proc(int(name))
            if p and p["cmd"]:
                out[p["pid"]] = p
    return out


def processes(project: Project, tid: str) -> list[dict]:
    """Processes working for the thread now (never this process itself)."""
    procs = _all_procs()
    me = os.getpid()
    holder_pid = None
    c = claims.get(project, tid)
    if c:
        s = sessions.get(project, c["session"])
        if sessions.is_alive(s):
            holder_pid = s.get("pid")
    # folders that are the thread's own
    places = [project.data_dir / tid]
    if not lifecycle.is_main(project, tid):
        places.append(lifecycle.worktree(project, tid))
    else:
        d = _thread_folder(project, tid)
        if d:
            places.append(d)
    places += [project.root / b["path"] for b in inventory.read_record(project, tid)["builds"]]
    places = [p.resolve() for p in places]

    def from_bash_tool(pid: int) -> bool:
        """Started by the holder's Bash tool: an ancestor below the Claude process
        is Claude's shell wrapper (it sources a shell snapshot). MCP servers and
        other helpers Claude starts itself do not count."""
        seen, wrapped = 0, False
        while pid and pid in procs and seen < 64:
            if pid == holder_pid:
                return wrapped
            if "shell-snapshots/" in procs[pid]["cmd"]:
                wrapped = True
            pid = procs[pid]["ppid"]
            seen += 1
        return False

    out = []
    for pid, p in procs.items():
        if pid in (me, holder_pid) or " hook " in p["cmd"] and "rms" in p["cmd"]:
            continue
        parent = procs.get(p["ppid"])
        if parent and parent["cmd"] == p["cmd"]:
            continue  # a forked copy of its parent shell: the parent's row stands for it
        why = None
        if holder_pid and from_bash_tool(pid):
            why = "started by the session"
        else:
            cwd = Path(p["cwd"])
            if any(cwd == pl or cwd.is_relative_to(pl) for pl in places):
                why = "works in the thread's folders"
        if why:
            cmd = p["cmd"]
            if "shell-snapshots/" in cmd:  # Claude's wrapper: show the command it runs
                cmd = bash_tool_command(cmd)
            out.append({**p, "cmd": cmd, "why": why, "cwd": project.rel(p["cwd"])})
    return sorted(out, key=lambda r: r["seconds"], reverse=True)


def bash_tool_command(wrapper: str) -> str:
    """The command inside Claude's Bash-tool wrapper (… && eval '<cmd>' && pwd -P …)."""
    m = re.search(r"&& eval '(.*)'(?: < /dev/null)? && pwd -P", wrapper, re.S)
    if not m:
        return wrapper
    return " ".join(m.group(1).replace("'\"'\"'", "'").split())


# --- all of it -----------------------------------------------------------------------

def collect(project: Project, tid: str, sizes: bool = True) -> dict:
    tid = tid.upper()
    t = threads.discover(project).get(tid)
    c = claims.get(project, tid)
    st, holder = claims.state(project, c)
    view = inventory.thread_view(project, tid, sizes=sizes)
    srcs = sources(project, tid, sizes) if lifecycle.uses_git(project) else []
    exps = experiments(project, tid, sizes)
    data_total = _size(project.data_dir / tid, sizes) if (project.data_dir / tid).exists() else 0
    wt = lifecycle.worktree(project, tid)
    folder = _thread_folder(project, tid)
    disk = None
    if sizes:
        disk = {
            "sources": sum(r["bytes"] or 0 for r in srcs),
            "builds made": sum(b.get("bytes") or 0 for b in view["builds"]),
            "builds used": sum(b.get("bytes") or 0 for b in view["uses"]),
            "data": data_total or 0,
            "records": inventory.size_of(folder) if folder else 0,
        }
        disk["total (own)"] = disk["sources"] + disk["builds made"] + disk["data"] + disk["records"]
    return {
        "thread": tid, "name": t.name if t else "", "status": t.status if t else "?",
        "claim": st, "held_by": c["short"] if c else None,
        "checkout": (("root checkout (main)" if lifecycle.is_main(project, tid)
                      else project.rel(wt) + "/") if wt.is_dir() else None),
        "branch": project.thread_branch(tid),
        "sources": srcs, "builds": view["builds"], "uses": view["uses"], "experiments": exps,
        "data_bytes": data_total, "disk": disk, "processes": processes(project, tid),
        "claude": claude(project, tid),
    }


def claude(project: Project, tid: str) -> dict:
    usage.refresh(project)
    return usage.for_thread(project, tid)


def text(project: Project, tid: str) -> str:
    h = inventory.human
    r = collect(project, tid)
    lines = [f"{r['thread']}  {r['name']}  [{r['status']}]  claim: {r['claim']}"
             + (f" by {r['held_by']}" if r["held_by"] else ""),
             f"  branch {r['branch']}; checkout {r['checkout'] or '(none yet)'}", "", "Sources:"]
    lines += [f"  {s['repo']:<16} {s['branch'] or 'detached':<14} {(s['commit'] or '')[:9]}  "
              f"{s['state']}  ({h(s['bytes'] or 0)})" for s in r["sources"]] or ["  none"]
    lines += ["", "Builds made:"]
    lines += [f"  {b['name']:<26} {b['path']}  ({h(b.get('bytes') or 0)})" for b in r["builds"]] or ["  none"]
    lines += ["Builds used:"]
    lines += [f"  {b['name']:<26} {b.get('path', '?')}  ({h(b.get('bytes') or 0)})" for b in r["uses"]] or ["  none"]
    lines += ["", "Experiments:"]
    lines += [f"  {e['id']:<14} {e['status']:<8} {e['title'][:50]:<50} "
              f"{'summary' if e['has_summary'] else '-':<8} data {h(e['data_bytes'] or 0)}"
              for e in r["experiments"]] or ["  none"]
    lines += ["", "Disk:"]
    lines += [f"  {k:<12} {h(v)}" for k, v in (r["disk"] or {}).items()]
    lines += [""] + usage.text(project, r["thread"]).splitlines()
    lines += ["", "Running now:"]
    lines += [f"  {p['pid']:>7}  {p['seconds'] // 60:>4} min  {p['cwd']:<30} {p['cmd'][:70]}"
              for p in r["processes"]] or ["  nothing"]
    return "\n".join(lines)
