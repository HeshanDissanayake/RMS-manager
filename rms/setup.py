"""Putting RMS on a project: hooks in .claude/settings*.json, and a setup check.

See docs/adopting.md for the steps and docs/SPEC.md for the why.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from rms import hooks, store, threads
from rms.config import CONFIG_NAME, DEFAULTS, load

EVENTS = {  # Claude Code event -> rms hook name
    "SessionStart": "session-start",
    "PreToolUse": "pre-tool-use",
    "UserPromptSubmit": "user-prompt-submit",
    "SessionEnd": "session-end",
}


# Hooks live only in the project's own folder (owner's decision 2026-11-11):
# never in ~/.claude/settings.json. Claude Code reads them only when `claude`
# is started in the project root (docs/SPEC.md §6).
SCOPES = ("project", "local")


def settings_path(root: Path, scope: str = "project") -> Path:
    """project: <root>/.claude/settings.json (in git, shared)
    local:   <root>/.claude/settings.local.json (personal, not in git)
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (project or local)")
    return root / ".claude" / ("settings.local.json" if scope == "local" else "settings.json")


def _is_rms_entry(entry: dict) -> bool:
    return any(" hook " in h.get("command", "") and "rms" in h.get("command", "")
               for h in entry.get("hooks", []))


def merge_hooks(settings: dict, rms_cmd: str) -> dict:
    """Add RMS's hooks to a settings dict; keep every other hook and setting.

    Earlier RMS entries are replaced (so running it again is safe).
    """
    out = dict(settings)
    all_hooks = {k: list(v) for k, v in (settings.get("hooks") or {}).items()}
    for event, entries in hooks.settings_for(rms_cmd)["hooks"].items():
        kept = [e for e in all_hooks.get(event, []) if not _is_rms_entry(e)]
        all_hooks[event] = kept + entries
    out["hooks"] = all_hooks
    return out


def install_hooks(root: Path, rms_cmd: str, scope: str = "project") -> Path:
    path = settings_path(root, scope)
    cur = store.read_json(path, None) if path.exists() else {}
    if cur is None:
        raise ValueError(f"{path} is not valid JSON; fix it first")
    store.write_json(path, merge_hooks(cur, rms_cmd))
    return path


# --- the Claude Code mod (rms-thread), per project ------------------------------

MOD = "rms-thread@rms-manager"


def rms_home() -> Path:
    """This RMS checkout (the marketplace that lists the mod)."""
    return Path(__file__).resolve().parent.parent


