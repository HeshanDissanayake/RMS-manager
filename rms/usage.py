"""Claude usage per thread: which models, how many tokens, which sessions.

Source: Claude Code's own transcripts (``~/.claude/projects/<folder>/<session>.jsonl``
and ``<session>/subagents/agent-*.jsonl``). Every assistant message there carries
its model and token usage. RMS reads them and keeps its own record per session in
``<state_dir>/usage/<session id>.json``, so the numbers survive when Claude Code
cleans old transcripts away.

Which thread a message counts for:
1. the thread the session held at that moment (claim/release events), else
2. the thread whose checkout the session was in (<worktrees_dir>/T<NN>/), else
3. no thread ("").

Sessions RMS never saw (no hooks, e.g. started elsewhere) are found by their
folder: any transcript whose working folder is inside the project counts.

Tokens per model: input, cache write, cache read, output (thinking is part of
output). "total" is all four. Claude Code's own background calls (titles,
summaries; often a small model) are not in the messages; they come from the
transcript's last cost record and count for the thread the session spent most on.
Cost: Claude Code's own cost per model for the session, shared out between threads
by the tokens' price weights (input 1, cache write 1.25/2, cache read 0.1, output 5).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from rms import sessions, store
from rms.config import Project

KINDS = ("input", "cache_write", "cache_read", "output")
WEIGHT = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
VERSION = 2  # bump to re-read every transcript


# --- where the transcripts are --------------------------------------------------------

def projects_dir() -> Path:
    if os.environ.get("RMS_CLAUDE_PROJECTS"):
        return Path(os.environ["RMS_CLAUDE_PROJECTS"])
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def folder_name(path: Path | str) -> str:
    """Claude Code's folder name for a working folder (/a/b_c -> -a-b-c)."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def usage_dir(project: Project) -> Path:
    return project.state_dir / "usage"


def _inside(project: Project, cwd: str | None) -> bool:
    if not cwd:
        return False
    try:
        Path(cwd).resolve().relative_to(project.root.resolve())
        return True
    except ValueError:
        return False


def transcripts(project: Project) -> dict[str, list[Path]]:
    """session id -> its transcript files (main first, then subagents)."""
    out: dict[str, list[Path]] = {}

    def add(main: Path) -> None:
        sid = main.stem
        files = [main] + sorted((main.parent / sid / "subagents").glob("agent-*.jsonl"))
        out[sid] = files

    for rec in sessions.all_sessions(project):
        t = rec.get("transcript")
        if t and Path(t).is_file():
            add(Path(t))
    base = projects_dir()
    prefix = folder_name(project.root.resolve())
    if base.is_dir():
        for d in base.iterdir():
            if not (d.is_dir() and (d.name == prefix or d.name.startswith(prefix + "-"))):
                continue
            for f in d.glob("*.jsonl"):
                if f.stem not in out and _inside(project, _first_cwd(f)):
                    add(f)
    return out


def _first_cwd(f: Path) -> str | None:
    try:
        with open(f, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh):
                if '"cwd"' in line:
                    try:
                        return json.loads(line).get("cwd")
                    except json.JSONDecodeError:
                        pass
                if i > 200:
                    break
    except OSError:
        pass
    return None


# --- which thread, when ------------------------------------------------------------------

def _t(text: str | None) -> datetime | None:
    d = store.parse_time(text.replace("Z", "+00:00")) if text else None
    if d and d.tzinfo is None:
        d = d.astimezone()
    return d


def held_intervals(project: Project, short: str) -> list[tuple[datetime, datetime | None, str]]:
    """(from, to, thread) for each stretch the session (by short id) held a thread."""
    out, open_ = [], {}
    for e in store.read_events(project.state_dir):
        ev, t = e.get("event"), _t(e.get("t"))
        if t is None:
            continue
        if ev in ("claim", "take-over") and e.get("session") == short:
            open_[e["thread"]] = t
        if ev == "take-over" and e.get("previous") == short and e["thread"] in open_:
            out.append((open_.pop(e["thread"]), t, e["thread"]))
        if ev == "release" and e.get("session") == short and e.get("thread") in open_:
            out.append((open_.pop(e["thread"]), t, e["thread"]))
        if ev == "session-end" and e.get("session") == short:
            out += [(s, t, th) for th, s in open_.items()]
            open_ = {}
    out += [(s, None, th) for th, s in open_.items()]
    return out


