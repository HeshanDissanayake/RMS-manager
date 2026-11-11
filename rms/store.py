"""Plain-file state: atomic JSON writes, one project-wide lock, the event log.

All RMS state lives in the project's state folder (``store/rms`` by default)
as small JSON files, so it works without any server and can be inspected or
repaired by hand.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

_local = threading.local()


def now() -> str:
    """Local time with offset, to the second (ISO 8601)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Write atomically: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def locked(state_dir: Path) -> Iterator[None]:
    """Exclusive project-wide lock for every change of state.

    Re-entrant within one process (nested calls do not lock again), so a
    function that locks can call another that locks.
    """
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        _local.depth = 1
        try:
            yield
        finally:
            _local.depth = 0
            fcntl.flock(f, fcntl.LOCK_UN)


def append_event(state_dir: Path, event: str, **fields: Any) -> dict:
    rec = {"t": now(), "event": event}
    rec.update({k: v for k, v in fields.items() if v is not None})
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def read_events(state_dir: Path, limit: int | None = None) -> list[dict]:
    try:
        with open(state_dir / "events.jsonl", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    if limit is not None:
        lines = lines[-limit:]
    out = []
    for line in lines:
        with contextlib.suppress(json.JSONDecodeError):
            out.append(json.loads(line))
    return out
