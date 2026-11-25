"""
test_app.py - unit tests for nxspoof.app: the run (start sequence and every ending), signals,
commands and options.

Runs use a fake Mac (FakeSystem below): sharing "turns on", the hotspot is "found", pf "loads"
(counters from tests/fixtures/macos/pfctl_vsr.txt), and the servers bind 127.0.0.1 on high ports.
The whole old-vs-new session comparison is tools/simulate.py (too slow for unit tests).
"""

import collections
import io
import json
import os
import shutil
import signal
import socket
import tempfile
import threading
import time
import unittest

from nxspoof.app import cli, main as main_module, signals, starter
from nxspoof.app.config import build_config
from nxspoof.app.context import Context
from nxspoof.app.run import Run
from nxspoof.app.system import System
from nxspoof.core.events import EventBus
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.core.places import Places
from nxspoof.output import datafolder, runfile
from nxspoof.output.colors import strip as c_strip
from nxspoof.features import runlog
from nxspoof.macos.firewall import FirewallError
from nxspoof.net.policy import Decision
from nxspoof.settings.store import SettingsStore
from tests.marks import unmarked_outcomes, unmarked_references

CableStatus = collections.namedtuple("CableStatus", "ok")

FIXTURES = os.path.join(PROJECT_DIR, "tests", "fixtures")
MAC_IP = "192.168.2.1"
DNS_PORT, HTTP_PORT = 5396, 8096
PF_ANCHOR = "com.apple/999.nxspoof"
S2 = "48:f1:eb:5a:22:c2"
TEST2 = Decision("test", "connection test", "Switch 2 connection test")
BLOCK = Decision("block", "default", None)

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A finished pfctl call.
class PfResult(object):
    # returncode 0 and the given output.
    def __init__(self, stdout=""):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""

# pfctl without root: rules "load", pf is "on", counters from the real-format sample.
def fake_pfctl(*args, stdin=None):
    counters = read_text(os.path.join(FIXTURES, "macos", "pfctl_vsr.txt"))
    answers = {("-a", PF_ANCHOR, "-vsr"): counters,
               ("-a", PF_ANCHOR, "-sr"): "".join(l + "\n" for l in counters.splitlines() if not l.startswith(" ")),
               ("-s", "info"): "Status: Enabled\n", ("-sr",): 'anchor "com.apple/*" all\n'}
    return PfResult(answers.get(args, ""))

# A monitor that does nothing.
class NoMonitor(object):
    # Nothing to start.
    def start(self):
        pass

    # Nothing to stop.
    def stop(self):
        pass

# The fake Mac; each test can change one behavior.
class FakeSystem(System):
    # Sharing off, hotspot found at once, root, fake pfctl; calls are recorded.
    def __init__(self, leases_path):
        System.__init__(self, pfctl=fake_pfctl, leases_path=leases_path)
        self.hotspot = ("bridge100", MAC_IP)
        self.calls = []
        self.root = True
        self.counters_fail = False
        self.cable = "already"               # what plugging the virtual cable in finds ("already" in, or "plugged" just now)
        self.hotspot_now = {"name": "NXSPOOF", "channel": 11, "security": "WPA2/WPA3 Personal", "password": True}

    # Root or not, as set by the test.
    def is_root(self):
        return self.root

    # The interfaces of a made-up Mac whose hotspot is up.
    def interfaces(self):
        return ["bridge0: UP, members en1 feth0", "bridge100: UP, active, 192.168.2.1, members en0 feth1", "en0: UP, active"]

    # No idle sleep, as if held (nothing started: the Mac running the tests may sleep as it likes).
    def keep_awake(self, pid):
        return "awake"

    # Let go of it.
    def let_sleep(self, handle):
        return None

    # A made-up Mac (the tests never read this Mac's own facts).
    def machine_facts(self):
        return {"macos": "15.0", "build": "24A000", "model": "Mac00,0", "chip": "Apple M0", "arch": "arm64", "rosetta": "no",
                "python": "3.9.6", "python_path": "/usr/bin/python3", "language": "en (region en_US)", "managed": "no"}

    # The hotspot as the default settings want it (the tests never read this Mac's own).
    def hotspot_settings(self):
        return dict(self.hotspot_now)

    # Off at start.
    def sharing_running(self):
        return False

    # Record and "work".
    def set_sharing(self, enabled):
        self.calls.append(("sharing", enabled))
        return "enabled=%s [test]" % enabled

    # The virtual cable: as set by the test (this Mac's own is never looked at).
    def plug_cable(self):
        if self.cable == "plugged":
            self.calls.append(("cable in",))
        return self.cable, CableStatus(True)

    # Record it.
    def unplug_cable(self):
        self.calls.append(("cable out",))

    # This Mac is ready (setup's checks are never run on this Mac in tests).
    def mac_problems(self, program, places):
        return []

    # Stay in this process: the start never runs again from a program copy in tests.
    def restart_from(self, folder, argv):
        return None

    # No fork in tests: the start's run happens here, and nobody waits for its answer.
    def in_background(self, output):
        return "worker", None, None

    # The hotspot (or None to keep waiting).
    def find_sharing_interface(self):
        return self.hotspot

    # Bind 127.0.0.1.
    def bind_address(self, ip):
        return "127.0.0.1"

    # No kernel log in tests.
    def wifi_monitor(self, tracker):
        self.tracker = tracker
        return NoMonitor()

    # Counters, or a failure when the test asks for one.
    def firewall_counters(self):
        if self.counters_fail:
            raise FirewallError("pfctl exploded")
        return System.firewall_counters(self)

    # Record the flush.
    def firewall_flush(self):
        self.calls.append(("flush",))

    # Nothing to open.
    def open_sharing_settings(self):
        self.calls.append(("open settings",))

    # No program of this Mac is using a console's server in tests.
    def connected_to(self, ip, port):
        return False