def _thread_by_folder(project: Project, cwd: str | None) -> str:
    if not cwd:
        return ""
    try:
        rel = Path(cwd).resolve().relative_to(project.worktrees_dir.resolve())
    except ValueError:
        return ""
    return rel.parts[0].upper() if rel.parts and re.fullmatch(r"T\d+", rel.parts[0], re.I) else ""


# --- reading one session ---------------------------------------------------------------

def _empty() -> dict:
    return {k: 0 for k in KINDS} | {"messages": 0}


def _add(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + v


def _tokens(u: dict) -> dict:
    return {"input": u.get("input_tokens") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or 0,
            "cache_read": u.get("cache_read_input_tokens") or 0,
            "output": u.get("output_tokens") or 0}


def _prompt_text(msg: dict) -> str | None:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c if isinstance(b, dict)):
            return None
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(texts) if texts else None
    return None


def read_session(project: Project, sid: str, files: list[Path]) -> dict:
    rec = sessions.get(project, sid)
    short = rec["short"] if rec else sessions.short_id(sid)
    spans = held_intervals(project, short) if rec else []
    msgs: dict[str, dict] = {}
    tools_seen: dict[str, str] = {}  # one message is spread over several lines
    info = {"id": sid, "short": short, "registered": bool(rec), "title": None,
            "first_prompt": None, "version": None, "cwd": None, "first": None, "last": None,
            "prompts": 0, "tools": {}, "subagents": max(0, len(files) - 1), "cost": None}
    for n, f in enumerate(files):
        try:
            fh = open(f, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = d.get("type")
                ts = d.get("timestamp")
                if ts and n == 0:
                    info["first"] = info["first"] or ts
                    info["last"] = ts
                if n == 0 and d.get("cwd"):
                    info["cwd"] = info["cwd"] or d["cwd"]
                    info["version"] = d.get("version") or info["version"]
                if typ == "ai-title" and n == 0:
                    info["title"] = d.get("aiTitle") or info["title"]
                elif typ == "cost-state" and n == 0:
                    info["cost"] = d
                elif typ == "user" and n == 0 and not d.get("isMeta") and not d.get("isSidechain"):
                    text = _prompt_text(d.get("message") or {})
                    if text and not text.startswith("<"):
                        info["prompts"] += 1
                        info["first_prompt"] = info["first_prompt"] or text.strip()[:200]
                elif typ == "assistant":
                    m = d.get("message") or {}
                    model, u = m.get("model"), m.get("usage")
                    if not u or not model or model.startswith("<"):
                        continue
                    key = m.get("id") or d.get("requestId") or d.get("uuid")
                    for i, b in enumerate(m.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            tools_seen[b.get("id") or f"{key}/{i}"] = b.get("name", "?")
                    msgs[key] = {"t": ts, "model": model, "tok": _tokens(u), "cwd": d.get("cwd"),
                                 "w1h": (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0}

    for name in tools_seen.values():
        info["tools"][name] = info["tools"].get(name, 0) + 1
    by_thread: dict[str, dict[str, dict]] = {}
    weights: dict[str, dict[str, float]] = {}
    for m in msgs.values():
        t = _t(m["t"])
        tid = ""
        for a, b, th in spans:
            if t and a <= t and (b is None or t <= b):
                tid = th
        tid = tid or _thread_by_folder(project, m["cwd"])
        row = by_thread.setdefault(tid, {}).setdefault(m["model"], _empty())
        _add(row, m["tok"])
        row["messages"] += 1
        w = sum(WEIGHT[k] * m["tok"][k] for k in KINDS) + 0.75 * m["w1h"]
        weights.setdefault(m["model"], {}).setdefault(tid, 0.0)
        weights[m["model"]][tid] += w

    # background calls and cost, from Claude Code's own cost record
    cost_total, cost = None, {}
    cs = info.pop("cost")
    if cs:
        cost_total = cs.get("totalCostUSD")
        main = max(by_thread, key=lambda k: total(by_thread[k]), default="")
        for model, mu in (cs.get("modelUsage") or {}).items():
            seen = _empty()
            for rows in by_thread.values():
                _add(seen, rows.get(model, {}))
            extra = {"input": mu.get("inputTokens", 0), "cache_write": mu.get("cacheCreationInputTokens", 0),
                     "cache_read": mu.get("cacheReadInputTokens", 0), "output": mu.get("outputTokens", 0)}
            extra = {k: max(0, v - seen[k]) for k, v in extra.items()}
            if any(extra.values()):
                row = by_thread.setdefault(main, {}).setdefault(model, _empty())
                _add(row, extra)
                weights.setdefault(model, {}).setdefault(main, 0.0)
                weights[model][main] += sum(WEIGHT[k] * extra[k] for k in KINDS)
            usd = mu.get("costUSD") or 0
            ws = weights.get(model, {})
            all_w = sum(ws.values())
            for tid, w in ws.items():
                cost.setdefault(tid, {})[model] = usd * w / all_w if all_w else 0
        info.update(lines_added=cs.get("totalLinesAdded"), lines_removed=cs.get("totalLinesRemoved"),
                    api_seconds=round((cs.get("totalAPIDuration") or 0) / 1000))
    info.update(by_thread=by_thread, cost_usd=cost_total, cost_by_thread=cost,
                models=sorted({m for rows in by_thread.values() for m in rows}),
                transcript=str(files[0]) if files else None, side=sessions.side_of(project, info["cwd"])
                if info["cwd"] else None)
    return info


def total(rows: dict[str, dict]) -> int:
    """All tokens in {model: counts}."""
    return sum(r.get(k, 0) for r in rows.values() for k in KINDS)


# --- keeping the records current ---------------------------------------------------------

def _stamp(files: list[Path]) -> list:
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append([str(f), st.st_size, int(st.st_mtime)])
        except OSError:
            pass
    return out


def refresh(project: Project, only: str | None = None) -> int:
    """Re-read transcripts that changed since last time. Returns how many."""
    d = usage_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    rec = sessions.get(project, only) if only else None
    if only and rec and rec.get("transcript") and Path(rec["transcript"]).is_file():
        main = Path(rec["transcript"])  # just this session: no folder scan
        found = {only: [main] + sorted((main.parent / only / "subagents").glob("agent-*.jsonl"))}
    else:
        found = transcripts(project)
        if only:
            found = {k: v for k, v in found.items() if k == only}
    n = 0
    for sid, files in found.items():
        path = d / f"{sid}.json"
        old = store.read_json(path) or {}
        stamp = _stamp(files)
        rec = sessions.get(project, sid)
        if (old.get("v") == VERSION and old.get("stamp") == stamp
                and old.get("registered") == bool(rec) and old.get("events") == _events_mark(project)):
            continue
        info = read_session(project, sid, files)
        info.update(v=VERSION, stamp=stamp, events=_events_mark(project), read=store.now())
        store.write_json(path, info)
        n += 1
    return n


def _events_mark(project: Project) -> int:
    try:
        return (project.state_dir / "events.jsonl").stat().st_size
    except OSError:
        return 0


def records(project: Project) -> list[dict]:
    d = usage_dir(project)
    if not d.is_dir():
        return []
    out = [store.read_json(p) for p in d.glob("*.json")]
    return sorted((r for r in out if r), key=lambda r: r.get("first") or "")


# --- views ------------------------------------------------------------------------------

def short_model(model: str) -> str:
    """claude-opus-5-5 -> opus 5.5; claude-haiku-4-5-20251001 -> haiku 4.5."""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", model)
    if not m:
        return model.removeprefix("claude-")
    return f"{m.group(1)} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")


def for_thread(project: Project, tid: str, recs: list[dict] | None = None) -> dict:
    """{models: {model: counts + total + pct}, total, cost, sessions: [...]}."""
    tid = tid.upper() if tid else ""
    recs = records(project) if recs is None else recs
    models: dict[str, dict] = {}
    out_sessions = []
    cost = 0.0
    have_cost = False
    for r in recs:
        rows = (r.get("by_thread") or {}).get(tid)
        if not rows:
            continue
        for model, c in rows.items():
            _add(models.setdefault(model, _empty()), c)
        c_usd = (r.get("cost_by_thread") or {}).get(tid)
        if c_usd:
            have_cost = True
            cost += sum(c_usd.values())
        out_sessions.append({
            "session": r["short"], "id": r["id"], "registered": r.get("registered"),
            "title": r.get("title") or (r.get("first_prompt") or "")[:60],
            "first": r.get("first"), "last": r.get("last"), "prompts": r.get("prompts"),
            "subagents": r.get("subagents"), "total": total(rows),
            "models": {m: sum(c.get(k, 0) for k in KINDS) for m, c in rows.items()},
            "cost": sum(c_usd.values()) if c_usd else None,
            "tools": sum((r.get("tools") or {}).values()),
            "also": sorted(k or "none" for k in r.get("by_thread", {}) if k != tid),
        })
    grand = total(models)
    for c in models.values():
        c["total"] = sum(c[k] for k in KINDS)
        c["pct"] = round(100 * c["total"] / grand, 1) if grand else 0.0
    kinds = {k: sum(c[k] for c in models.values()) for k in KINDS}
    return {"thread": tid, "total": grand, "kinds": kinds,
            "models": dict(sorted(models.items(), key=lambda kv: -kv[1]["total"])),
            "cost": round(cost, 4) if have_cost else None,
            "sessions": sorted(out_sessions, key=lambda s: s["first"] or "", reverse=True)}


def totals(project: Project, recs: list[dict] | None = None) -> dict[str, int]:
    """thread -> all its tokens ("" = no thread)."""
    recs = records(project) if recs is None else recs
    out: dict[str, int] = {}
    for r in recs:
        for tid, rows in (r.get("by_thread") or {}).items():
            out[tid] = out.get(tid, 0) + total(rows)
    return out


def tokens(n: int | float) -> str:
    for unit, size in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(int(n))


def mix(view: dict, top: int = 3) -> str:
    """'opus 5.5 92%, haiku 5.5 8%'."""
    return ", ".join(f"{short_model(m)} {c['pct']:.0f}%" for m, c in list(view["models"].items())[:top])


def text(project: Project, tid: str) -> str:
    v = for_thread(project, tid)
    lines = [f"Claude usage of {tid or 'no thread'}: {tokens(v['total'])} tokens"
             + (f", about ${v['cost']:.2f}" if v["cost"] is not None else "")]
    if not v["models"]:
        return lines[0] + "\n  (no Claude messages recorded)"
    lines.append(f"  {'model':<22}{'input':>9}{'cache wr':>10}{'cache rd':>10}{'output':>9}"
                 f"{'total':>10}{'share':>8}")
    for m, c in v["models"].items():
        lines.append(f"  {m:<22}{tokens(c['input']):>9}{tokens(c['cache_write']):>10}"
                     f"{tokens(c['cache_read']):>10}{tokens(c['output']):>9}{tokens(c['total']):>10}"
                     f"{c['pct']:>7.1f}%")
    lines += ["", "Sessions:"]
    for s in v["sessions"]:
        lines.append(f"  {s['session']}{'' if s['registered'] else ' (unregistered)'}  "
                     f"{utc(s['first'])}  {tokens(s['total']):>7}  "
                     + ", ".join(f"{short_model(m)} {tokens(n)}" for m, n in s["models"].items())
                     + f"  {s['title'] or ''}")
    return "\n".join(lines)


def utc(text: str | None) -> str:
    """A transcript time (UTC) in local time, to the minute."""
    t = _t(text)
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else ""

