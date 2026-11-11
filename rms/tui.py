"""The terminal UI: threads, sessions, warnings and events, live; and a Thread tab.

Run: ``rms tui`` (from inside the project, or with --project).
Overview tab: refreshed every second. Thread tab (t on a thread, or pick it
there): its sources and their state, builds, experiments, disk use and the
processes running for it now, and its Claude use: tokens per model with
each model's share, and the sessions (disk use and tokens are computed in the
background; r recomputes; token counts also refresh every 10 s).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual import work
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Header, Label, RichLog, Select, Static,
                             TabbedContent, TabPane)

from rms import claims, inventory, sessions, store, threadinfo, threads, usage, warns

H = inventory.human
from rms.config import Project

STATE_STYLE = {"free": "dim", "live": "bold green", "stale": "bold yellow",
               "gone": "yellow", "ended": "dim"}
EVENT_STYLE = {"claim": "green", "claim-refused": "red", "take-over": "yellow", "release": "cyan",
               "denied": "red", "handover": "cyan", "handover-due": "yellow", "close": "magenta", "close-approved": "magenta", "close-refused": "red", "warning": "yellow", "warning-resolved": "green", "warning-dismissed": "dim", "create": "green", "hook-error": "bold red", "session-start": "blue",
               "session-end": "dim"}


def _time(iso: str | None) -> str:
    return iso[11:19] if iso else ""


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "yes", "Yes"), Binding("n,escape", "no", "No")]

    def __init__(self, question: str, yes_label: str = "Release (y)") -> None:
        super().__init__()
        self.question = question
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button(self.yes_label, variant="warning", id="yes")
                yield Button("Cancel (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class TextScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,enter", "close", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="textbox"):
            yield Label(self.title_text, classes="pane-title")
            with VerticalScroll():
                yield Static(self.body, markup=False)
            yield Label("esc to close", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


THREAD_SECTIONS = [("sources", "Sources (repos in the thread's checkout)"),
                   ("builds", "Builds (made by the thread, and used)"),
                   ("experiments", "Experiments"),
                   ("disk", "Disk use"),
                   ("claude", "Claude tokens by model"),
                   ("claude-sessions", "Claude sessions on this thread"),
                   ("processes", "Running now (refreshed every second)")]
THREAD_COLUMNS = {
    "sources": ("Repo", "Branch", "Commit", "State", "Size"),
    "builds": ("", "Build", "Path", "Source", "Cost", "Size"),
    "experiments": ("ID", "Status", "Title", "Summary", "Record", "Data"),
    "disk": ("What", "Size"),
    "claude": ("Model", "Share", "Total", "Input", "Cache write", "Cache read", "Output", "Messages"),
    "claude-sessions": ("Session", "Started", "Last", "Prompts", "Tokens", "Models", "Cost", "Title"),
    "processes": ("PID", "Running", "Why", "Folder", "Command"),
}


class RMSApp(App):
    TITLE = "RMS"
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #overview-body { height: 1fr; }
    #top { height: 1fr; }
    #thread-bar { height: 3; }
    #thread-pick { width: 52; }
    #thread-head { padding: 1 2; color: $text-muted; }
    #thread-body DataTable { height: auto; max-height: 14; }
    #thread-body .pane { height: auto; }
    #threads-pane { width: 3fr; }
    #sessions-pane { width: 2fr; }
    #events-pane { height: 10; }
    #warnings-pane { height: 9; }
    .pane { border: round $primary-darken-2; padding: 0 1; }
    .pane:focus-within { border: round $accent; }
    .pane-title { text-style: bold; color: $text-muted; }
    DataTable { height: 1fr; }
    RichLog { height: 1fr; }
    #summary { height: 1; padding: 0 1; color: $text-muted; }
    ConfirmScreen, TextScreen { align: center middle; }
    #dialog { width: 64; height: auto; border: thick $warning; background: $surface; padding: 1 2; }
    #question { width: 100%; margin-bottom: 1; }
    #buttons { height: auto; align: right middle; }
    #buttons Button { margin-left: 2; }
    #textbox { width: 90%; height: 80%; border: thick $primary; background: $surface; padding: 1 2; }
    .hint { color: $text-muted; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("x", "release", "Release claim"),
        Binding("c", "approve_close", "Approve close"),
        Binding("d", "settle('dismissed')", "Dismiss warning", show=False),
        Binding("v", "settle('resolved')", "Warning resolved", show=False),
        Binding("enter", "details", "Details", show=True),
        Binding("e", "toggle_ended", "Show ended"),
        Binding("t", "open_thread", "Thread tab"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.show_ended = False
        self.events_seen = 0
        self.thread_ids: list[str] = []
        self.session_ids: list[str] = []

    # layout
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="summary")
        with TabbedContent(initial="overview"):
            with TabPane("Overview", id="overview"):
                with Vertical(id="overview-body"):
                    with Horizontal(id="top"):
                        with Vertical(id="threads-pane", classes="pane"):
                            yield Label("Threads (t: open in the Thread tab)", classes="pane-title")
                            yield DataTable(id="threads", cursor_type="row", zebra_stripes=True)
                        with Vertical(id="sessions-pane", classes="pane"):
                            yield Label("Sessions", classes="pane-title")
                            yield DataTable(id="sessions", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="warnings-pane", classes="pane"):
                        yield Label("Warnings (for you: d dismiss, v resolved)", classes="pane-title")
                        yield DataTable(id="warnings", cursor_type="row", zebra_stripes=True)
                    with Vertical(id="events-pane", classes="pane"):
                        yield Label("Events", classes="pane-title")
                        yield RichLog(id="events", markup=False, wrap=False)
            with TabPane("Thread", id="thread-tab"):
                with Horizontal(id="thread-bar"):
                    yield Select([], id="thread-pick", prompt="Pick a thread", allow_blank=True)
                    yield Static("", id="thread-head")
                with VerticalScroll(id="thread-body"):
                    for key, title in THREAD_SECTIONS:
                        with Vertical(classes="pane"):
                            yield Label(title, classes="pane-title")
                            yield DataTable(id=f"t-{key}", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"{self.project.name} · {self.project.root}"
        self.query_one("#threads", DataTable).add_columns(
            "ID", "Thread", "Status", "Claim", "Held by", "Since", "Tokens")
        self.query_one("#sessions", DataTable).add_columns(
            "Session", "State", "Thread", "Where", "Started", "Last seen")
        self.query_one("#warnings", DataTable).add_columns("ID", "Thread", "Since", "Warning")
        self.warning_ids: list[str] = []
        for key, cols in THREAD_COLUMNS.items():
            self.query_one(f"#t-{key}", DataTable).add_columns(*cols)
        self.thread_tid: str | None = None
        self.token_totals: dict[str, int] = usage.totals(self.project)
        self.update_usage()
        self.set_interval(10.0, self.update_usage)
        self.query_one("#thread-pick", Select).set_options(
            [(f"{t.id}  {t.name}", t.id) for t in threads.discover(self.project).values()])
        self.refresh_all()
        self.set_interval(1.0, self.refresh_all)
        self.query_one("#threads", DataTable).focus()

    # data
    def refresh_all(self) -> None:
        p = self.project
        all_threads = threads.discover(p)
        cl = claims.all_claims(p)
        recs = sessions.all_sessions(p)

        tt = self.query_one("#threads", DataTable)
        row = tt.cursor_row
        tt.clear()
        self.thread_ids = []
        for t in all_threads.values():
            c = cl.get(t.id)
            st, _ = claims.state(p, c)
            status_style = "dim" if t.done else ""
            tt.add_row(
                Text(t.id, style="bold"),
                Text(t.name, style=status_style),
                Text(t.status, style=status_style),
                Text(st + (" · handover due" if c and c.get("handover_due") else ""),
                     style=STATE_STYLE[st]),
                c["short"] if c else "",
                _time(c["since"]) if c else "",
                usage.tokens(self.token_totals[t.id]) if self.token_totals.get(t.id) else "",
            )
            self.thread_ids.append(t.id)
        if self.thread_ids:
            tt.move_cursor(row=min(row, len(self.thread_ids) - 1), animate=False)

        stt = self.query_one("#sessions", DataTable)
        row = stt.cursor_row
        stt.clear()
        self.session_ids = []
        live = 0
        for r in reversed(recs):
            st = sessions.status(r)
            live += st == "live"
            if st == "ended" and not self.show_ended:
                continue
            where = f"side/{r['side']}" if r.get("side") else p.rel(r["cwd"])
            stt.add_row(Text(r["short"], style="bold"), Text(st, style=STATE_STYLE[st]),
                        r.get("thread") or "", where, _time(r.get("started")),
                        _time(r.get("last_seen")))
            self.session_ids.append(r["id"])
        if self.session_ids:
            stt.move_cursor(row=min(row, len(self.session_ids) - 1), animate=False)

        wt = self.query_one("#warnings", DataTable)
        row = wt.cursor_row
        wt.clear()
        self.warning_ids = []
        for w in warns.open_rows(p):
            wt.add_row(Text(w["id"], style="bold yellow"), w.get("thread") or "",
                       _time(w.get("first_seen")), w["text"])
            self.warning_ids.append(w["id"])
        if self.warning_ids:
            wt.move_cursor(row=min(row, len(self.warning_ids) - 1), animate=False)

        stale = sum(claims.state(p, c)[0] == "stale" for c in cl.values())
        summary = f"{len(all_threads)} threads · {len(cl)} claimed · {live} live sessions"
        if self.token_totals:
            summary += f" · {usage.tokens(sum(self.token_totals.values()))} Claude tokens"
        if stale:
            summary += f" · {stale} stale claim{'s' if stale > 1 else ''} (select, x to release)"
        if self.warning_ids:
            summary += f" · {len(self.warning_ids)} open warning{'s' if len(self.warning_ids) > 1 else ''}"
        self.query_one("#summary", Static).update(summary)

        if self.thread_tid and self.query_one(TabbedContent).active == "thread-tab":
            self.fill_processes(threadinfo.processes(p, self.thread_tid))

        log = self.query_one("#events", RichLog)
        events = store.read_events(p.state_dir)
        if len(events) < self.events_seen:
            log.clear()
            self.events_seen = 0
        for e in events[self.events_seen:]:
            rest = "  ".join(f"{k}={v}" for k, v in e.items() if k not in ("t", "event"))
            line = Text(e["t"][11:19] + "  ", style="dim")
            line.append(f"{e['event']:<15}", style=EVENT_STYLE.get(e["event"], ""))
            line.append(rest)
            log.write(line)
        self.events_seen = len(events)

    # actions
    def _focused_table(self) -> str:
        f = self.focused
        return f.id if isinstance(f, DataTable) else "threads"

    def _selected_thread(self) -> str | None:
        tt = self.query_one("#threads", DataTable)
        if not self.thread_ids or tt.cursor_row < 0:
            return None
        return self.thread_ids[min(tt.cursor_row, len(self.thread_ids) - 1)]

    def _selected_session(self) -> dict | None:
        stt = self.query_one("#sessions", DataTable)
        if not self.session_ids or stt.cursor_row < 0:
            return None
        return sessions.get(self.project, self.session_ids[min(stt.cursor_row, len(self.session_ids) - 1)])

    def action_refresh(self) -> None:
        self.refresh_all()
        if self.thread_tid and self.query_one(TabbedContent).active == "thread-tab":
            self.load_thread(self.thread_tid)

    # --- the Thread tab ---------------------------------------------------------------

    def action_open_thread(self) -> None:
        tid = self._selected_thread()
        if tid:
            self.show_thread(tid)

    def show_thread(self, tid: str) -> None:
        self.query_one(TabbedContent).active = "thread-tab"
        pick = self.query_one("#thread-pick", Select)
        if pick.value != tid:
            pick.value = tid  # raises Select.Changed, which loads it
        else:
            self.load_thread(tid)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "thread-pick" and isinstance(event.value, str):
            self.load_thread(event.value)

    def load_thread(self, tid: str) -> None:
        self.thread_tid = tid
        quick = threadinfo.collect(self.project, tid, sizes=False)
        self.fill_thread(quick)  # at once, sizes still "…"
        self.measure_thread(tid)

    @work(thread=True, exclusive=True, group="usage")
    def update_usage(self) -> None:
        """Re-read changed Claude transcripts (in the background)."""
        if usage.refresh(self.project) or not self.token_totals:
            tot = usage.totals(self.project)
            self.call_from_thread(self._usage_done, tot)

    def _usage_done(self, tot: dict[str, int]) -> None:
        self.token_totals = tot
        if self.thread_tid and self.query_one(TabbedContent).active == "thread-tab":
            self.fill_claude(usage.for_thread(self.project, self.thread_tid))

    @work(thread=True, exclusive=True, group="thread-sizes")
    def measure_thread(self, tid: str) -> None:
        data = threadinfo.collect(self.project, tid, sizes=True)
        if self.thread_tid == tid:
            self.call_from_thread(self.fill_thread, data)

    def fill_thread(self, d: dict) -> None:
        def size(n):
            return "…" if n is None else H(n)

        head = (f"{d['thread']}  {d['name']}  [{d['status']}]   claim: {d['claim']}"
                + (f" by {d['held_by']}" if d["held_by"] else "")
                + f"   branch {d['branch']}   checkout {d['checkout'] or '(none yet)'}")
        self.query_one("#thread-head", Static).update(head)

        t = self.query_one("#t-sources", DataTable)
        t.clear()
        for r in d["sources"]:
            style = "yellow" if r["dirty"] else "green" if r["ahead"] else ""
            t.add_row(Text(r["repo"], style="bold"), r["branch"] or "detached",
                      (r["commit"] or "")[:9], Text(r["state"], style=style), size(r["bytes"]))

        t = self.query_one("#t-builds", DataTable)
        t.clear()
        for kind, rows in (("made", d["builds"]), ("used", d["uses"])):
            for b in rows:
                src = b.get("source") or {}
                t.add_row(kind, Text(b["name"], style="bold"), b.get("path", "?"),
                          f"{src.get('repo')}@{(src.get('commit') or '')[:9]}" if src else "?",
                          b.get("cost", "?"),
                          size(b.get("bytes")) if b.get("exists", True) else Text("missing", style="red"))

        t = self.query_one("#t-experiments", DataTable)
        t.clear()
        for e in d["experiments"]:
            st = {"done": "green", "running": "yellow", "planned": "dim"}.get(e["status"], "")
            t.add_row(Text(e["id"], style="bold"), Text(e["status"], style=st), e["title"],
                      "yes" if e["has_summary"] else "-", size(e["record_bytes"]),
                      size(e["data_bytes"]) if e["data"] else "-")

        t = self.query_one("#t-disk", DataTable)
        t.clear()
        if d["disk"] is None:
            t.add_row("measuring …", "")
        else:
            for k, v in d["disk"].items():
                t.add_row(Text(k, style="bold" if k.startswith("total") else ""), H(v))

        self.fill_processes(d["processes"])
        self.fill_claude(d["claude"])

    def fill_claude(self, v: dict) -> None:
        T = usage.tokens
        t = self.query_one("#t-claude", DataTable)
        t.clear()
        for m, c in v["models"].items():
            t.add_row(Text(usage.short_model(m), style="bold"), Text(f"{c['pct']:.1f}%", style="cyan"),
                      T(c["total"]), T(c["input"]), T(c["cache_write"]), T(c["cache_read"]),
                      T(c["output"]), str(c["messages"]))
        if v["models"]:
            k = v["kinds"]
            cost = f"  (about ${v['cost']:.2f})" if v["cost"] is not None else ""
            t.add_row(Text("total" + cost, style="bold"), "100%", Text(T(v["total"]), style="bold"),
                      T(k["input"]), T(k["cache_write"]), T(k["cache_read"]), T(k["output"]), "")
        else:
            t.add_row(Text("no Claude messages recorded for this thread", style="dim"), *[""] * 7)
        t = self.query_one("#t-claude-sessions", DataTable)
        t.clear()
        for s in v["sessions"]:
            t.add_row(Text(s["session"], style="bold" if s["registered"] else "yellow"),
                      usage.utc(s["first"]), usage.utc(s["last"])[11:], str(s["prompts"] or 0),
                      T(s["total"]),
                      ", ".join(f"{usage.short_model(m)} {T(n)}" for m, n in s["models"].items()),
                      f"${s['cost']:.2f}" if s["cost"] is not None else "",
                      (s["title"] or "") + ("" if s["registered"] else "  (unregistered)"))

    def fill_processes(self, procs: list[dict]) -> None:
        t = self.query_one("#t-processes", DataTable)
        t.clear()
        for r in procs:
            mins, secs = divmod(r["seconds"], 60)
            t.add_row(str(r["pid"]), f"{mins}:{secs:02d}", r["why"], r["cwd"], r["cmd"][:100])
        if not procs:
            t.add_row("", "", "", "", Text("nothing running for this thread", style="dim"))

    def action_toggle_ended(self) -> None:
        self.show_ended = not self.show_ended
        self.refresh_all()
        self.notify("showing ended sessions" if self.show_ended else "hiding ended sessions")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_details()

    def action_details(self) -> None:
        p = self.project
        if self._focused_table() == "sessions":
            r = self._selected_session()
            if not r:
                return
            body = "\n".join(f"{k:10} {v}" for k, v in r.items())
            self.push_screen(TextScreen(f"Session {r['short']} ({sessions.status(r)})", body))
            return
        tid = self._selected_thread()
        t = threads.discover(p).get(tid) if tid else None
        if not t:
            return
        from rms import inventory, lifecycle
        f = lifecycle.thread_file(p, t.id) if lifecycle.uses_git(p) else None
        body = (threads.summary(t, f) + "\n\nClaim: " + claims.describe(p, claims.get(p, t.id))
                + "\n\n" + inventory.view_text(p, t.id))
        self.push_screen(TextScreen(f"{t.id}  {t.name}", body))

    def action_settle(self, status: str) -> None:
        if self._focused_table() != "warnings" or not self.warning_ids:
            self.notify("select a warning first (tab to the Warnings pane)", severity="warning")
            return
        wtable = self.query_one("#warnings", DataTable)
        wid = self.warning_ids[min(wtable.cursor_row, len(self.warning_ids) - 1)]
        verb = "Dismiss (accept as is)" if status == "dismissed" else "Mark resolved"

        def done(yes: bool | None) -> None:
            if yes:
                warns.settle(self.project, wid, status, by="owner (tui)")
                self.notify(f"{wid} {status}")
                self.refresh_all()

        self.push_screen(ConfirmScreen(f"{verb}: {wid}?", "Yes (y)"), done)

    def action_approve_close(self) -> None:
        from rms import lifecycle
        tid = self._selected_thread()
        if not tid:
            return
        q = (f"Allow the session holding {tid} to close it (once)?\n"
             f"It then merges the result (milestone, merge point or nothing).")

        def done(yes: bool | None) -> None:
            if yes:
                lifecycle.approve_close(self.project, tid, by="owner (tui)")
                self.notify(f"close of {tid} approved")
                self.refresh_all()

        self.push_screen(ConfirmScreen(q, "Approve (y)"), done)

    def action_release(self) -> None:
        p = self.project
        if self._focused_table() == "sessions":
            r = self._selected_session()
            tid = r.get("thread") if r else None
        else:
            tid = self._selected_thread()
        c = claims.get(p, tid) if tid else None
        if not c:
            self.notify("no claim to release here", severity="warning")
            return
        st, _ = claims.state(p, c)
        warn = ("\nThat session is LIVE: it will lose write access to the thread."
                if st == "live" else "")
        q = f"Release {tid} ({claims.describe(p, c)})?{warn}"

        def done(yes: bool | None) -> None:
            if not yes:
                return
            try:
                claims.release(p, tid, by="owner (tui)")
                self.notify(f"released {tid}")
            except claims.ClaimError as e:
                self.notify(str(e), severity="error")
            self.refresh_all()

        self.push_screen(ConfirmScreen(q), done)


def run(project: Project) -> int:
    RMSApp(project).run()
    return 0