# A temporary project folder with the real settings (copy) and an empty lease file.
class RunTest(unittest.TestCase):
    # The folder and the fake Mac.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")
        shutil.copy(os.path.join(FIXTURES, "settings_current.json"), os.path.join(self.dir, "settings.json"))
        open(os.path.join(self.dir, "leases"), "w").close()
        self.system = FakeSystem(os.path.join(self.dir, "leases"))

    # Remove the folder.
    def tearDown(self):
        shutil.rmtree(self.dir)

    # A Run with options (technical Terminal into a string); returns (run, context, terminal).
    def make_run(self, *options):
        args = cli.parse_args(["--settings", os.path.join(self.dir, "settings.json"), "--dns-port", str(DNS_PORT),
                               "--http-port", str(HTTP_PORT), "--console", "technical"] + list(options))
        store = SettingsStore(args.settings)
        message = store.load()
        bus = EventBus()
        ctx = Context(bus, build_config(store, args), store, self.system, Places.together(self.dir, self.dir))
        out = io.StringIO()
        self.events = []
        bus.add_sink(self.events.append)
        run = Run(ctx, message, stream=out, is_tty=False)
        run.POLL = 0.05
        return run, ctx, out

    # Run in the background, call `when_ready(ctx)` once the phase is reached, wait for the end; returns (code, lines).
    def play(self, run, ctx, out, phase="running", action=None, timeout=10):
        result = {}
        thread = threading.Thread(target=lambda: result.update(code=run.execute()), daemon=True)
        thread.start()
        end = time.time() + timeout
        while ctx.phase != phase and thread.is_alive() and time.time() < end:
            time.sleep(0.02)
        if thread.is_alive():
            if phase == "running":
                while "READY" not in out.getvalue() and "PROBLEM" not in out.getvalue() and time.time() < end:
                    time.sleep(0.02)
            (action or (lambda c: c.request_stop("ctrl-c")))(ctx)
        thread.join(timeout)
        self.assertFalse(thread.is_alive(), "the run didn't end")
        return result.get("code"), [l[11:] for l in out.getvalue().splitlines()]

# Every real run plugs the virtual cable in itself (D30), and takes it out at the end when it put it in.
class TheCable(RunTest):
    # The cable goes in before anything else, the START lines say so, and it comes out after sharing goes off.
    def test_plugs_the_cable(self):
        self.system.cable = "plugged"
        run, ctx, out = self.make_run()
        code, lines = self.play(run, ctx, out)
        self.assertEqual(code, 0)
        start = [l for l in lines if l.startswith("START ")]
        self.assertEqual(start, ["START setup's checks: this Mac is ready",
                                 "START virtual cable: plugged in just now (taken out again when NXSPOOF stops)"])
        self.assertIn("READY Everything works on the Mac side. Now connect the console. Stop with the dashboard's Stop, menu 2 or "
                      "nx_spoof.py stop.", lines)
        self.assertEqual([e.data for e in self.events if e.kind == "run.preflight"], [{"doctor": "ready"}, {"cable": "plugged"}])
        self.assertEqual(self.system.calls, [("cable in",), ("sharing", True), ("flush",), ("sharing", False), ("cable out",)])
        self.assertIn("SHARING virtual cable taken out (this run had put it in)", lines)

    # A cable that was already in stays in; keep_sharing keeps a cable this run put in.
    def test_cable_already_in_stays(self):
        run, ctx, out = self.make_run()
        self.play(run, ctx, out)
        self.assertNotIn(("cable out",), self.system.calls)
        self.setUp()
        self.system.cable = "plugged"
        run, ctx, out = self.make_run("--keep-sharing")
        self.play(run, ctx, out)
        self.assertNotIn(("cable out",), self.system.calls)

    # A cable that can't go in stops the start with a FATAL line (and the usual clean shutdown).
    def test_cable_fails(self):
        # The cable that won't go in.
        def broken():
            raise OSError("couldn't add feth0 to bridge0: busy")
        self.system.plug_cable = broken
        run, ctx, out = self.make_run()
        code, lines = self.play(run, ctx, out)
        self.assertEqual(code, 1)
        self.assertIn("FATAL virtual cable: couldn't plug it in (OSError: couldn't add feth0 to bridge0: busy) - Internet Sharing can't start without it; "
                      "try Set up this Mac", lines)
        self.assertEqual(lines[-1], "Stopped.")

    # A dry run (--ip) never touches the cable.
    def test_dry_run_leaves_the_cable(self):
        self.system.plug_cable = lambda: self.fail("the run plugged the cable in")
        run, ctx, out = self.make_run("--ip", "127.0.0.1")
        self.assertEqual(self.play(run, ctx, out)[0], 0)

