"""
test_features.py - unit tests for nxspoof.features: services, FileZilla, the casual story, summaries.

The main test replays the reference session through the new pieces (tracker, story, services,
summary) into the new outputs (Terminal in both styles, log file) and compares the result with the
session recording (tests/fixtures/reference/session.*), timestamps included. Only durations are
normalized: the recording's are a fraction of a second off the replay's exact times.
"""

import collections
import io
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from nxspoof.consoles.activity import ConsoleActivity
from nxspoof.consoles.registry import ConsoleRegistry
from nxspoof.consoles.tracker import ConsoleTracker
from nxspoof.core.controls import ControlError, Controls
from nxspoof.core.events import EventBus
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.features import filezilla, sensors, services, story, summary
from nxspoof.net.policy import Decision
from nxspoof.output.logfile import LogFileSink
from nxspoof.output.terminal import TerminalSink
from nxspoof.settings import store
from tests.marks import unmarked_outcomes, unmarked_references

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")
REFERENCE = os.path.join(PROJECT_DIR, "tests", "fixtures", "reference", "session")
S1, S2, UNKNOWN = "dc:68:eb:5a:11:c1", "48:f1:eb:5a:22:c2", "0a:01:02:03:04:05"
S2_IP, MAC_IP = "127.0.0.1", "192.168.2.1"
UA = "libcurl (nnNifm; bf4812d9-d89b-4804-b53a-d08e0bed634f; SDK 23.3.0.0; Add-on 23.3.0.0)"
TEST2 = Decision("test", "connection test", "Switch 2 connection test")
BLOCK = Decision("block", "default", None)
START = time.mktime((2026, 11, 13, 22, 26, 10, 0, 0, -1))           # the reference run started at 22:26:10
PF = collections.OrderedDict([("DHCP", 2), ("DNS", 9), ("web test", 4), ("IPv6 (blocked)", 0), ("everything else (blocked)", 0)])
DURATIONS = [(re.compile(r"\b\d+m \d\ds\b|\b\d+s\b"), "<dur>"), (re.compile(r"\b\d+ seconds?\b"), "<dur>"),
             (re.compile(r"\(\d+:\d\d:\d\d\)"), "(<dur>)")]

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# Replace durations by <dur> in each line.
def normalize(lines):
    out = []
    for line in lines:
        for rx, repl in DURATIONS:
            line = rx.sub(repl, line)
        out.append(line)
    return out

# The registry on the real settings, with the reference run's FTP settings (port 5021, rechecking on).
class ReferenceRegistry(ConsoleRegistry):
    # Load the real settings (read-only copy in tests/fixtures).
    def __init__(self, open_filezilla=False):
        s = store.SettingsStore(FIXTURE)
        s.load()
        ConsoleRegistry.__init__(self, s, mac_ip=MAC_IP)
        self.open_filezilla = open_filezilla

    # FTP settings as in the reference run.
    def ftp_settings(self, mac):
        ftp = ConsoleRegistry.ftp_settings(self, mac)
        ftp.update(port=5021, recheck_when_up_seconds=1, open_filezilla=self.open_filezilla)
        return ftp

