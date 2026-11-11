"""The terminal UI: threads, sessions and events, live.

Run: ``rms tui`` (from inside the project, or with --project).
It only reads RMS's files (refreshed every second); its one action is the
owner releasing a claim.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, RichLog, Static

from rms import claims, sessions, store, threads
from rms.config import Project

STATE_STYLE = {"free": "dim", "live": "bold green", "stale": "bold yellow",
               "gone": "yellow", "ended": "dim"}
EVENT_STYLE = {"claim": "green", "claim-refused": "red", "take-over": "yellow", "release": "cyan",
               "denied": "red", "handover": "cyan", "handover-due": "yellow", "close": "magenta", "close-approved": "magenta", "close-refused": "red", "create": "green", "hook-error": "bold red", "session-start": "blue",
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


class RMSApp(App):
    TITLE = "RMS"
    CSS = """
    Screen { layout: vertical; }
    #top { height: 1fr; }
    #threads-pane { width: 3fr; }
    #sessions-pane { width: 2fr; }
    #events-pane { height: 12; }
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
        Binding("enter", "details", "Details", show=True),
        Binding("e", "toggle_ended", "Show ended"),
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
        with Horizontal(id="top"):
            with Vertical(id="threads-pane", classes="pane"):
                yield Label("Threads", classes="pane-title")
                yield DataTable(id="threads", cursor_type="row", zebra_stripes=True)
            with Vertical(id="sessions-pane", classes="pane"):
                yield Label("Sessions", classes="pane-title")
                yield DataTable(id="sessions", cursor_type="row", zebra_stripes=True)
        with Vertical(id="events-pane", classes="pane"):
            yield Label("Events", classes="pane-title")
            yield RichLog(id="events", markup=False, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"{self.project.name} · {self.project.root}"
        self.query_one("#threads", DataTable).add_columns(
            "ID", "Thread", "Status", "Claim", "Held by", "Since")
        self.query_one("#sessions", DataTable).add_columns(
            "Session", "State", "Thread", "Where", "Started", "Last seen")
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

        stale = sum(claims.state(p, c)[0] == "stale" for c in cl.values())
        summary = f"{len(all_threads)} threads · {len(cl)} claimed · {live} live sessions"
        if stale:
            summary += f" · {stale} stale claim{'s' if stale > 1 else ''} (select, x to release)"
        self.query_one("#summary", Static).update(summary)

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
        body = threads.summary(t) + "\n\nClaim: " + claims.describe(p, claims.get(p, t.id))
        self.push_screen(TextScreen(f"{t.id}  {t.name}", body))

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