# The start sequence and the normal stop.
class StartAndStop(RunTest):
    # Everything in today's order, then the ordered shutdown; the log file has the same lines.
    def test_full_run(self):
        run, ctx, out = self.make_run()
        code, lines = self.play(run, ctx, out)
        self.assertEqual(code, 0)
        heads = [l.split("  ")[0] for l in lines]
        expected_order = ["SHARING turning Internet Sharing ON: enabled=True [test]", "Looking for the Internet Sharing Wi-Fi network (bridge100)...",
                          "Found Internet Sharing on bridge100, Mac address on that network is 192.168.2.1",
                          "DNS   listening on 192.168.2.1:%d (only the 2 test names answered, everything else BLOCKED)" % DNS_PORT,
                          "FIREWALL ON", "HTTP  listening on 192.168.2.1:%d (only the 2 test names answered)" % HTTP_PORT,
                          "WIFI  watching for consoles joining the Wi-Fi and getting an address",
                          "READY Everything works on the Mac side. Now connect the console. Stop with the dashboard's Stop, menu 2 or "
                          "nx_spoof.py stop.", "Stopping...",
                          "pf    firewall rules removed", "SUMMARY no console connected during this run",
                          "SHARING turned Internet Sharing OFF - your Mac goes back to your home Wi-Fi", "Stopped."]
        positions = [next(i for i, l in enumerate(lines) if l.startswith(e.split("  ")[0] if e.startswith("FIREWALL") else e))
                     for e in expected_order]
        self.assertEqual(positions, sorted(positions), heads)
        self.assertEqual(sum(1 for l in lines if l.startswith("CHECK") and " PASS " in l), 6)
        self.assertIn("pf          42 packets  pass in quick on bridge100 inet proto tcp from any to 192.168.2.1 port = 80 flags S/SA keep state", lines)
        self.assertIn("SUMMARY firewall: allowed DHCP 6  DNS 14  web test 42   ||   IPv6 (blocked) 0  everything else (blocked) 0", lines)
        self.assertEqual(self.system.calls, [("sharing", True), ("flush",), ("sharing", False)])
        log = read_text(os.path.realpath(os.path.join(self.dir, "last_run.log")))
        self.assertEqual([l[11:] for l in log.splitlines()], lines)
        self.assertEqual(ctx.phase, "stopped")

    # A console getting an address is tracked by the run (DHCP monitor -> tracker -> FTP watcher).
    def test_console_traffic(self):
        run, ctx, out = self.make_run("--no-ftp-watch")

        # Once READY: a DHCP lease and a Wi-Fi join, then stop.
        def console(c):
            run.tracker.on_dhcp("192.168.2.3", S2)
            self.system.tracker.feed("kernel SoftAP Client [%s] interface 1" % S2, time.time())
            c.request_stop("ctrl-c")
        _, lines = self.play(run, ctx, out, action=console)
        self.assertIn("DHCP  gave address 192.168.2.3 to Switch 2 (48:f1:eb:5a:22:c2)", lines)
        self.assertIn("WIFI  Switch 2 (48:f1:eb:5a:22:c2) joined the Wi-Fi", lines)
        self.assertIn("SUMMARY Switch 2  48:f1:eb:5a:22:c2  192.168.2.3", lines)
        self.assertEqual(unmarked_references(self.events, ctx.registry), [])
        self.assertEqual(unmarked_outcomes(self.events), [])
        console_line = [e for e in self.events if e.tech and e.tech.startswith("console Switch 2")][0]
        self.assertEqual([console_line.technical_line()[s:e] for s, e, _ in console_line.technical_names()], ["Switch 2"])

    # The structured twin (logs/<run>.jsonl): every event, lines and facts; the checklist read from it is the one read
    # from the text, item for item (so the twin carries everything the checks used to find in the wording).
    def test_structured_twin(self):
        run, ctx, out = self.make_run("--no-ftp-watch")

        # Once READY: a console arrives, passes, is blocked, leaves and comes back; then a Stop (SIGTERM).
        def console(c):
            run.tracker.on_dhcp("192.168.2.3", S2)
            run.tracker.on_dns("192.168.2.3", "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, "192.168.2.1")
            run.tracker.on_http("192.168.2.3", "GET", "ctest.p01.ctest.srv.nintendo.net", "/", "libcurl (nnNifm)", TEST2)
            run.tracker.on_dns("192.168.2.3", "dauth.p01.lp1.ndas.srv.nintendo.net", "A", BLOCK, None)
            run.tracker.on_wifi_leave(S2, "disconnected", time.time() - 5, time.time())
            run.tracker.on_dhcp("192.168.2.3", S2)
            c.request_stop("SIGTERM")
        self.play(run, ctx, out, action=console)
        log = os.path.realpath(os.path.join(self.dir, "last_run.log"))
        twin = runlog.load_records(log)
        self.assertTrue(os.path.isfile(log[:-4] + ".jsonl"))
        kinds = [r["kind"] for r in twin]
        for kind in ("run.version", "selftest.check", "run.ready", "firewall.on", "console.address", "console.online", "console.blocked",
                     "console.left", "console.joined", "run.stopping", "firewall.removed", "summary.run", "summary.console", "sharing.off", "run.stopped"):
            self.assertIn(kind, kinds)
        self.assertEqual([runlog.line_of(r) for r in runlog.lines_only(twin)], [l[11:] for l in read_text(log).splitlines()],
                         "the twin's lines are the text log's lines")
        from_twin = [(i.status, i.title) for i in runlog.check_records(twin)]
        self.assertEqual(from_twin, [(i.status, i.title) for i in runlog.check_log(read_text(log))])
        self.assertIn(("pass", "stopped cleanly (a Stop (the dashboard's, menu 2 or nx_spoof.py stop))"), from_twin)
        self.assertIn(("pass", "a console left the Wi-Fi and came back"), from_twin)
        back = [i for i in runlog.check_records(twin) if i.title.startswith("a console left")][0]
        self.assertEqual(back.evidence, ["Switch 2 came back"], "named, not by MAC")
        info = runlog.run_info(os.path.basename(log), twin)
        self.assertEqual((info["consoles"], info["stopped"], info["new_code"]), ([("Switch 2", S2)], "a Stop (the dashboard's, menu 2 or nx_spoof.py stop)", True))
        events = runlog.run_events(twin, read_text(log))              # the page draws a past run like the live log
        live = [e.casual for e in self.events if e.casual]                  # (the first and last lines come before / after the log file)
        self.assertTrue(set(e["casual"] for e in events if e["casual"]) <= set(live) and len([e for e in events if e["casual"]]) >= len(live) - 2)
        named = [e for e in events if e["casual"] and e["casual_names"]][0]
        s, e, who = named["casual_names"][0]
        self.assertEqual((named["casual"][s:e], who), ("Switch 2", S2))

    # D29: the hotspot vanishes mid-run: a warning, the cable and sharing put back, the self-test again, back; when it doesn't
    # come back, a PROBLEM once after the listed tries, and the tries go on (D30; the watch's own thread is left out: its steps
    # are called here, in order).
    def test_hotspot_watch(self):
        from nxspoof.app import hotspotwatch
        run, ctx, out = self.make_run("--no-ftp-watch")
        watch = hotspotwatch.HotspotWatch(run, sleep=lambda s: None)
        run.on_ready.remove(watch.start)
        plugged, result = [], {}
        self.system.plug_cable = lambda: plugged.append(1) or ("plugged", CableStatus(True))
        original = self.system.set_sharing

        # Drop the hotspot twice: once it comes back with sharing, once it never does.
        def drop(c):
            del plugged[:]                   # the start's own plug doesn't count
            self.system.hotspot = None
            self.system.set_sharing = lambda enabled: setattr(self.system, "hotspot", ("bridge100", MAC_IP)) or original(enabled)
            self.assertFalse(watch.up())
            result["back"], result["down_after"] = watch.recover(), run.hotspot_down
            self.system.hotspot, self.system.set_sharing = None, original
            old, hotspotwatch.RETRY_AFTER = hotspotwatch.RETRY_AFTER, (0, 0)
            old_every, hotspotwatch.THEN_EVERY = hotspotwatch.THEN_EVERY, 0
            try:
                result["lost"] = watch.recover(max_tries=4)
            finally:
                hotspotwatch.RETRY_AFTER, hotspotwatch.THEN_EVERY = old, old_every
            c.request_stop("SIGTERM")
        _, lines = self.play(run, ctx, out, action=drop)
        self.assertEqual((result["back"], result["down_after"], result["lost"], len(plugged)), (True, False, False, 5))
        self.assertIn("SHARING the hotspot went DOWN (Internet Sharing went off) - putting it back", lines)
        self.assertIn("SHARING try 1: virtual cable plugged back in", lines)
        self.assertIn("SHARING try 1: Internet Sharing turned ON again", lines)
        self.assertIn("SHARING the hotspot is UP again (%s, try 1); the self-test passed" % MAC_IP, lines)
        self.assertIn("PROBLEM the hotspot is still down after 2 tries - trying again every 0 s until it's back (Stop NXSPOOF to give up)", lines)
        self.assertIn("SHARING try 4: Internet Sharing turned ON again", lines)                  # it never gives up by itself (D30)
        kinds = [e.kind for e in self.events]
        self.assertEqual([k for k in kinds if k.startswith("hotspot.")], ["hotspot.down", "hotspot.back", "hotspot.down", "hotspot.lost"])

    # One run at a time: while a run holds the lock (output/runfile.py), a new start refuses before anything (no keeper, no
    # log, no sharing); a free lock and a stale run.json mean nothing.
    def test_refuses_when_another_run_is_active(self):
        import contextlib
        from nxspoof.output import runfile
        held = runfile.take(self.dir)
        runfile.write(self.dir, {"pid": 4242})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main_module.main(["start", "--settings", os.path.join(self.dir, "settings.json")], system=self.system, project_dir=self.dir)
        self.assertEqual(code, 1)
        self.assertIn("NXSPOOF is already running (process 4242): nothing done.", out.getvalue())
        self.assertEqual(self.system.calls, [], "nothing was touched")
        held.close()
        self.assertEqual((runfile.held(self.dir), runfile.read(self.dir)), (False, {}))

    # start --fix: setup's fixes run before the lock is taken (setup waits while it's held), then the start goes on.
    def test_fix_then_start(self):
        import contextlib
        seen, problems = [], [[{"name": "bridge", "title": "Thunderbolt Bridge", "tech": "no fixed address", "fixable": True}], []]
        self.system.mac_problems = lambda program, places: problems.pop(0)
        self.system.mac_fix = lambda places, names: seen.append((names, runfile.held(places.runtime)))
        self.system.in_background = lambda output: ("waiter", None, None)           # the run's side isn't played here
        self.system.wait_answer = lambda fd, timeout: {"ok": True, "text": "Connect a Switch."}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main_module.main(["start", "--fix", "--settings", os.path.join(self.dir, "settings.json")], system=self.system, project_dir=self.dir)
        self.assertEqual((code, seen), (0, [(["bridge"], False)]), out.getvalue())
        self.assertIn("setup fixes bridge first", out.getvalue())

    # This Mac not ready: the reasons and exit 2 (the menu then offers --anyway), nothing touched; --anyway starts.
    def test_refuses_when_not_ready(self):
        import contextlib
        self.system.mac_problems = lambda program, places: [{"title": "Thunderbolt Bridge", "tech": "no fixed address"}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main_module.main(["start", "--settings", os.path.join(self.dir, "settings.json")], system=self.system, project_dir=self.dir)
        self.assertEqual(code, 2)
        self.assertIn("  - Thunderbolt Bridge: no fixed address", out.getvalue())
        self.assertEqual(self.system.calls, [])

    # keep_sharing: sharing stays on, and casual says so.
    def test_keep_sharing(self):
        run, ctx, out = self.make_run("--keep-sharing")
        _, lines = self.play(run, ctx, out)
        self.assertEqual(self.system.calls, [("sharing", True), ("flush",)])
        self.assertNotIn("SHARING turned Internet Sharing OFF - your Mac goes back to your home Wi-Fi", lines)

    # Not root: sharing is left to you, and never turned off by us.
    def test_not_root(self):
        self.system.root = False
        run, ctx, out = self.make_run()
        _, lines = self.play(run, ctx, out)
        self.assertIn("SHARING not running as root - turn Internet Sharing ON by hand", lines)
        self.assertNotIn(("sharing", False), self.system.calls)

    # Every run says what kind of Mac it's on (one line, and the fact run.mac with each fact).
    def test_this_mac_line(self):
        run, ctx, out = self.make_run("--no-ftp-watch")
        self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        line = [e for e in self.events if e.kind == "run.mac"][0]
        self.assertEqual(line.technical_line(), "LOG   this Mac: macOS 15.0 (24A000), model Mac00,0, Apple M0, arm64, Python 3.9.6 (/usr/bin/python3), "
                         "language en (region en_US), managed by an organization: no")
        self.assertEqual(line.data["model"], "Mac00,0")

    # A run keeps the Mac awake (said, in casual words too) and lets go at its end; --allow-sleep says so instead.
    def test_keep_awake(self):
        let_go = []
        self.system.let_sleep = let_go.append
        run, ctx, out = self.make_run("--no-ftp-watch")
        self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        awake = [e for e in self.events if e.kind == "run.awake"]
        self.assertEqual(len(awake), 1)
        self.assertIn("keeping this Mac awake while NXSPOOF runs", awake[0].tech)
        self.assertTrue(awake[0].casual.startswith("☕"))
        self.assertEqual(let_go, ["awake"])
        run, ctx, out = self.make_run("--no-ftp-watch", "--allow-sleep")
        _, lines = self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        self.assertIn("LOG   this Mac may sleep during the run (--allow-sleep): if it does, the hotspot sleeps with it", lines)
        self.assertEqual(let_go, ["awake", None])

    # The Mac slept (the wall clock jumped 10 minutes, the awake clock didn't): the run stops cleanly at the wake, says why
    # (a NOTE with how long, in casual words too), resumes nothing, and the checklist calls it a clean stop.
    def test_sleep_stops_the_run(self):
        run, ctx, out = self.make_run("--no-ftp-watch")
        jumped = {"by": 0.0}
        run.clocks = (lambda: time.time() + jumped["by"], time.time)
        code, lines = self.play(run, ctx, out, action=lambda c: jumped.update(by=600.0))
        self.assertEqual(code, 0)
        slept = [e for e in self.events if e.kind == "run.slept"]
        self.assertEqual(len(slept), 1)
        self.assertIn("this Mac slept (about 10m 00s) - stopping: a sleep ends the run", slept[0].tech)
        self.assertTrue(slept[0].casual.startswith("😴"))
        self.assertEqual(ctx.stop_reason, "slept")
        self.assertEqual(lines[-1], "Stopped.")
        clean = [i for i in runlog.check_run(run.log_path) if i.title.startswith("stopped cleanly")]
        self.assertEqual([(i.status, i.title) for i in clean], [("pass", "stopped cleanly (the Mac slept: a sleep ends the run)")])

    # A hotspot that never comes up: FATAL, with the network interfaces as macOS has them (another macOS may name them otherwise).
    def test_hotspot_never_up(self):
        self.system.hotspot = None
        run, ctx, out = self.make_run("--no-ftp-watch")
        code, _ = self.play(run, ctx, out, phase="stopped", timeout=20)
        self.assertEqual(code, 1)
        fatal = [e for e in self.events if e.level == "fatal"][0]
        self.assertIn("the hotspot didn't come up within 60 s", fatal.tech)
        self.assertEqual(fatal.details[0], "the network interfaces now (a hotspot is a bridge1xx with an address):")
        self.assertIn("bridge100: UP, active, 192.168.2.1, members en0 feth1", fatal.details)

    # Start reads the hotspot as macOS has it (never changes it) and logs it; a name that isn't the last run's is a warning.
    def test_hotspot_read_at_start(self):
        self.system.hotspot_now = {"name": "Old Mac", "channel": 6, "security": "WPA2/WPA3 Personal", "password": True}
        run, ctx, out = self.make_run("--no-ftp-watch")
        self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        log = open(run.log_path, encoding="utf-8").read()
        self.assertIn('SHARING hotspot: "Old Mac", channel 6, WPA2/WPA3 Personal, password set', log)
        self.assertNotIn("name changed", log)
        self.assertEqual(datafolder.before(self.dir), {})
        time.sleep(1.1)                                              # the next run's log gets its own name (one per second)
        self.system.hotspot_now = dict(self.system.hotspot_now, name="Living room")
        run, ctx, out = self.make_run("--no-ftp-watch")
        _, lines = self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        log = open(run.log_path, encoding="utf-8").read()
        self.assertIn('SHARING the hotspot\'s name changed: "Living room" now, "Old Mac" at the last run - each Switch has to join "Living room"', log)
        rows = [{"name": "hotspot", "explain": []}]
        starter.renamed_note(rows, self.system, os.path.dirname(run.log_path))   # This Mac: the same name as the last run, no note
        self.assertEqual(rows[0]["explain"], [])
        self.system.hotspot_now["name"] = "Kitchen"
        starter.renamed_note(rows, self.system, os.path.dirname(run.log_path))
        self.assertEqual(rows[0]["explain"], ['Its name changed since the last run (it was "Living room"): each Switch has to join "Kitchen", with its password.'])

    # A dry run (--ip): no sharing, no firewall, no monitors.
    def test_dry_run(self):
        run, ctx, out = self.make_run("--ip", "127.0.0.1", "--answer-ip", MAC_IP)
        code, lines = self.play(run, ctx, out)
        self.assertEqual(code, 0)
        self.assertIn("NOTE  dry run on 127.0.0.1: firewall not loaded (only used on the real hotspot)", lines)
        self.assertEqual(self.system.calls, [])
        self.assertTrue(os.path.realpath(os.path.join(self.dir, "last_run.log")).endswith("-dryrun.log"))

# Every other ending goes through the same protected shutdown.
class Endings(RunTest):
    # Stopped while waiting for the hotspot: sharing off, no summary.
    def test_stop_while_waiting(self):
        self.system.hotspot = None
        run, ctx, out = self.make_run()
        code, lines = self.play(run, ctx, out, phase="waiting")
        self.assertEqual(code, 0)
        self.assertIn("...not found yet. Is Internet Sharing ON with Wi-Fi ticked? (still waiting, up to 60 s)", lines)
        self.assertEqual(lines[-3:], ["Stopping...", "SHARING turned Internet Sharing OFF - your Mac goes back to your home Wi-Fi", "Stopped."])
        self.assertFalse(any(l.startswith("SUMMARY") for l in lines))

    # DNS port taken: FATAL, exit 1, sharing still turned off, "Stopped.".
    def test_dns_port_taken(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", DNS_PORT))
        try:
            run, ctx, out = self.make_run()
            code, lines = self.play(run, ctx, out)
        finally:
            blocker.close()
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("FATAL cannot start DNS on 192.168.2.1:%d" % DNS_PORT) for l in lines))
        self.assertEqual(lines[-2:], ["SHARING turned Internet Sharing OFF - your Mac goes back to your home Wi-Fi", "Stopped."])
        self.assertNotIn(("flush",), self.system.calls)

    # Web port taken after the firewall loaded: the firewall is still cleaned up.
    def test_web_port_taken(self):
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", HTTP_PORT))
        blocker.listen(1)
        try:
            run, ctx, out = self.make_run()
            code, lines = self.play(run, ctx, out)
        finally:
            blocker.close()
        self.assertEqual(code, 1)
        self.assertIn("      Something else uses port %d. Find it with: sudo lsof -nP -iTCP:%d" % (HTTP_PORT, HTTP_PORT), lines)
        self.assertIn("pf    firewall rules removed", lines)
        self.assertEqual(self.system.calls, [("sharing", True), ("flush",), ("sharing", False)])

    # A failing shutdown step is logged and the next steps still run.
    def test_failing_step_doesnt_skip_the_rest(self):
        self.system.counters_fail = True
        run, ctx, out = self.make_run()
        _, lines = self.play(run, ctx, out)
        self.assertIn("NOTE  cleanup step failed: FirewallError: pfctl exploded", lines)
        self.assertIn(("flush",), self.system.calls)
        self.assertIn("SUMMARY no console connected during this run", lines)
        self.assertEqual(lines[-1], "Stopped.")

    # A Stop is a SIGTERM (the dashboard's, menu 2, nx_spoof.py stop, D30), said as such; another signal is named.
    def test_signal_reason(self):
        run, ctx, out = self.make_run()
        _, lines = self.play(run, ctx, out, action=lambda c: c.request_stop("SIGTERM"))
        i = lines.index("NOTE  stop requested (SIGTERM: the dashboard's Stop, menu 2 or nx_spoof.py stop) - cleaning up")
        self.assertEqual(lines[i + 1], "Stopping...")
        run, ctx, out = self.make_run()
        _, lines = self.play(run, ctx, out, action=lambda c: c.request_stop("SIGHUP"))
        self.assertIn("NOTE  stop signal received (SIGHUP) - cleaning up", lines)

    # The dashboard's ■ Stop as a note only (no signal): the run stops by itself, says who asked; menu 2's note too.
    def test_stop_by_note(self):
        for by, said in (("dashboard", "NOTE  stop requested from the dashboard"), ("stop command", "NOTE  stop requested by nx_spoof.py stop (menu 2)")):
            notes = [by]
            run, ctx, out = self.make_run()
            run.stop_asked = lambda: notes.pop() if notes and ctx.phase == "running" else None
            _, lines = self.play(run, ctx, out, action=lambda c: None)
            self.assertIn(said, lines)
            self.assertEqual(ctx.stop_reason, by)

    # The run talks to nobody (D30): no controls of its own; what the dashboard shows is run_state.json while it runs (gone
    # at the end); settings.json changed by the dashboard is read again and each difference said, as the fact it is.
    def test_state_file_and_settings_watch(self):
        run, ctx, out = self.make_run()
        seen = {}

        # While running: the state file, then a change to settings.json (as the dashboard saves it).
        def during(c):
            held = runfile.take(self.dir)                # the start holds the lock for a real run
            run.statefile.write()
            seen["state"] = runfile.read_state(self.dir)
            path = os.path.join(self.dir, "settings.json")
            doc = json.loads(read_text(path))
            doc["ftp"]["port"] = 2121
            doc["consoles"][0]["owner"] = "Kim"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f)
            os.utime(path, (time.time() + 5, time.time() + 5))
            seen["changes"] = run.statefile.settings.check()
            held.close()
            c.request_stop("SIGTERM")
        _, lines = self.play(run, ctx, out, action=during)
        self.assertEqual((seen["state"]["run"]["phase"], seen["state"]["actions"], seen["changes"]), ("running", [], 2))
        self.assertEqual([c["name"] for c in seen["state"]["consoles"]], ["Switch 1", "Switch 2"])
        self.assertIn("SETTINGS settings.json changed: ftp.port 5000 -> 2121 (applies now)", lines)
        self.assertIn("SETTINGS settings.json changed: console Switch 1 (owner)", lines)
        self.assertEqual([e.data.get("name") for e in self.events if e.kind == "settings.changed"], ["ftp.port"])
        self.assertEqual([e.mac for e in self.events if e.kind == "console.changed"], ["dc:68:eb:5a:11:c1"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, runfile.STATE)), "removed at the end")

