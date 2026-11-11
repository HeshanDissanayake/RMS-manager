"""A fresh sandbox root (sandbox/reset-demo.sh) per test class, plus helpers."""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from rms import cli, index, lifecycle, sessions
from rms.config import load

HERE = Path(__file__).resolve().parent.parent
TMP = HERE / ".tmp"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}


class SandboxCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        TMP.mkdir(exist_ok=True)
        cls.dir = Path(tempfile.mkdtemp(dir=TMP))
        cls._env = dict(os.environ)
        os.environ.update(GIT_ENV)
        os.environ.pop("RMS_PROJECT", None)
        subprocess.run([str(HERE / "sandbox/reset-demo.sh")], check=True, capture_output=True,
                       env={**os.environ, "RMS_DEMO_DIR": str(cls.dir / "demo")})
        cls.root = cls.dir / "demo" / "root"
        cls.p = load(cls.root)
        cls.procs = []

    @classmethod
    def tearDownClass(cls) -> None:
        for pr in cls.procs:
            pr.kill()
            pr.wait()
        os.environ.clear()
        os.environ.update(cls._env)
        shutil.rmtree(cls.dir)

    # helpers
    def session(self, sid: str) -> None:
        pr = subprocess.Popen(["sleep", "600"])
        self.procs.append(pr)
        sessions.register(self.p, sid, str(self.root), pr.pid)

    def rms(self, sid: str | None, *args: str) -> tuple[int, str]:
        old = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        if sid:
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(list(args))
        finally:
            os.chdir(cwd)
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            if old:
                os.environ["CLAUDE_CODE_SESSION_ID"] = old
        return rc, out.getvalue() + err.getvalue()

    def note(self, tid: str, state: str) -> None:
        f = lifecycle.thread_dir(self.p, tid) / "THREAD.md"
        text = f.read_text()
        text = re.sub(r"## State as of .*?(?=\n## )", f"## State as of {lifecycle.today()}\n\n{state}\n",
                      text, flags=re.S)
        f.write_text(text)

    def index_row(self, tid: str) -> dict:
        return index.get(self.p.index_file.read_text(), tid)

