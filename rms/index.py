"""Editing the thread index (INDEX.md): rows in Markdown tables, by thread ID.

The index has an "Open threads" table and a "Done threads" table (found by the
heading above each). Columns are matched by their header name, case-blind, so
a project may have more or fewer columns; unknown ones are left empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ID_RE = re.compile(r"\b(T\d+)\b")


@dataclass
class Table:
    heading: str          # the '## ...' heading above it (lower case)
    header: list[str]     # column names (lower case)
    header_line: int
    rows: list[int]       # line numbers of data rows


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _line(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def tables(lines: list[str]) -> list[Table]:
    out: list[Table] = []
    heading = ""
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("## "):
            heading = s[3:].strip().lower()
        if s.startswith("|"):
            header = [c.lower() for c in _cells(s)]
            t = Table(heading, header, i, [])
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not all(set(c) <= set("-: ") for c in _cells(lines[i])):
                    t.rows.append(i)
                i += 1
            if t.header and t.header[0] == "id":
                out.append(t)
            continue
        i += 1
    return out


def _find(lines: list[str], tid: str) -> tuple[Table, int] | None:
    for t in tables(lines):
        for r in t.rows:
            m = ID_RE.search(_cells(lines[r])[0])
            if m and m.group(1) == tid:
                return t, r
    return None


def _table(lines: list[str], heading_word: str) -> Table:
    for t in tables(lines):
        if heading_word in t.heading:
            return t
    raise ValueError(f"no table under a heading containing {heading_word!r} in the index")


def get(text: str, tid: str) -> dict | None:
    lines = text.splitlines()
    hit = _find(lines, tid)
    if not hit:
        return None
    t, r = hit
    return dict(zip(t.header, _cells(lines[r])))


def set_cells(text: str, tid: str, values: dict[str, str]) -> str:
    lines = text.splitlines()
    hit = _find(lines, tid)
    if not hit:
        raise ValueError(f"{tid} not in the index")
    t, r = hit
    cells = _cells(lines[r])
    cells += [""] * (len(t.header) - len(cells))
    for k, v in values.items():
        if k.lower() in t.header:
            cells[t.header.index(k.lower())] = v
    lines[r] = _line(cells)
    return "\n".join(lines) + "\n"


def add_row(text: str, heading_word: str, values: dict[str, str]) -> str:
    lines = text.splitlines()
    t = _table(lines, heading_word)
    cells = [values.get(h, "") for h in t.header]
    at = (t.rows[-1] if t.rows else t.header_line + 1) + 1
    lines.insert(at, _line(cells))
    return "\n".join(lines) + "\n"


def move_row(text: str, tid: str, heading_word: str, values: dict[str, str]) -> str:
    """Move a thread's row to the table under heading_word, keeping same-named cells."""
    lines = text.splitlines()
    hit = _find(lines, tid)
    if not hit:
        raise ValueError(f"{tid} not in the index")
    t, r = hit
    old = dict(zip(t.header, _cells(lines[r])))
    if heading_word in t.heading:
        return set_cells(text, tid, values)
    del lines[r]
    merged = {**old, **{k.lower(): v for k, v in values.items()}}
    return add_row("\n".join(lines) + "\n", heading_word, merged)