# A run in the background (app/background.py, D28): stop, and following a run from its records.
class Background(unittest.TestCase):
    # stop: the run named in run.json gets the request, then stop waits for the lock to go; no run: said; a root run: said.
    def test_stop(self):
        from nxspoof.app import background
        from nxspoof.output import runfile
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, folder)
        places, out = Places.together(folder, folder), io.StringIO()
        self.assertEqual(background.stop_command(None, places, out), 0)
        self.assertIn("isn't running", out.getvalue())
        held, asked = runfile.take(folder), []
        runfile.write(folder, {"pid": 4242})
        sys_ = type("S", (), {"stop_run": lambda self, pid: asked.append(pid) or held.close() or True})()
        out = io.StringIO()
        self.assertEqual(background.stop_command(sys_, places, out, sleep=lambda s: None), 0)
        self.assertEqual((asked, out.getvalue().splitlines()[-1]), ([4242], "NXSPOOF has stopped."))
        held = runfile.take(folder)
        runfile.write(folder, {"pid": 4242})

        # A run that isn't yours (--no-keeper): the signal is refused, the note is left all the same.
        def refuse(self, pid):
            raise PermissionError(1, "not permitted")
        out = io.StringIO()
        self.assertEqual(background.stop_command(type("S", (), {"stop_run": refuse})(), places, out, sleep=lambda s: None), 1)
        self.assertEqual(runfile.stop_asked(folder), "stop command")
        held.close()

    # A Stop's note (stop.json, D30): the run picks it up within half a second, even with no signal at all (on the Mac a
    # signal got lost on the way), and says who asked.
    def test_stop_by_note(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, folder)
        self.assertIsNone(runfile.stop_asked(folder))
        runfile.ask_stop(folder, "dashboard")
        self.assertEqual((runfile.stop_asked(folder), runfile.stop_asked(folder)), ("dashboard", None), "taken once")

    # A killed run never tidies its files: once a new start holds the lock, they're gone, so the dashboard waits for the new
    # run's run.json instead of following the dead run's log (Run I, 2026-11-23).
    def test_new_start_forgets_a_killed_run(self):
        folder = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, folder)
        runfile.write(folder, {"pid": 29958, "records": "2026-11-23_19-11-14.jsonl"})
        runfile.write_state(folder, {"run": {"phase": "running"}})
        runfile.ask_stop(folder, "dashboard")
        lock = runfile.take(folder)
        self.addCleanup(lock.close)
        self.assertEqual(runfile.read(folder)["pid"], 29958, "what a dashboard would have taken for the new run")
        runfile.forget_before(folder)
        self.assertEqual((runfile.read(folder), runfile.read_state(folder), runfile.stop_asked(folder)), ({}, {}, None))

    # One record in each style: technical shows the tag line and its details; casual the friendly line, a problem always.
    def test_lines_for(self):
        from nxspoof.app import background
        r = {"t": "10:00:00", "tag": "DNS", "tech": "Switch 2 asked A x -> BLOCKED", "casual": "🛡️ Blocked", "level": "info",
             "details": ["one"], "marks": [], "casual_marks": []}
        self.assertEqual([c_strip(l) for l in background.lines_for(r, "technical")], ["[10:00:00] DNS   Switch 2 asked A x -> BLOCKED", "[10:00:00]       one"])
        self.assertEqual([c_strip(l) for l in background.lines_for(r, "casual")], ["🛡️ Blocked"])
        warn = dict(r, casual=None, level="warn")
        self.assertIn("Something went wrong", background.lines_for(warn, "casual")[0])
        hint = dict(r, casual="✋ Bridge: needs setup\n   Run setup with admin rights.", level="warn", details=["Run setup with admin rights.", "two"])
        self.assertEqual([c_strip(l) for l in background.lines_for(hint, "casual")],
                         ["✋ Bridge: needs setup\n   Run setup with admin rights.", "   two"], "a detail the line already says isn't shown twice")
        self.assertEqual(background.lines_for(dict(r, tech=None), "technical"), [])

    # follow: the newest run's records, until it stopped.
    def test_follow(self):
        from nxspoof.app import background
        from nxspoof.core.places import Places
        folder = tempfile.mkdtemp(prefix="nxspoof-follow-")
        try:
            logs = os.path.join(folder, "logs")
            os.makedirs(logs)
            open(os.path.join(logs, "2026-11-20_10-00-00.log"), "w").close()
            with open(os.path.join(logs, "2026-11-20_10-00-00.jsonl"), "w") as f:
                f.write(json.dumps({"ts": 1, "kind": "log", "level": "info", "tag": "READY", "tech": "Everything works", "casual": "✅ All set"}) + "\n")
                f.write(json.dumps({"ts": 2, "kind": "run.stopped", "level": "info", "tech": "Stopped."}) + "\n")
            store = SettingsStore(os.path.join(folder, "settings.json"))
            out = io.StringIO()
            code = background.follow_command(Places.together(folder, folder), store, "casual", False, out, sleep=lambda s: None)
            self.assertEqual(code, 0)
            self.assertIn("✅ All set", out.getvalue())
            self.assertIn("(this run has stopped)", out.getvalue())
        finally:
            shutil.rmtree(folder)

