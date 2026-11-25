"""
test_center.py - unit tests for the control center (app/center.py, dashboard/center.py): the dashboard on its
own, and a real run (on the fake Mac of test_app.py) reporting to it.

Everything runs on 127.0.0.1 with high ports in a temporary project folder: the real logs/, settings.json
and dashboard are never touched.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from nxspoof.app import center as center_app
from nxspoof.app import starter
from nxspoof.app.center import Center
from nxspoof.core.controls import ControlError
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.core.places import Places
from nxspoof.dashboard import center as center_link
from nxspoof.output import runfile
from nxspoof.settings.store import SettingsStore
from tests.test_app import FakeSystem, RunTest

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")
REFERENCE_LOG = os.path.join(PROJECT_DIR, "tests", "fixtures", "old_text_logs", "ctrl_c.log")     # a past run, as an old text log
CENTER_PORT = 8892

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A fake Mac that records what the control center opens.
class CenterSystem(FakeSystem):
    # Nothing opened yet; the password window answers `answer` (and the "run" writes `wrote` to its output file).
    def __init__(self, leases_path):
        FakeSystem.__init__(self, leases_path)
        self.opened, self.passwords = [], []
        self.answer, self.wrote = ("started", ""), ""
        self.before_answer = None            # what "the command" does before the window answers (a task's records)
        self.sharing_on = False

    # Record the password window's command instead of showing it.
    def start_with_password(self, cwd, cmd, output, prompt):
        self.passwords.append((cwd, cmd, output, prompt))
        with open(output, "a", encoding="utf-8") as f:
            f.write(self.wrote)
        return "handle"

    # The password window's answer (after the command's own work, for a task).
    def password_outcome(self, handle):
        if self.before_answer:
            self.before_answer(self.passwords[-1][1])
        return self.answer

    # The hotspot is up when the test says so.
    def sharing_running(self):
        return self.sharing_on

    # Record instead of opening the browser.
    def open_url(self, url):
        self.opened.append(("browser", url))

# Wait until a condition holds (or fail after a few seconds).
def wait_for(test, condition, what, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return
        time.sleep(0.05)
    test.fail("timed out waiting for: %s" % what)

# A control center running in the background in a temporary project folder.
class CenterTest(RunTest):
    IGNORED = ()                 # options given on the command line that the dashboard ignores

    # The project folder (from RunTest) with a run log, the dashboard's port remembered as ours, and the control center started.
    def setUp(self):
        RunTest.setUp(self)
        shutil.copy(FIXTURE, os.path.join(self.dir, "settings.json"))
        with open(os.path.join(self.dir, center_link.DASH_FILE), "w", encoding="utf-8") as f:
            json.dump({"port": CENTER_PORT}, f)
        os.makedirs(os.path.join(self.dir, "logs"))
        shutil.copy(REFERENCE_LOG, os.path.join(self.dir, "logs", "2026-11-14_09-00-00.log"))
        self.system = CenterSystem(os.path.join(self.dir, "leases"))
        self.store = SettingsStore(os.path.join(self.dir, "settings.json"))
        message = self.store.load()
        self.devnull = open(os.devnull, "w")
        self.center = Center(self.store, message, self.system, Places.together(self.dir, self.dir), stream=self.devnull, ignored=self.IGNORED)
        self.thread = threading.Thread(target=self.center.run, kwargs={"signals_on": False}, daemon=True)
        self.thread.start()
        wait_for(self, lambda: center_link.find(self.dir) is not None, "the control center to open")

    # Close the control center and remove the folder.
    def tearDown(self):
        self.center.request_stop("test")
        self.thread.join(5)
        self.devnull.close()
        RunTest.tearDown(self)

    # The page's view of the control center: GET a path -> JSON.
    def get(self, path):
        return center_link.http_json(CENTER_PORT, path)[1]

    # Press a control as the page does.
    def press(self, name, payload=None):
        return center_link.http_json(CENTER_PORT, "/api/action/" + name, payload or {}, token=self.center.server.token)

# The dashboard on its own: no run, everything that doesn't need admin rights.
class OnItsOwn(CenterTest):
    # Idle state: no run, consoles and settings from settings.json, Start and Close offered; browser opened; secret file written.
    def test_idle_state(self):
        st = self.get("/api/state")
        self.assertEqual((st["run"]["phase"], st["center"]), ("idle", {"present": True, "run": False, "starting": False,
                                                                   "task": None, "leftovers": []}))
        self.assertEqual([c["name"] for c in st["consoles"]], ["Switch 1", "Switch 2"])
        self.assertEqual(sorted(st["actions"]), ["add_console", "edit_console", "import_settings", "mac_task", "open_sharing_settings",
                                                 "remove_console", "set_settings", "start_run"])
        self.assertIn(("browser", "http://localhost:%d/" % CENTER_PORT), self.system.opened)
        self.assertEqual(center_link.read_file(self.dir), {"port": CENTER_PORT, "pid": os.getpid()})

    # Edits while idle are written by the control center (it owns settings.json while no run is active).
    def test_edits_while_idle(self):
        status, answer = self.press("set_settings", {"values": {"ftp.port": "2121"}})
        self.assertEqual((status, answer["message"]), (200, "Saved 1 change."))
        self.assertEqual(json.loads(read_text(os.path.join(self.dir, "settings.json")))["ftp"]["port"], 2121)

    # Runs and one run's details from logs/; a name outside logs/ is refused.
    def test_runs(self):
        runs = self.get("/api/data/runs")
        self.assertEqual([r["name"] for r in runs], ["2026-11-14_09-00-00.log"])
        self.assertEqual(runs[0]["consoles"][0], ["Switch 2", "48:f1:eb:5a:22:c2"])
        details = self.get("/api/data/run?name=2026-11-14_09-00-00.log")
        first = details["events"][0]                                   # an old run (text only): technical lines, no marks
        self.assertEqual((first["t"], first["tech"], first["casual"], first["tech_names"]), ("23:57:39", "LOG   this run: logs/2026-11-13_23-57-39.log", None, []))
        self.assertEqual(details["items"][-1]["status"], "pass")
        status, answer = center_link.http_json(CENTER_PORT, "/api/data/run?name=../settings.json")
        self.assertEqual(status, 400)

    # ▶ Start on a Mac that isn't ready: the page gets the reasons, nothing is asked; "Fix and start" and "Start anyway" ask for
    # the password (start --fix, start --anyway).
    def test_start_not_ready(self):
        self.center.mac_checks = lambda: [{"name": "bridge", "title": "Thunderbolt Bridge", "state": "fixable", "casual": "needs setup",
                                           "tech": "bridge: automatic", "explain": [], "fixable": True}]
        status, answer = self.press("start_run")
        self.assertEqual((status, answer["not_ready"]), (200, [{"title": "Thunderbolt Bridge", "casual": "needs setup", "fixable": True}]))
        self.assertEqual(self.system.passwords, [])
        self.assertEqual(self.press("start_run", {"fix": True})[0], 200)            # Fix and start: one password for both
        self.assertEqual(self.system.passwords[-1][1], ["/usr/bin/python3", "-B", "./nx_spoof.py", "start", "--fix"])
        wait_for(self, lambda: not self.center.starter.busy(), "that start to be over (no run in a test)")
        self.assertEqual(self.press("start_run", {"anyway": True})[0], 200)
        cwd, cmd, output, prompt = self.system.passwords[-1]
        self.assertEqual(cmd, ["/usr/bin/python3", "-B", "./nx_spoof.py", "start", "--anyway"])
        self.assertEqual((cwd, output), (os.path.join(self.dir, "program"), os.path.join(self.dir, "logs", ".page_start.txt")))
        self.assertIn("needs your password", prompt)

    # ▶ Start runs from the program copy in the data folder (made from the nx-spoof folder), never from the nx-spoof folder.
    def test_start_from_program_copy(self):
        self.center.mac_checks = lambda: []
        self.center.project_dir = PROJECT_DIR                    # the real program, copied (only read) into the test's data folder
        self.assertEqual(self.press("start_run")[0], 200)
        cwd = self.system.passwords[-1][0]
        self.assertEqual(cwd, os.path.join(self.dir, "program"))
        for rel in ("nx_spoof.py", os.path.join("nxspoof", "app", "starter.py"), os.path.join("nxspoof", "dashboard", "web", "index.html")):
            self.assertTrue(os.path.isfile(os.path.join(cwd, rel)), rel)
        self.assertIn("program copy ready: %s (" % cwd, read_text(self.center.logfile.path))

    # Write what a setup / clean up started by the page writes: its log and records with the ticket from cmd, its facts.
    def fake_task(self, label, facts):
        # The fake command: its records, with the ticket it was given.
        def write(cmd):
            ticket = cmd[cmd.index("--task") + 1]
            base = os.path.join(self.dir, "logs", "2026-11-16_18-00-00-%s" % label)
            open(base + ".log", "w").close()
            with open(base + ".jsonl", "w", encoding="utf-8") as f:
                for kind, data in [("task.started", {"ticket": ticket})] + facts:
                    f.write(json.dumps({"kind": kind, "data": data, "ts": time.time()}) + "\n")
        return write

    # This Mac's 🛠️ with two ticked steps: setup --yes --only, waited for (not in the background), followed from its records.
    def test_task_setup(self):
        step = lambda name, state: ("checklist.step", {"step": name, "title": name.title(), "state": state, "says": "x"})
        self.system.before_answer = self.fake_task("setup", [step("bridge", "fixable"), step("bridge", "fixed"), step("cable", "manual"),
                                                             step("python", "ok"), ("checklist.done", {"command": "setup", "todo": 1})])
        status, answer = self.press("mac_task", {"kind": "setup", "steps": ["cable", "bridge"]})
        self.assertEqual(status, 200, answer)
        cwd, cmd, output, prompt = self.system.passwords[-1]
        self.assertEqual(cmd[3:8], ["setup", "--yes", "--only", "bridge,cable", "--task"])
        self.assertEqual((cwd, os.path.basename(output)), (os.path.join(self.dir, "program"), ".page_task.txt"))
        self.assertIn("to set up this Mac", prompt)
        wait_for(self, lambda: (self.get("/api/state")["center"]["task"] or {}).get("phase") == "done", "the task to finish")
        task = self.get("/api/state")["center"]["task"]
        self.assertEqual([(s["step"], s["state"]) for s in task["steps"]], [("bridge", "fixed"), ("cable", "manual"), ("python", "ok")])
        self.assertEqual((task["todo"], task["log"]), (1, os.path.join("logs", "2026-11-16_18-00-00-setup.log")))
        self.assertIn("setup finished: 1 fixed (bridge), 1 need you (cable), 1 fine; its log: logs/2026-11-16_18-00-00-setup.log",
                      read_text(self.center.logfile.path))

    # What the page may not ask: an unknown task, no steps, steps setup doesn't know (nothing of it reaches a shell line), during a run.
    def test_task_refused(self):
        for payload in ({"kind": "rm"}, {"kind": "setup"}, {"kind": "setup", "steps": []}, {"kind": "setup", "steps": ["bridge; rm -rf ~"]},
                        {"kind": "setup", "steps": "bridge"}):
            self.assertEqual(self.press("mac_task", payload)[0], 400, payload)
        held = runfile.take(self.dir)            # a run holds the lock
        self.assertEqual(self.press("mac_task", {"kind": "cleanup"})[0], 400)
        held.close()
        self.assertEqual(self.system.passwords, [])

    # Cancelled in the password window: said; a command that ends without its closing fact: said, with what it wrote.
    def test_task_cancelled_or_cut_short(self):
        self.system.answer = ("cancelled", "the password window was cancelled")
        self.assertEqual(self.press("mac_task", {"kind": "undo"})[0], 200)
        wait_for(self, lambda: (self.get("/api/state")["center"]["task"] or {}).get("phase") == "cancelled", "the cancel")
        self.system.answer, self.system.wrote = ("started", ""), "Traceback: boom\n"
        self.assertEqual(self.press("mac_task", {"kind": "cleanup"})[0], 200)
        wait_for(self, lambda: (self.get("/api/state")["center"]["task"] or {}).get("phase") == "failed", "the cut-short task")
        log = read_text(self.center.logfile.path)
        self.assertIn("DASH  undo cancelled: the password window was cancelled", log)
        self.assertIn("DASH  cleanup: ended without its closing line - what it wrote:", log)
        self.assertIn("Traceback: boom", log)

    # No Unplug any more (a Stop and Clean up take the cable out): the page can't ask for it.
    def test_no_cable_out(self):
        self.assertEqual(self.press("mac_task", {"kind": "cable_out"})[0], 400)
        self.assertEqual(self.system.passwords, [])

    # Export gives the tidy file; Import shows what changes first (nothing written), then imports and keeps the old file.
    def test_export_and_import(self):
        exported = self.get("/api/data/settings_file")
        self.assertEqual(exported["name"], "settings.json")
        data = json.loads(exported["text"])
        data["consoles"] = data["consoles"][:1]
        data["ftp"]["port"] = 2121
        text = json.dumps(data)
        status, answer = self.press("import_settings", {"text": text})
        self.assertEqual(status, 200, answer)
        self.assertEqual((answer["preview"]["consoles_removed"], answer["preview"]["settings"]), (["Switch 2"], [["ftp.port", 5000, 2121]]))
        self.assertEqual(self.store.get("ftp.port"), 5000)                       # a preview writes nothing
        self.assertEqual(self.press("import_settings", {"text": "{ broken", "apply": True})[0], 400)
        status, answer = self.press("import_settings", {"text": text, "apply": True})
        self.assertEqual(status, 200, answer)
        self.assertEqual(self.store.get("ftp.port"), 2121)
        kept = [n for n in os.listdir(self.dir) if n.startswith("settings.json.before-import-")]
        self.assertEqual(len(kept), 1)
        self.assertIn("SETTINGS imported from a file: 1 console removed (Switch 2); 1 setting changed (ftp.port 5000 -> 2121); "
                      "your previous settings are kept as %s" % kept[0], read_text(self.center.logfile.path))

    # The crash banner: a last run without "Stopped." (until a clean up newer than it), Internet Sharing on with no run.
    def test_leftovers(self):
        base = os.path.join(self.dir, "logs", "2026-11-16_17-00-00")
        open(base + ".log", "w").close()
        with open(base + ".jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "run.version", "data": {}, "ts": time.time()}) + "\n")
        self.system.sharing_on = True
        self.center.leftovers.checked = 0
        found = self.get("/api/state")["center"]["leftovers"]
        self.assertEqual([f["kind"] for f in found], ["crash", "sharing"])
        self.assertIn("The last run (2026-11-16 17:00) never reached its end", found[0]["text"])
        self.assertIn("leftovers: The last run (2026-11-16 17:00)", read_text(self.center.logfile.path))
        later = os.path.join(self.dir, "logs", "2026-11-16_17-05-00-cleanup.log")
        open(later, "w").close()
        os.utime(later, (time.time() + 5, time.time() + 5))
        self.system.sharing_on = False
        self.center.leftovers.checked = 0
        self.assertEqual(self.get("/api/state")["center"]["leftovers"], [])

    # ▶ Start, then the password window is cancelled: said in the log, ▶ Start offered again.
    def test_start_cancelled(self):
        self.center.mac_checks = lambda: []
        self.system.answer = ("cancelled", "the password window was cancelled")
        self.assertEqual(self.press("start_run")[1]["message"], "Type your Mac password in the macOS window.")
        wait_for(self, lambda: "start_run" in self.get("/api/state")["actions"], "Start to be offered again")
        log = read_text(self.center.logfile.path)
        self.assertIn("DASH  starting NXSPOOF: macOS is asking for your password (quick check: this Mac is ready; no Terminal window)", log)
        self.assertIn("DASH  start cancelled: the password window was cancelled", log)

    # The start has finished but no run holds the lock: the log says so with what the start wrote; ▶ Start is back.
    def test_start_failed(self):
        self.center.mac_checks = lambda: []
        self.system.wrote = "❌ NXSPOOF couldn't start: the hotspot didn't come up within 60 s\n"
        self.press("start_run")
        wait_for(self, lambda: "didn't start" in read_text(self.center.logfile.path), "the failed-start line")
        log = read_text(self.center.logfile.path)
        self.assertIn("DASH  NXSPOOF didn't start - what the start said:", log)
        self.assertIn("      ❌ NXSPOOF couldn't start: the hotspot didn't come up within 60 s", log)
        self.assertFalse(self.get("/api/state")["center"]["starting"])

    # The start has finished and a run holds the lock, but it never shows up here: said after the wait.
    def test_start_no_show(self):
        self.center.mac_checks = lambda: []
        self.system.wrote = "✅ NXSPOOF is running.\n"
        self.system.before_answer = lambda cmd: self.held.append(runfile.take(self.dir))
        self.held = []
        old, starter.WAIT_FOR_RUN = starter.WAIT_FOR_RUN, 1
        try:
            self.press("start_run")
            wait_for(self, lambda: "didn't show up" in read_text(self.center.logfile.path), "the no-show line")
        finally:
            starter.WAIT_FOR_RUN = old
            for h in self.held:
                h.close()
        self.assertIn("      ✅ NXSPOOF is running.", read_text(self.center.logfile.path))
        self.assertFalse(self.get("/api/state")["center"]["starting"])

    # No options given: no "ignored" line.
    def test_nothing_ignored(self):
        wait_for(self, lambda: self.center.logfile.path is not None, "the dashboard log")
        self.assertNotIn("ignored:", read_text(self.center.logfile.path))

    # Closing (its page was closed, app/pagewatch.py): the dashboard's file keeps only its port (remembered for next time).
    def test_close(self):
        self.assertTrue(self.center.request_stop("its page was closed"))
        self.thread.join(5)
        self.assertEqual(center_link.read_file(self.dir), {"port": CENTER_PORT})
        self.assertIsNone(center_link.find(self.dir))

    # One dashboard at a time: a second one opens the first one's page and ends.
    def test_second_dashboard(self):
        second = Center(self.store, "settings.json loaded", self.system, Places.together(self.dir, self.dir), stream=self.devnull)
        self.assertEqual(second.run(signals_on=False), 0)
        self.assertEqual(self.system.opened[-1], ("browser", "http://localhost:%d/" % CENTER_PORT))

# The page IS the dashboard (app/pagewatch.py): it closes once no page has shown it for a while; during a run, a reminder.
class PageWatchTest(unittest.TestCase):
    # A fake control center with a settable clock, page count and run.
    def setUp(self):
        from nxspoof.app import pagewatch
        self.pw, self.now, self.calls = pagewatch, [1000.0], []
        test = self

        # The fake control center.
        class Fake(object):
            notes, answer = [], None
            follow = type("F", (), {"on": False, "present": lambda self: self.on, "stop": lambda self: test.calls.append(("signal",))})()
            server = type("S", (), {"pages": 0, "viewers": lambda self: self.pages})()
            tasks = type("T", (), {"busy": lambda self: False})()
            system = type("Sy", (), {"ask_user": lambda self, text, buttons, default: test.fake.answer})()
            controls = type("C", (), {"call": lambda self, name, payload: test.calls.append((name, payload))})()

            # Never starting.
            def is_starting(self):
                return False

            # Remember the note.
            def note(self, text, casual=None, level="info"):
                self.notes.append(text)

            # Record the request.
            def request_stop(self, reason):
                test.calls.append(("stop", reason))
                return True

            # Record the request.
            def close_keeping_run(self, reason):
                test.calls.append(("keep", reason))
                return True

            # Record the request.
            def open_page(self):
                test.calls.append(("open",))
        self.fake = Fake()
        self.watch = pagewatch.PageWatch(self.fake, clock=lambda: self.now[0])

    # Let `seconds` pass, then check.
    def later(self, seconds):
        self.now[0] += seconds
        return self.watch.check()

    # Nothing running: waits for the first page, then closes PAGE_GONE seconds after the last one went.
    def test_closes_when_the_page_is_gone(self):
        self.assertFalse(self.later(self.pw.PAGE_GONE + 1), "the first page gets FIRST_PAGE seconds")
        self.fake.server.pages = 2
        self.assertFalse(self.later(500))
        self.fake.server.pages = 0
        self.assertFalse(self.later(self.pw.PAGE_GONE - 1), "a reload takes far less")
        self.assertTrue(self.later(2))
        self.assertEqual(self.calls, [("stop", "its page was closed")])

    # During a run: the dialog. Keep running (or no answer within 2 minutes): the dashboard closes, the run goes on; Stop
    # NXSPOOF: a signal to the run, then the dashboard closes.
    def test_dialog_during_a_run(self):
        self.fake.follow.on, self.fake.server.pages = True, 1
        self.later(1)
        self.fake.server.pages = 0
        for answer in (self.pw.KEEP, None):
            self.fake.answer = answer
            self.assertTrue(self.later(self.pw.PAGE_GONE + 1))
        self.assertEqual(self.calls, [("keep", "its page was closed"), ("keep", "its page was closed")])
        self.fake.answer = self.pw.STOP
        self.assertTrue(self.later(self.pw.PAGE_GONE + 1))
        self.assertEqual(self.calls[2:], [("signal",), ("stop", "its page was closed, run stopped")])

    # NXSPOOF opened again while the dialog was up (its page is back on this dashboard): Keep running doesn't close it.
    def test_page_back_during_the_dialog(self):
        self.fake.follow.on, self.fake.server.pages = True, 1
        self.later(1)
        self.fake.server.pages, self.fake.answer = 0, self.pw.KEEP
        self.fake.system.ask_user = lambda text, buttons, default: setattr(self.fake.server, "pages", 1) or self.pw.KEEP
        self.assertFalse(self.later(self.pw.PAGE_GONE + 1))
        self.assertEqual(self.calls, [])

# The dashboard always opens its page: a dashboard nobody can see would close itself (D27).
class AlwaysOpensThePage(CenterTest):

    # The browser opened anyway.
    def test_opens(self):
        wait_for(self, lambda: ("browser", "http://localhost:%d/" % CENTER_PORT) in self.system.opened, "the page to open")

    # ▶ Start refuses while a run started elsewhere (menu, Terminal) holds the lock but isn't shown here yet.
    def test_start_refused_while_another_runs(self):
        held = runfile.take(self.dir)
        status, answer = self.press("start_run")
        held.close()
        self.assertEqual(status, 400)
        self.assertIn("already running or starting", answer["error"])

# The dashboard opened with run options on the command line: it says once, at the start, that it ignores them.
class IgnoredOptions(CenterTest):
    IGNORED = ("--keep-sharing", "--no-firewall")

    # The dashboard log says which options were ignored (and the plain dashboard says nothing of the kind).
    def test_said_once(self):
        wait_for(self, lambda: self.center.logfile.path is not None, "the dashboard log")
        lines = [l for l in read_text(self.center.logfile.path).splitlines() if "ignored:" in l]
        self.assertEqual(len(lines), 1)
        self.assertIn("SETTINGS ignored: --keep-sharing  --no-firewall (options only apply to a run;", lines[0])

# A real run (fake Mac) while the control center is open: the dashboard finds it by its lock and follows its files (D30).
class RunUnderTheCenter(CenterTest):
    # The run shows on the control center (its records, its state file); a setting saved on the page reaches the run through
    # settings.json; the page's Stop is a signal; afterwards the control center is on its own again.
    def test_run_reports_to_the_center(self):
        run, ctx, out, held = self.make_run_with_dashboard()
        result = {}
        thread = threading.Thread(target=lambda: result.update(code=run.execute()), daemon=True)
        thread.start()
        wait_for(self, lambda: self.center.follow.present(), "the dashboard to find the run")
        wait_for(self, lambda: self.get("/api/state")["run"]["phase"] == "running", "the run to be running")
        st = self.get("/api/state")
        self.assertEqual(st["center"], {"present": True, "run": True, "starting": False})
        self.assertIn("stop", st["actions"])
        self.assertNotIn("start_run", st["actions"])
        self.press("set_settings", {"values": {"ftp.port": 2121}})                           # written by the dashboard
        self.assertEqual(self.store.get("ftp.port"), 2121)
        wait_for(self, lambda: ctx.store.get("ftp.port") == 2121, "the run to read settings.json again")
        self.system.stop_run = lambda pid: (self.stopped.append(pid), ctx.request_stop("SIGTERM"))
        self.stopped = []
        self.assertEqual(self.press("stop")[0], 200)
        thread.join(10)
        held.close()                                                   # the start's lock goes with the run
        self.assertEqual((result.get("code"), self.stopped), (0, [os.getpid()]))
        wait_for(self, lambda: not self.center.follow.present(), "the control center to be on its own again")
        self.assertEqual(self.get("/api/state")["run"]["phase"], "idle")
        shown = [i.get("tech") for i in self.center.server.history]
        self.assertIn("READY Everything works on the Mac side. Now connect the console. Stop with the dashboard's Stop, menu 2 or "
                      "nx_spoof.py stop.", shown)
        self.assertIn("SETTINGS settings.json changed: ftp.port 5000 -> 2121 (applies now)", shown)
        self.assertIn("Stopped.", shown)
        self.assertNotIn(("browser", "http://localhost:%d/" % CENTER_PORT), self.system.opened[1:], "no second browser tab")

    # The page closed during a run: the dashboard closes, the run goes on; a dashboard opened again shows it from its first
    # line, and stops it.
    def test_close_keep_and_reopen(self):
        run, ctx, out, held = self.make_run_with_dashboard()
        result = {}
        thread = threading.Thread(target=lambda: result.update(code=run.execute()), daemon=True)
        thread.start()
        wait_for(self, lambda: self.get("/api/state")["run"]["phase"] == "running", "the run to be running")
        self.assertTrue(self.center.close_keeping_run("its page was closed"))
        self.thread.join(10)
        self.assertTrue(thread.is_alive(), "the run keeps going")
        self.assertNotIn("a run is still active", read_text(self.center.logfile.path))
        again = Center(self.store, "settings.json loaded", self.system, Places.together(self.dir, self.dir), stream=self.devnull)
        again_thread = threading.Thread(target=again.run, kwargs={"signals_on": False}, daemon=True)
        again_thread.start()
        try:
            wait_for(self, lambda: again.follow.present(), "the run to show up on the new dashboard")
            wait_for(self, lambda: any(i.get("kind") == "run.log" for i in again.server.history), "the run's first line")
            self.system.stop_run = lambda pid: ctx.request_stop("SIGTERM")
            status, _ = center_link.http_json(CENTER_PORT, "/api/action/stop", {}, token=again.server.token)
            self.assertEqual(status, 200)
            thread.join(10)
            self.assertEqual(result.get("code"), 0)
        finally:
            held.close()
            again.request_stop("test")
            again_thread.join(5)

    # A Run on this project, on the fake Mac, as nx_spoof.py start leaves it: the lock held, run.json written as it goes.
    # Returns (run, its context, its Terminal output, the lock).
    def make_run_with_dashboard(self):
        from nxspoof.app import cli
        from nxspoof.app.config import build_config
        from nxspoof.app.context import Context
        from nxspoof.app.run import Run
        from nxspoof.app.start import where
        from nxspoof.core.events import EventBus
        import io
        args = cli.parse_args(["--settings", os.path.join(self.dir, "settings.json"), "--dns-port", "5394", "--http-port", "8094", "--console", "technical"])
        store = SettingsStore(args.settings)
        message = store.load()
        bus = EventBus()
        places = Places.together(self.dir, self.dir)
        ctx = Context(bus, build_config(store, args), store, self.system, places)
        out = io.StringIO()
        held = runfile.take(self.dir)
        run = Run(ctx, message, stream=out, is_tty=False, where=lambda info: where(places, self.system, info),
                  stop_asked=lambda: runfile.stop_asked(self.dir))
        return run, ctx, out, held

# The dashboard's file on its own: the remembered port.
class DashFile(unittest.TestCase):
    # 8800 the first time; opened: the port and the process id; closed: the port stays; a file nobody answers isn't a dashboard.
    def test_file(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, folder)
        self.assertEqual(center_link.remembered_port(folder), 8800)
        center_link.opened(folder, 8812)
        self.assertEqual(center_link.read_file(folder), {"port": 8812, "pid": os.getpid()})
        self.assertIsNone(center_link.find(folder), "nobody answers on 8812")
        center_link.closed(folder)
        self.assertEqual((center_link.read_file(folder), center_link.remembered_port(folder)), ({"port": 8812}, 8812))

    # Every button is the dashboard's own (D30: the run has none); there's no Close any more (the dashboard closes with its
    # page, app/pagewatch.py), and no way for a run to register.
    def test_buttons_are_the_dashboards(self):
        self.assertFalse(hasattr(Center, "close") or hasattr(Center, "register") or hasattr(center_app, "CENTER_ONLY"))

if __name__ == "__main__":
    unittest.main()
