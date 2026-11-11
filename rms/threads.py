"""Threads: one folder per thread (T<NN>-<name>), described in the index file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rms.config import Project

THREAD_DIR_RE = re.compile(r"^(T\d+)(?:-(.+))?$")
THREAD_ID_RE = re.compile(r"\b(T\d+)\b")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


@dataclass
class Thread:
    id: str
    name: str
    status: str
    since: str
    folder: Path | None

    @property
    def done(self) -> bool:
        return self.status.lower().startswith("done")

    @property
    def thread_file(self) -> Path | None:
        return self.folder / "THREAD.md" if self.folder else None


def _plain(cell: str) -> str:
    return LINK_RE.sub(r"\1", cell).replace("**", "").strip()


def parse_index(text: str) -> list[dict]:
    """Rows of every Markdown table whose first header cell is 'ID'."""
    rows: list[dict] = []
    header: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if header[0] != "id":
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def _sort_key(tid: str) -> int:
    return int(tid[1:])


def discover(project: Project) -> dict[str, Thread]:
    """All threads: folders under threads_dir, details from the index file."""
    found: dict[str, Thread] = {}
    if project.threads_dir.is_dir():
        for d in project.threads_dir.iterdir():
            m = THREAD_DIR_RE.match(d.name)
            if d.is_dir() and m:
                name = (m.group(2) or "").replace("-", " ")
                found[m.group(1)] = Thread(m.group(1), name, "unknown", "", d)
    if project.index_file.is_file():
        for row in parse_index(project.index_file.read_text(encoding="utf-8")):
            m = THREAD_ID_RE.search(row.get("id", ""))
            if not m:
                continue
            tid = m.group(1)
            t = found.get(tid) or Thread(tid, "", "unknown", "", None)
            t.name = _plain(row.get("thread", "")) or t.name
            t.status = _plain(row.get("status", "")) or t.status
            t.since = _plain(row.get("since", "") or row.get("closed", "")) or t.since
            found[tid] = t
    return {k: found[k] for k in sorted(found, key=_sort_key)}


def section(thread: Thread, title_prefix: str) -> str | None:
    """Body of the first '## <title_prefix>...' section of THREAD.md."""
    f = thread.thread_file
    if not f or not f.is_file():
        return None
    return section_text(f.read_text(encoding="utf-8"), title_prefix)


def section_text(text: str, title_prefix: str) -> str | None:
    """The first '## <title_prefix>...' section (heading included) of a Markdown text."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].lower().startswith(title_prefix.lower())
            if inside:
                out.append(line)
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip() or None


def summary(thread: Thread, file: Path | None = None) -> str:
    """Header line + State + How to resume: what a session needs to start."""
    parts = [f"{thread.id}: {thread.name}  [{thread.status}]"]
    f = file or thread.thread_file
    text = f.read_text(encoding="utf-8") if f and f.is_file() else ""
    for title in ("State", "How to resume"):
        body = section_text(text, title)
        if body:
            parts.append(body)
    if len(parts) == 1:
        parts.append("(no THREAD.md with State / How to resume found)")
    return "\n\n".join(parts)