# A Context on the real settings (copy) and the fake Mac, with the given options.
def make_context(*options):
    args = cli.parse_args(["--settings", os.path.join(FIXTURES, "settings_current.json")] + list(options))
    store = SettingsStore(args.settings)
    store.load()
    return Context(EventBus(), build_config(store, args), store, FakeSystem("/nonexistent"), Places.together(PROJECT_DIR, PROJECT_DIR))

# Signals, options and commands.
class Commands(unittest.TestCase):
    # A real SIGTERM becomes a stop request; repeats change nothing; handlers are put back.
    def test_signals(self):
        ctx = make_context()
        before = signal.getsignal(signal.SIGTERM)
        previous = signals.install(ctx)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGHUP)
            self.assertTrue(ctx.stopping.wait(2))
        finally:
            signals.restore(previous)
        self.assertEqual(ctx.stop_reason, "SIGTERM")
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    # What a run uses: an option wins; a live setting follows settings.json; a restart setting stays as at start.
    def test_setting_in_force(self):
        ctx = make_context("--no-firewall")
        self.assertEqual((ctx.setting("spoof.firewall"), ctx.overridden_by("spoof.firewall")), (False, "--no-firewall"))
        ctx.store._doc["ftp"]["port"] = 2121
        ctx.store._doc["logs"]["keep_last"] = 7
        ctx.store._recheck()
        self.assertEqual(ctx.setting("ftp.port"), 2121)
        self.assertEqual(ctx.setting("logs.keep_last"), 0)
        self.assertIsNone(ctx.overridden_by("ftp.port"))

    # The command word is optional (start), and every old option still parses.
    def test_cli_commands(self):
        self.assertEqual(cli.parse_args([]).command, "start")
        args = cli.parse_args(["sharing", "on"])
        self.assertEqual((args.command, args.words), ("sharing", ["on"]))
        args = cli.parse_args(["--no-firewall", "--console", "casual"])
        self.assertEqual((args.command, args.no_firewall, args.console, args.argv), ("start", False, "casual", ["--no-firewall", "--console", "casual"]))

    # Option overrides reach the console registry (e.g. --open-filezilla for the FTP watcher).
    def test_overrides_reach_registry(self):
        ctx = make_context("--open-filezilla")
        self.assertTrue(ctx.registry.ftp_settings(S2)["open_filezilla"])

    # `check`: the self-test on 127.0.0.1, no admin rights.
    def test_check_command(self):
        out = io.StringIO()
        code = main_module.main(["check", "--settings", os.path.join(FIXTURES, "settings_current.json")], stream=out)
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(sum(1 for l in out.getvalue().splitlines() if l.startswith("CHECK") and " PASS " in l), 6)

    # `settings export|import`: export always; import refused while a run is active, asked otherwise (or --yes), the old file kept.
    def test_settings_command(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        shutil.copy(os.path.join(FIXTURES, "settings_current.json"), os.path.join(self.dir, "settings.json"))
        system, out = FakeSystem("/nonexistent"), io.StringIO()
        exported = os.path.join(self.dir, "exported.json")
        self.assertEqual(main_module.main(["settings", "export", exported], system=system, project_dir=self.dir, stream=out), 0)
        data = json.loads(open(exported, encoding="utf-8").read())
        data["ftp"]["port"] = 2121
        with open(exported, "w", encoding="utf-8") as f:
            json.dump(data, f)
        held = runfile.take(self.dir)            # a run holds the lock
        self.assertEqual(main_module.main(["settings", "import", exported, "--yes"], system=system, project_dir=self.dir, stream=out), 1)
        held.close()
        self.assertEqual(main_module.main(["settings", "import", exported, "--yes"], system=system, project_dir=self.dir, stream=out), 0)
        self.assertIn("SETTINGS importing %s: 1 setting changed (ftp.port 5000 -> 2121)" % exported, out.getvalue())
        self.assertEqual(json.loads(open(os.path.join(self.dir, "settings.json"), encoding="utf-8").read())["ftp"]["port"], 2121)
        self.assertEqual(len([n for n in os.listdir(self.dir) if n.startswith("settings.json.before-import-")]), 1)
        self.assertEqual(main_module.main(["settings", "nope"], system=system, project_dir=self.dir, stream=out), 2)

    # `cleanup` needs root; as root it flushes leftovers, turns sharing off and takes the cable out.
    def test_cleanup_command(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.addCleanup(shutil.rmtree, self.dir)
        system = FakeSystem("/nonexistent")
        system.root = False
        out = io.StringIO()
        self.assertEqual(main_module.main(["cleanup"], system=system, project_dir=self.dir, stream=out), 1)
        self.assertIn("needs admin rights", out.getvalue())
        system.root = True
        self.assertEqual(main_module.main(["cleanup"], system=system, project_dir=self.dir, stream=out), 0)
        self.assertEqual(system.calls, [("flush",), ("sharing", False), ("cable out",)])
        logs = os.path.join(self.dir, "logs")
        records = [json.loads(l) for n in sorted(os.listdir(logs)) if n.endswith("-cleanup.jsonl") for l in open(os.path.join(logs, n))]
        self.assertEqual([(r["data"]["command"], r["data"]["todo"]) for r in records if r["kind"] == "checklist.done"], [("cleanup", 1), ("cleanup", 0)])
        self.assertEqual([r["data"]["step"] for r in records if r["kind"] == "checklist.cleanup"], ["firewall", "sharing", "cable"])
        held = runfile.take(self.dir)            # a run holds the lock
        self.assertEqual(main_module.main(["cleanup"], system=system, project_dir=self.dir, stream=out), 1)
        held.close()
        self.assertIn("NXSPOOF is running: stop it", out.getvalue())

# pf's token: the real System keeps it out of the status (and the logs) and gives it back with the rules, once.
class PfToken(unittest.TestCase):
    # Loaded twice (the older token given back), flushed: every token given back, pf as it was found.
    def test_token_given_back(self):
        calls, tokens = [], iter(["41", "42"])

        # pfctl, recorded; each -E hands out the next token.
        def pfctl(*args, stdin=None):
            calls.append(args)
            result = PfResult()
            result.stderr = "Token : %s\n" % next(tokens) if args == ("-E",) else ""
            return result
        system = System(pfctl=pfctl)
        status = system.firewall_apply("bridge100", MAC_IP, None, True)
        self.assertNotIn("token", status)
        system.firewall_apply("bridge100", MAC_IP, None, True)
        self.assertIn(("-X", "41"), calls)
        system.firewall_flush()
        system.firewall_flush()                                     # a second flush gives nothing back twice
        self.assertEqual([c for c in calls if c[0] == "-X"], [("-X", "41"), ("-X", "42")])

if __name__ == "__main__":
    unittest.main()
