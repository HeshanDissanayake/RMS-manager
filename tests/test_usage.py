"""Claude usage per thread: tokens by model, read from (fake) Claude Code transcripts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from rms import cli, lifecycle, sessions, usage
from rms.git import git
from tests.sandbox_case import SandboxCase


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _msg(mid, t, model, cwd, inp=10, cw=100, cr=1000, out=50, tool=None):
    content = [{"type": "tool_use", "name": tool, "input": {}}] if tool else [{"type": "text", "text": "x"}]
    return {"type": "assistant", "timestamp": _iso(t), "cwd": cwd, "version": "2.1.322",
            "message": {"id": mid, "model": model, "content": content,
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr, "output_tokens": out}}}


class Usage(SandboxCase):
    def setUp(self):
        self.claude = self.dir / "claude-projects"
        os.environ["RMS_CLAUDE_PROJECTS"] = str(self.claude)

    def write(self, cwd, sid, lines, sub=None):
        d = self.claude / usage.folder_name(cwd)
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("".join(json.dumps(x) + "\n" for x in lines))
        if sub:
            s = d / sid / "subagents"
            s.mkdir(parents=True, exist_ok=True)
            (s / "agent-1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sub))
        return f

    def test_tokens_per_thread_and_model(self):
        p, root = self.p, str(self.root)
        sid = "bbbb1111-0000-0000-0000-000000000000"
        self.session(sid)
        t0 = datetime.now(timezone.utc) - timedelta(seconds=30)
        before = [{"type": "user", "timestamp": _iso(t0), "cwd": root,
                   "message": {"role": "user", "content": "hello"}},
                  _msg("m1", t0 + timedelta(seconds=1), "claude-opus-5-5", root)]
        self.write(root, sid, before)
        rec = sessions.get(p, sid)
        rec["transcript"] = str(self.claude / usage.folder_name(root) / f"{sid}.jsonl")
        sessions.save(p, rec)
        rc, out = self.rms(sid, "claim", "T01")
        self.assertEqual(rc, 0, out)
        t1 = datetime.now(timezone.utc) + timedelta(seconds=1)
        after = before + [
            _msg("m2", t1, "claude-opus-5-5", root, tool="Bash"),
            _msg("m2", t1, "claude-opus-5-5", root, tool="Bash"),  # same message twice: once
            {"type": "ai-title", "aiTitle": "Sort work"},
            {"type": "cost-state", "totalCostUSD": 1.0, "modelUsage": {
                "claude-opus-5-5": {"inputTokens": 20 + 5, "outputTokens": 100 + 10,
                                    "cacheReadInputTokens": 2000 + 1000, "cacheCreationInputTokens": 200 + 100,
                                    "costUSD": 0.9},
                "claude-haiku-5-5": {"inputTokens": 500, "outputTokens": 20, "costUSD": 0.1}}},
        ]
        sub = [_msg("s1", t1 + timedelta(seconds=1), "claude-opus-5-5", root, 5, 100, 1000, 10)]
        self.write(root, sid, after, sub=sub)
        # a session RMS never saw, working in T02's checkout
        wt2 = lifecycle.ensure_worktree(p, "T02")
        self.write(str(wt2), "cccc2222-0000-0000-0000-000000000000",
                   [_msg("r1", t1, "claude-sonnet-5-5", str(wt2), 1, 2, 3, 4)])

        self.assertEqual(usage.refresh(p), 2)
        self.assertEqual(usage.refresh(p), 0)  # nothing changed: nothing re-read
        v = usage.for_thread(p, "T01")
        opus, haiku = v["models"]["claude-opus-5-5"], v["models"]["claude-haiku-5-5"]
        self.assertEqual(opus["messages"], 2)          # m2 + the subagent's s1
        self.assertEqual(opus["cache_read"], 2000)
        self.assertEqual(haiku["input"], 500)           # background, from the cost record
        self.assertEqual(v["total"], opus["total"] + haiku["total"])
        self.assertAlmostEqual(sum(c["pct"] for c in v["models"].values()), 100, delta=0.2)
        s = v["sessions"][0]
        self.assertEqual((s["title"], s["prompts"], s["tools"]), ("Sort work", 1, 1))
        none = usage.for_thread(p, "")
        self.assertEqual(none["models"]["claude-opus-5-5"]["messages"], 1)  # m1, before the claim
        self.assertAlmostEqual(v["cost"] + none["cost"], 1.0, places=3)
        t2 = usage.for_thread(p, "T02")
        self.assertEqual(t2["total"], 1 + 4)  # input + output; cache counts kept aside
        self.assertEqual(t2["models"]["claude-sonnet-5-5"]["cache_read"], 3)
        self.assertFalse(t2["sessions"][0]["registered"])
        self.assertEqual(usage.short_model("claude-opus-5-5"), "opus 5.5")
        self.assertEqual(usage.short_model("claude-haiku-4-5-20251001"), "haiku 4.5")
        # the band's data
        info = cli.whoami_json(p, sessions.get(p, sid))
        self.assertEqual(info["usage"]["total"], v["total"])
        self.assertEqual(info["usage"]["models"][0]["model"], "haiku 5.5")  # most in + out
        rc, out = self.rms(None, "usage")
        self.assertIn("T01", out)
        rc, out = self.rms(None, "usage", "T01")
        self.assertIn("claude-haiku-5-5", out)

        # a session from before RMS, in the root, that edited T03's notes: guessed
        f = self.root / "threads"
        t03 = next(f.glob("T03-*"))
        self.write(root, "dddd3333-0000-0000-0000-000000000000", [
            {"type": "user", "timestamp": _iso(t0), "cwd": root,
             "message": {"role": "user", "content": "carry on"}},
            _msg("g1", t0, "claude-opus-5-5", root, 1, 0, 0, 1),
            {**_msg("g2", t0 + timedelta(seconds=2), "claude-opus-5-5", root, 1, 0, 0, 1),
             "message": {**_msg("g2", t0, "claude-opus-5-5", root, 1, 0, 0, 1)["message"],
                         "content": [{"type": "tool_use", "id": "tu1", "name": "Edit",
                                      "input": {"file_path": str(t03 / "THREAD.md")}}]}},
        ])
        usage.refresh(p)
        g = usage.for_thread(p, "T03")
        self.assertEqual(g["total"], 4)  # both messages: the first takes the first guess
        self.assertTrue(g["sessions"][0]["guessed"])

        # the handover commits the thread's Claude record
        self.assertEqual(self.rms(sid, "handover")[0], 0)
        self.note("T01", "Sorted. Next: measure.")
        rc, out = self.rms(sid, "handover", "-m", "done for now")
        self.assertEqual(rc, 0, out)
        rel = f"threads/{next(f.glob('T01-*')).name}/{usage.RECORD_FILE}"
        rec = json.loads(git(self.root, "show", f"main:{rel}"))
        self.assertEqual(rec["total"], v["total"])
        self.assertEqual(rec["sessions"][0]["session"], sessions.short_id(sid))