def install_mod(root: Path, scope: str = "project") -> list[str]:
    """Turn the rms-thread mod on for one project (its .claude/settings*.json).

    Uses Claude Code's own commands, run in the project folder: add this repo
    as a marketplace and install the mod, both at project (or local) scope, so
    `claude` started there loads it and other folders do not.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} (project or local)")
    out = []
    for argv in (["claude", "plugin", "marketplace", "add", str(rms_home()), "--scope", scope],
                 ["claude", "plugin", "install", MOD, "--scope", scope]):
        r = subprocess.run(argv, cwd=root, capture_output=True, text=True)
        text = (r.stdout + r.stderr).strip().splitlines()
        out.append(text[-1] if text else "")
        if r.returncode != 0:
            raise ValueError(f"{' '.join(argv[:4])}: {out[-1]}")
    return out


def mod_enabled(root: Path) -> bool:
    for scope in SCOPES:
        data = store.read_json(settings_path(root, scope), {}) or {}
        if (data.get("enabledPlugins") or {}).get(MOD):
            return True
    return False


# --- the check ----------------------------------------------------------------

def _hook_commands(root: Path) -> dict[str, list[tuple[Path, str]]]:
    """For each event, the RMS hook commands found (file, command)."""
    found: dict[str, list[tuple[Path, str]]] = {e: [] for e in EVENTS}
    for scope in SCOPES:
        path = settings_path(root, scope)
        data = store.read_json(path, {}) if path.exists() else {}
        for event in EVENTS:
            for entry in (data.get("hooks") or {}).get(event, []):
                for h in entry.get("hooks", []):
                    cmd = h.get("command", "")
                    if "rms" in cmd and f"hook {EVENTS[event]}" in cmd:
                        found[event].append((path, cmd))
    return found


def _executable(cmd: str, root: Path) -> Path | None:
    """The program a hook command runs, with $CLAUDE_PROJECT_DIR filled in."""
    expanded = cmd.replace("$CLAUDE_PROJECT_DIR", str(root)).replace("${CLAUDE_PROJECT_DIR}", str(root))
    try:
        first = shlex.split(expanded)[0]
    except (ValueError, IndexError):
        return None
    return Path(os.path.expanduser(first))


def doctor(root: Path) -> list[tuple[str, str]]:
    """[(level, message)], level in ok / note / warn / fail."""
    root = Path(root).resolve()
    out: list[tuple[str, str]] = []
    ok = lambda m: out.append(("ok", m))  # noqa: E731
    warn = lambda m: out.append(("warn", m))  # noqa: E731
    fail = lambda m: out.append(("fail", m))  # noqa: E731

    if not (root / CONFIG_NAME).is_file():
        fail(f"no {CONFIG_NAME} in {root} (rms adopt {root})")
        return out
    try:
        p = load(root)
    except (ValueError, json.JSONDecodeError) as e:
        fail(f"{CONFIG_NAME}: {e}")
        return out
    ok(f"{CONFIG_NAME}: project \"{p.name}\"")
    missing = set(DEFAULTS) - set(p.cfg)
    if missing:
        warn(f"{CONFIG_NAME} leaves {sorted(missing)} at their defaults")

    if p.threads_dir.is_dir():
        ts = threads.discover(p)
        (ok if ts else warn)(f"threads: {len(ts)} found in {p.rel(p.threads_dir)}/"
                             + ("" if ts else " (folders are named T<NN>-<name>)"))
    else:
        fail(f"threads folder {p.rel(p.threads_dir)}/ does not exist")
    (ok if p.index_file.is_file() else warn)(
        f"index file {p.rel(p.index_file)}" + ("" if p.index_file.is_file()
                                               else " missing (thread names/status come from it)"))
    if not p.side_dir.is_dir():
        warn(f"side folder {p.rel(p.side_dir)}/ does not exist (only needed for side sessions)")

    try:
        p.state_dir.mkdir(parents=True, exist_ok=True)
        probe = p.state_dir / ".probe"
        probe.write_text("x")
        probe.unlink()
        ok(f"state folder {p.rel(p.state_dir)}/ is writable")
    except OSError as e:
        fail(f"state folder {p.rel(p.state_dir)}/ not writable: {e}")
    r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(p.state_dir)],
                       capture_output=True)
    if r.returncode == 1:
        warn(f"{p.rel(p.state_dir)}/ is not ignored by git: add it to .gitignore")
    elif r.returncode == 0:
        ok(f"{p.rel(p.state_dir)}/ is ignored by git")

    found = _hook_commands(root)
    for event, cmds in found.items():
        if not cmds:
            fail(f"hook {event} missing (rms install-hooks {root})")
            continue
        if len(cmds) > 1:
            warn(f"hook {event} configured {len(cmds)} times (runs more than once)")
        for path, cmd in cmds:
            exe = _executable(cmd, root)
            if exe is None or not exe.is_file() or not os.access(exe, os.X_OK):
                fail(f"hook {event} in {path}: program {exe} not found or not executable")
            else:
                ok(f"hook {event} in {p.rel(path)} -> {exe.resolve()}")
    if not mod_enabled(root):
        out.append(("note", f"the rms-thread mod (thread picker, thread band) is not on for "
                            f"this project: rms install-mod {root}"))
    if all(found.values()):
        out.append(("note", f"start `claude` in {root}: sessions started in a subfolder "
                            "do not read these hooks and are not seen by RMS"))
    return out
