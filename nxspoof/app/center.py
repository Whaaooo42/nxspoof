"""
nxspoof.app.center - the control center: the dashboard, the one that serves the page (D16-D18, D27, D30).

  python3 -B nx_spoof.py dashboard        (or double-click NXSPOOF.app or scripts/dashboard.command, or menu 5)

No hotspot and no admin rights: the page at http://localhost:8800 with everything that doesn't need them: the consoles
(Add, Edit, Remove), ⚙️ Configure (it writes settings.json, run or not), the past runs (every run in logs/), This Mac
(setup's checks, and Set up / Undo setup), and ▶ Start (app/starter.py: nx_spoof.py start through macOS's password window).
It finds the run (app/runfollow.py: the lock and run.json), shows it from its files (its records, run_state.json), stops it
with a signal, and is on its own again when the run's lock is free. Closing the page leaves the run going (the next
dashboard shows it). It runs until no page shows it any more (app/pagewatch.py, D27), Ctrl+C, or its Terminal window closes.
"""

import os
import threading
import time

from nxspoof.app import signals
from nxspoof.app.pagewatch import PageWatch
from nxspoof.app.runfollow import RunFollow
from nxspoof.app.starter import PageStart, hotspot_name, mac_checks
from nxspoof.app.tasks import Leftovers, MacTasks
from nxspoof.consoles.activity import ConsoleActivity
from nxspoof.consoles.registry import ConsoleRegistry
from nxspoof.core.controls import ControlError, Controls
from nxspoof.core.events import EventBus
from nxspoof.dashboard import center as center_link
from nxspoof.dashboard import controls as dashboard_controls
from nxspoof.dashboard.server import DashboardServer, DashboardSink, js_spans
from nxspoof.dashboard.state import build_state
from nxspoof.features import runlog
from nxspoof.output.logfile import LogFileSink, RecordSink, create_run_log, records_path
from nxspoof.output.terminal import TerminalSink