# Everything wired on one bus, with a hand-driven clock; outputs captured.
class Rig(object):
    # Build the pieces the way the app will.
    def __init__(self, log_path=None, open_filezilla=False):
        self.now = START
        self.bus = EventBus()
        self.registry = ReferenceRegistry(open_filezilla)
        self.activity = ConsoleActivity(clock=lambda: self.now)
        self.technical, self.casual = io.StringIO(), io.StringIO()
        self.bus.add_sink(TerminalSink("technical", False, self.registry.color_of, self.technical))
        self.bus.add_sink(TerminalSink("casual", False, self.registry.color_of, self.casual))
        if log_path:
            self.bus.add_sink(LogFileSink(log_path))
        self.events = []
        self.bus.add_sink(self.events.append)
        self.tracker = ConsoleTracker(self.bus, self.registry, self.activity, answer_ip=MAC_IP)
        self.story = story.Story(self.bus, self.registry, self.activity, clock=lambda: self.now)
        self.story.attach(ticker=False)
        self.opened = []
        self.services = services.Services(self.bus, self.registry, self.activity, open_filezilla=self.opened.append, background=False)
        self.services.attach()

    # Move the clock to START + seconds.
    def at(self, seconds):
        self.now = START + seconds

    # Play the reference scenario (same observations, at the recording's times) and stop at 22:26:51.
    def play(self):
        with mock.patch("time.time", lambda: self.now):
            t = self.tracker
            self.at(2)
            t.on_dhcp(S2_IP, S2)
            t.on_wifi_join(S2, self.now)
            t.on_dns(S2_IP, "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, MAC_IP)
            t.on_http(S2_IP, "GET", "ctest.p01.ctest.srv.nintendo.net", "/", UA, TEST2)
            self.at(4)
            t.on_dns(S2_IP, "dauth.p01.lp1.ndas.srv.nintendo.net", "A", BLOCK, "0.0.0.0")
            t.on_dns(S2_IP, "sun.p01.lp1.d4c.srv.nintendo.net", "A", BLOCK, "0.0.0.0")
            self.at(5)
            t.on_dns(S2_IP, "dauth.p01.lp1.ndas.srv.nintendo.net", "A", BLOCK, "0.0.0.0")
            self.story.tick()
            t.on_dns(S2_IP, "api.p01.lp1.ctest.srv.nintendo.net", "A", BLOCK, "0.0.0.0")
            self.at(6)
            t.on_http(S2_IP, "GET", "api.github.com", "/repos/x", "Mozilla/5.0 (Nintendo Switch)", BLOCK)
            self.at(10)
            self.story.tick()                                   # 5 blocked now, but it was told less than a minute ago
            ftp = self.services.watchers[S2]
            self.at(12)
            ftp.step(True)
            self.at(15)
            for _ in range(services.MISSES_BEFORE_DOWN):            # down only after a few failed knocks in a row
                ftp.step(False)
            self.at(18)
            t.on_wifi_join(S1, self.now)
            self.at(26)
            t.on_wifi_leave(S1, "disconnected", START + 18, self.now)
            self.at(28)
            t.on_wifi_join(UNKNOWN, self.now)
            self.at(37)
            t.on_wifi_leave(S2, "disconnected", START + 2, self.now)
            self.at(38)
            t.on_wifi_join(S2, self.now)
            t.on_dns(S2_IP, "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, MAC_IP)
            t.on_http(S2_IP, "GET", "ctest.p01.ctest.srv.nintendo.net", "/", UA, TEST2)
            self.at(41)
            summary.report(self.bus, self.activity, self.registry, START, self.now, PF)

# The whole reference session, through the new code.
class ReferenceSession(unittest.TestCase):
    # A temporary folder for the log file.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")

    # Remove it.
    def tearDown(self):
        shutil.rmtree(self.dir)

    # Technical Terminal and log file: the reference session's lines (timestamps included) for every console line and the summary.
    def test_technical_same_as_reference(self):
        log_path = os.path.join(self.dir, "run.log")
        rig = Rig(log_path)
        rig.play()
        tags = ("DHCP", "FTP", "WIFI", "NEW", "DNS", "HTTP", "SUMMARY")
        expected = [l for l in read_text(REFERENCE + ".log").splitlines() if l[11:].split(" ", 1)[0] in tags]
        self.assertEqual(normalize(rig.technical.getvalue().splitlines()), normalize(expected))
        self.assertEqual(read_text(log_path), rig.technical.getvalue())

    # Casual Terminal: the reference session's friendly lines, in order, from "connected" to the end of "What happened".
    def test_casual_same_as_reference(self):
        rig = Rig()
        rig.play()
        reference = read_text(REFERENCE + ".casual.txt").splitlines()
        story_part = reference[reference.index("🎮 Switch 2 connected"):reference.index("🛑 Stopping…")]
        summary_part = reference[reference.index("📋 What happened:"):reference.index("🏠 Your Mac is back on your home Wi-Fi. Bye!")]
        self.assertEqual(normalize(rig.casual.getvalue().splitlines()), normalize(story_part + summary_part))

    # One rounding everywhere: the technical and casual times for the same visit agree.
    def test_durations_agree(self):
        rig = Rig()
        rig.play()
        tech = re.search(r"Switch 1 \(dc:68:eb:5a:11:c1\) left the Wi-Fi \(disconnected, was on for (\d+)s\)", rig.technical.getvalue())
        casual = re.search(r"Switch 1 left \(it was here (\d+) seconds\)", rig.casual.getvalue())
        self.assertEqual(tech.group(1), casual.group(1))
        totals = re.findall(r"\(total (\d+)s\)", rig.technical.getvalue())
        heres = re.findall(r": here (\d+) seconds", rig.casual.getvalue())
        self.assertEqual(totals, heres)

    # Every console reference in every line (tracker, story, FTP, summary) is marked, and every mark is a real name.
    def test_every_console_reference_is_marked(self):
        rig = Rig()
        rig.play()
        self.assertEqual(unmarked_references(rig.events, rig.registry), [])
        self.assertEqual(unmarked_outcomes(rig.events), [])                       # and every outcome word (BLOCKED, 200 ok, went DOWN...)
        marked = [e for e in rig.events if e.tech_names or e.casual_names]
        self.assertGreater(len(marked), 30)

    # The FTP changes are facts too, and recorded for the summary and the dashboard.
    def test_service_facts(self):
        rig = Rig()
        rig.play()
        facts = [(e.kind, e.data["url"]) for e in rig.events if e.kind.startswith("service.")]
        self.assertEqual(facts, [("service.up", "ftp://127.0.0.1:5021"), ("service.down", "ftp://127.0.0.1:5021")])
        self.assertEqual(rig.services.url_for(S2), "ftp://127.0.0.1:5021")
        self.assertIsNone(rig.services.url_for(S1))

# Service watchers.
class Services(unittest.TestCase):
    # A console's address starts one watcher; the same address again doesn't; a new one restarts it.
    def test_one_watcher_per_console(self):
        rig = Rig()
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        first = rig.services.watchers[S2]
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.4")
        self.assertTrue(first._stop.is_set())
        self.assertEqual(rig.services.watchers[S2].url, "ftp://192.168.2.4:5021")
        watching = [e for e in rig.events if e.tech and e.tech.startswith("watching")]
        self.assertEqual(len(watching), 2)

    # Turned off (--no-ftp-watch): no watcher at all.
    def test_disabled(self):
        rig = Rig()
        rig.services.enabled = False
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        self.assertEqual(rig.services.watchers, {})

    # Up -> FileZilla opens when the console asks for it; rechecking off -> stop knocking once up.
    def test_up_opens_filezilla_and_stops(self):
        rig = Rig(open_filezilla=True)
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        w = rig.services.watchers[S2]
        self.assertEqual(w.step(False), 3.0)
        self.assertEqual(w.step(True), 1.0)
        self.assertEqual(rig.opened, ["ftp://192.168.2.3:5021"])
        w.recheck = 0
        w.up = False
        self.assertIsNone(w.step(True))

    # A server that was up: one failed knock isn't "down"; while a program on this Mac uses it, it's "busy", still up.
    def test_busy_and_misses(self):
        rig = Rig()
        using = []
        rig.services.in_use = lambda ip, port: bool(using)
        rig.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        w = rig.services.watchers[S2]
        w.step(True)
        using.append("FileZilla")
        self.assertEqual((w.step(False), w.step(False), w.step(False), w.step(False), w.up), (1.0, 1.0, 1.0, 1.0, True))
        busy = [l for l in rig.technical.getvalue().splitlines() if "busy" in l]
        self.assertEqual(len(busy), 1, "logged once per busy stretch")
        self.assertIn("Switch 2: FTP server busy (a program on this Mac is connected to it, e.g. FileZilla) - still UP", busy[0])
        using.pop()
        self.assertEqual((w.step(False), w.step(False), w.up), (3.0, 3.0, True))
        w.step(False)
        self.assertFalse(w.up)
        self.assertIn("went DOWN", rig.technical.getvalue().splitlines()[-1])

    # The real knock: something listening = up, nothing = down.
    def test_probe_port(self):
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        self.assertTrue(services.probe_port("127.0.0.1", port, timeout=1))
        server.close()
        self.assertFalse(services.probe_port("127.0.0.1", port, timeout=1))

    # In the background: a watcher knocks by itself and stops when asked.
    def test_background(self):
        bus, activity = EventBus(), ConsoleActivity()
        knocks = []
        svc = services.Services(bus, ReferenceRegistry(), activity, probe=lambda ip, port: knocks.append(port) or True)
        svc.attach()
        bus.fact("console.address", mac=S2, ip="192.168.2.3")
        svc.watchers[S2].thread.join(2)
        self.assertEqual(knocks[:1], [5021])
        svc.stop_all()
        self.assertEqual(svc.watchers, {})

# FileZilla.
class FileZilla(unittest.TestCase):
    # Missing -> a warning (shown in casual too); failing -> a problem; working -> said.
    def test_open(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        self.assertFalse(filezilla.FileZilla(bus, lambda: False, None).open("ftp://x"))
        self.assertFalse(filezilla.FileZilla(bus, lambda: True, lambda url: 1 / 0).open("ftp://x"))
        self.assertTrue(filezilla.FileZilla(bus, lambda: True, lambda url: None).open("ftp://x"))
        self.assertEqual([(e.level, e.tech) for e in events], [
            ("warn", "open_filezilla is on, but FileZilla isn't in /Applications"),
            ("problem", "could not open FileZilla: ZeroDivisionError: division by zero"),
            ("info", "opened FileZilla on ftp://x")])
        self.assertEqual(events[-1].casual, "📂 Opened FileZilla on ftp://x")
        self.assertEqual(events[1].details[-1], "ZeroDivisionError: division by zero")      # a bug keeps its traceback

# The end-of-run check of the sensors (features/sensors.py) and the tracker's counts.
class Sensors(unittest.TestCase):
    # A source that stayed silent while the others saw consoles is said (a warning, please report it); a normal run says nothing.
    def test_quiet_sources(self):
        bus, got = EventBus(), []
        bus.add_sink(got.append)
        sensors.check(bus, {"wifi": 3, "dhcp": 2, "unmatched": {}}, True, "15.0")
        sensors.check(bus, {"wifi": 0, "dhcp": 0, "unmatched": {}}, True, "15.0")          # nobody came
        sensors.check(bus, {"wifi": 0, "dhcp": 2, "unmatched": {}}, False, "15.0")         # the watcher never started (said at start)
        self.assertEqual(got, [])
        sensors.check(bus, {"wifi": 0, "dhcp": 2, "unmatched": {}}, True, "26.1")
        sensors.check(bus, {"wifi": 4, "dhcp": 0, "unmatched": {"192.168.2.3": 5, "192.168.2.4": 1}}, True, "26.1")
        self.assertEqual([(e.tag, e.level, e.kind, e.data["sensor"]) for e in got], [("WIFI", "warn", "sensors.quiet", "wifi"), ("DHCP", "warn", "sensors.quiet", "leases")])
        self.assertIn("saw no console join or leave, though 2 addresses handed out: macOS 26.1", got[0].tech)
        self.assertIn("6 lookups came from addresses no DHCP lease names (192.168.2.3, 192.168.2.4)", got[1].tech)
        self.assertTrue(all("Report a problem" in e.casual for e in got))

    # The tracker counts what each source saw; the Mac's own self-test isn't a device.
    def test_tracker_counts(self):
        rig = Rig()
        tracker = rig.tracker
        tracker.answer_ip = "192.168.2.1"
        tracker.on_dns("192.168.2.1", "ctest.cdn.nintendo.net", "A", Decision("test", "x", "Switch 1 connection test"), "192.168.2.1")
        tracker.on_dns("192.168.2.9", "example.com", "A", Decision("block", "x", "blocked"), None)
        tracker.on_dhcp("192.168.2.3", "48:f1:eb:5a:22:c2")
        tracker.on_wifi_join("48:f1:eb:5a:22:c2", time.time())
        self.assertEqual(tracker.sensed, {"wifi": 1, "dhcp": 1, "unmatched": {"192.168.2.9": 1}})

# The casual story.
class Story(unittest.TestCase):
    # "Blocked so far": only when the total grew, at most once a minute per console.
    def test_blocked_ticker(self):
        rig = Rig()
        for n in range(3):
            rig.activity.count_dns(S2, "x%d.com" % n, False)
        rig.story.tick(now=100.0)
        rig.activity.count_dns(S2, "y.com", False)
        rig.story.tick(now=130.0)
        rig.story.tick(now=160.0)
        rig.story.tick(now=230.0)
        self.assertEqual(rig.casual.getvalue().splitlines(), [
            "🛡️ Switch 2: 3 attempts to reach the internet blocked so far",
            "🛡️ Switch 2: 4 attempts to reach the internet blocked so far"])

# Summaries.
class Summary(unittest.TestCase):
    # Nobody came: both versions say so.
    def test_nobody(self):
        rig = Rig()
        summary.report(rig.bus, rig.activity, rig.registry, START, START + 5)
        tech = rig.technical.getvalue()
        self.assertIn("SUMMARY no console connected during this run", tech)
        self.assertNotIn("firewall", tech)
        self.assertEqual(rig.casual.getvalue().splitlines(), ["📋 What happened:", "   Nobody connected this time."])

    # A watched FTP server that never answered.
    def test_never_seen(self):
        rig = Rig()
        rig.activity.saw_ip("192.168.2.3", S2)
        rig.activity.service_watching(S2, "FTP", "ftp://192.168.2.3:5021")
        lines = [t for t, *_ in summary.technical_lines(rig.activity, rig.registry, START, START + 5)]
        self.assertIn("   FTP server     : never seen on ftp://192.168.2.3:5021", lines)

if __name__ == "__main__":
    unittest.main()