HOTSPOT_EVERY = 30                           # seconds between two reads of the hotspot's name while nothing runs
# The control center: its page, its controls, and the run it shows (if any).
class Center(object):
    # store: settings.json (loaded); system: the Mac (app/system.py); places: where NXSPOOF's files are (core/places.py:
    # the program, its scripts/; logs; the runtime files: the run's lock, run.json, run_state.json); stream: its Terminal
    # window; ignored: options given on the command line that only apply to a run (said once at the start); prepared: what
    # preparing the folders did ([(level, text)], said once at the start).
    def __init__(self, store, message, system, places, stream=None, ignored=(), prepared=()):
        self.store, self.message, self.system, self.project_dir = store, message, system, places.program
        self.places, self.prepared = places, list(prepared)
        self.ignored = list(ignored)
        self.bus = EventBus()
        self.registry, self.activity = ConsoleRegistry(store), ConsoleActivity()
        tty = bool(getattr(stream, "isatty", lambda: False)()) if stream is not None else os.isatty(1)
        self.bus.add_sink(TerminalSink("technical", tty, self.registry.color_of, stream))
        self.logfile, self.records = LogFileSink(), RecordSink()         # the dashboard's own log and its records (the problem report reads them)
        self.bus.add_sink(self.logfile)
        self.bus.add_sink(self.records)
        self.follow = RunFollow(self)
        self.starter, self.tasks, self.leftovers = PageStart(self), MacTasks(self), Leftovers(self)
        self.started = time.time()
        self.hotspot_read = (0.0, None)        # (when, the hotspot's name then): read again after a run or a task, or HOTSPOT_EVERY
        self.hotspot_failed = False            # an unreadable hotspot name was said once
        self.stopping, self.stop_reason, self.lock = threading.Event(), None, threading.Lock()
        self.own = Controls()
        dashboard_controls.register(self.own, self.bus, store, self.registry, self.activity)
        self.own.add("start_run", self.starter.start, available=self.idle)
        self.own.add("mac_task", self.tasks.run, available=self.idle)
        self.own.add("open_sharing_settings", self.open_sharing_settings)
        self.own.add("import_settings", lambda p: dashboard_controls.import_settings(self.bus, self.store, p), available=self.idle)
        self.follow.register_controls(self.own)
        self.controls = self.own
        self.server = DashboardServer(center_link.remembered_port(self.places.runtime), self.state, self.controls,
                                      docs_dir=self.places.docs_folder(), problem=lambda tech, details: self.bus.log(
                                          "DASH", tech, level="problem", details=details))
        self.pagewatch = PageWatch(self)
        self.bus.add_sink(DashboardSink(self.server))
        self.server.add_reader("hello", lambda q: {"center": True})
        self.server.add_reader("runs", lambda q: runlog.list_runs(self.logs_dir()))
        self.server.add_reader("run", lambda q: self.run_details(self.logs_dir(), q.get("name")))
        self.server.add_reader("mac", lambda q: self.mac_checks())
        self.server.add_reader("settings_file", lambda q: {"name": "settings.json", "text": self.store.export_text()})

    # A DASH line on the control center's bus.
    def note(self, tech, casual=None, level="info"):
        return self.bus.log("DASH", tech, casual, level)

    # ##########################################################################################################################################################
    # ##  WHAT THE PAGE SHOWS                                                                                                                                 ##
    # ##########################################################################################################################################################

    # The snapshot: the run's (run_state.json, app/runfollow.py) while one is shown, with the dashboard's own buttons; else
    # the control center's (no run, settings as saved).
    def state(self):
        if self.follow.present():
            st = self.follow.state()
            if st:
                st["actions"] = self.own.available()
                st["center"] = {"present": True, "run": True, "starting": False}
                return st
        s = self.store
        info = {"phase": "idle", "started": self.started, "hotspot_ip": None, "hotspot_name": self.hotspot_name(), "dry_run": False, "firewall": True,
                "firewall_loaded": False, "auto_sharing": True, "ftp_watch": bool(s.get("ftp.watch")),
                "console_mode": "technical", "filezilla": bool(self.system.filezilla_installed()),
                "log_file": os.path.relpath(self.logfile.path, self.places.logs) if self.logfile.path else None}
        st = build_state(info, self.registry, self.activity, s, self.own)
        st["center"] = {"present": True, "run": False, "starting": self.starter.shown(),
                        "task": self.tasks.state(), "leftovers": self.leftovers.state()}
        return st

    # The hotspot's name as macOS has it (read again every HOTSPOT_EVERY seconds: no admin rights needed), None if unreadable.
    def hotspot_name(self):
        if time.time() - self.hotspot_read[0] >= HOTSPOT_EVERY:
            self.hotspot_read = (time.time(), hotspot_name(self.system, self._hotspot_unreadable))
        return self.hotspot_read[1]

    # The hotspot's settings couldn't be read: said once (the page just shows no name).
    def _hotspot_unreadable(self, why):
        if not self.hotspot_failed:
            self.hotspot_failed = True
            self.note("couldn't read the hotspot's settings (%s) - the page shows no name" % why, level="warn")

    # One run of logs/ in detail (ControlError if there's no such run).
    def run_details(self, logs, name):
        details = runlog.run_details(logs, name)
        if details is None:
            raise ControlError("no run called %r" % name)
        for ev in details["events"]:                                   # the page counts in UTF-16 units (emoji)
            ev["tech_names"] = js_spans(ev["tech"], ev["tech_names"]) if ev["tech"] else []
            ev["casual_names"] = js_spans(ev["casual"], ev["casual_names"]) if ev["casual"] else []
        return details

    # ##########################################################################################################################################################
    # ##  BUTTONS AND THE RUN                                                                                                                                 ##
    # ##########################################################################################################################################################

    # This Mac: doctor's checks, read only (app/starter.py, which ▶ Start's quick check uses too).
    def mac_checks(self):
        return mac_checks(self.project_dir, self.system, self.places, self.logs_dir())

    # True while a ▶ Start is under way (app/starter.py), until the run shows up.
    def is_starting(self):
        return self.starter.busy()

    # True when the Mac is free for a start or a task: no run shown, no start under way, no This Mac task running.
    def idle(self):
        return not self.follow.present() and not self.is_starting() and not self.tasks.busy()

    # The log files' folder (core/places.py: logs/ in the logs role).
    def logs_dir(self):
        return self.places.logs_folder()

    # The open_sharing_settings control: System Settings → Sharing (for the hotspot's name and password).
    def open_sharing_settings(self, payload=None):
        self.system.open_sharing_settings()
        self.note("opened System Settings → Sharing (the hotspot's name and password are set there)")
        return {"ok": True, "message": "System Settings → Sharing is open: the ⓘ next to Internet Sharing has the name and password."}

    # Close the dashboard while a run goes on (its page was closed, app/pagewatch.py): the run keeps going and shows up
    # again on the next dashboard you open. reason: for the log. Returns True if this asked to close.
    def close_keeping_run(self, reason):
        self.note("closing the dashboard (%s); the run keeps going (open NXSPOOF again to see it or stop it)" % reason,
                  casual="👋 The dashboard closes. NXSPOOF keeps running: open NXSPOOF again to see it or stop it.")
        return self.request_stop(reason + ", run kept")

    # A run appeared (app/runfollow.py): shown here from now on; a start under way is over.
    def run_appeared(self, info):
        self.starter.done()
        self.note("NXSPOOF is running (process %s): showing it here, from its log (%s)" % (info.get("pid"), info.get("log")))

    # The run's lock is free again (app/runfollow.py): back on our own.
    def run_ended(self):
        self.leftovers.checked = 0                                     # did it leave anything behind? (checked on the next poll)
        self.hotspot_read = (0.0, None)                                # the run may have renamed the hotspot
        self.note("the run has ended - the dashboard is on its own again",
                 casual="🏁 NXSPOOF stopped. The dashboard stays open: settings, past runs, ▶ Start.")

    # Ask the control center to close (signals, its page closed); the first request wins.
    def request_stop(self, reason):
        with self.lock:
            if self.stop_reason is not None:
                return False
            self.stop_reason = reason
        self.stopping.set()
        return True

    # ##########################################################################################################################################################
    # ##  OPEN, WAIT, CLOSE                                                                                                                                   ##
    # ##########################################################################################################################################################

    # Open the page, wait until closed, then tidy up; returns the exit code. signals=False: leave Ctrl+C alone (tests).
    def run(self, signals_on=True):
        already = center_link.find(self.places.runtime)
        if already:                              # one dashboard at a time: the open one comes to the front
            url = "http://localhost:%d/" % int(already["port"])
            self.note("NXSPOOF's dashboard is already open (%s) - opening its page" % url, casual="🖥️ NXSPOOF is already open: %s" % url)
            self._open_browser(url)
            return 0
        url = self._serve()
        try:
            self.logfile.path = create_run_log(self.places.logs, label="dashboard", records=True,
                                               give=self.system.give_to_user)
            self.records.path = records_path(self.logfile.path)
        except OSError:
            pass
        center_link.opened(self.places.runtime, self.server.port, give=self.system.give_to_user)
        self.note("data folder: %s; the 'data' shortcut in the nx-spoof folder opens it" % self.places.describe())
        for level, text in self.prepared:                                         # the first time: old files moved over
            self.note(text, casual="📁 " + text[0].upper() + text[1:] + ".", level=level)
        self.bus.log("SETTINGS", self.message)
        if self.ignored:
            self.bus.log("SETTINGS", "ignored: %s (options only apply to a run; the dashboard on its own, and the runs it starts, "
                         "go by settings.json)" % "  ".join(self.ignored),
                         casual="ℹ️ The dashboard ignores %s: options only apply to a run started by hand." % ", ".join(self.ignored))
        self.note("dashboard open on %s (this Mac only)" % url, casual="🖥️ The dashboard is open: %s  (closing its page closes it; Ctrl+C too)" % url)
        self.follow.check()                      # a run already going shows at once
        if not self.follow.present():
            self.note("no run - press Start on the page, or menu 1")
        self._open_browser(url)                  # always: the page IS the dashboard (D27)
        previous = signals.install(self) if signals_on else {}
        try:
            while not self.stopping.wait(0.5):
                self.follow.check()
                self.pagewatch.check()
        finally:
            signals.restore(previous)
            self._close_down()
        return 0

    # Open the page in your browser (a failure is only noted).
    def _open_browser(self, url):
        try:
            self.system.open_url(url)
        except Exception as e:
            self.note("could not open the browser (%s) - open %s yourself" % (e, url))

    # Serve on the remembered port; something else holds it: a free one, remembered from now on. Returns the page's address.
    def _serve(self):
        try:
            return self.server.start()
        except OSError:
            wanted, self.server.port = self.server.port, 0
            url = self.server.start()
            self.note("port %d is used by another program: the dashboard is on %d from now on" % (wanted, self.server.port))
            return url

    # Say the port is free again (it stays remembered), say goodbye to open pages, stop serving.
    def _close_down(self):
        center_link.closed(self.places.runtime)
        if self.follow.present() and not (self.stop_reason or "").endswith((", run kept", ", run stopped")):
            self.note("a run is still active: it keeps running (open NXSPOOF again to see it and stop it, or menu 2)", level="warn",
                      casual="⚠️ NXSPOOF is still running: open NXSPOOF again to see it and stop it.")
        self.note("dashboard closed", casual="👋 Dashboard closed.")
        self.server.stop()
