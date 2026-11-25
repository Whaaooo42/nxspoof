"""
test_output.py - unit tests for nxspoof.output: colors, the Terminal (both styles), the run log file.

Colors and the casual problem layout are compared with the answers recorded at the cutoff (tests/fixtures/golden.json),
so the Terminal keeps looking exactly the same.
"""

import io
import os
import re
import shutil
import tempfile
import time
import unittest

from nxspoof.core.events import Event
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.core.places import Places
from nxspoof.core.text import SELFTEST, compose, name, outcome
from nxspoof.output import colors, datafolder, logfile, terminal
from tests import golden

REFERENCE = os.path.join(PROJECT_DIR, "tests", "fixtures", "reference", "session.log")
S2, UNKNOWN = "48:f1:eb:5a:22:c2", "0a:01:02:03:04:05"
STAMP_RX = re.compile(r"\[\d\d:\d\d:\d\d\]")

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A marked reference's color, the way the registry gives it (from the real settings).
def color_of(who):
    return {S2: "magenta", UNKNOWN: "yellow", SELFTEST: "gray"}.get(who, "white")

# "Switch 2", marked as console S2.
def switch2():
    return name("Switch 2", S2)

# A Terminal sink writing into a string.
def make_terminal(mode, use_colors=True):
    out = io.StringIO()
    return terminal.TerminalSink(mode, use_colors, color_of, out), out

# Colors: same codes as today.
class Colors(unittest.TestCase):
    # Every line of the reference log is colored exactly as recorded.
    def test_colorize_same_as_recorded(self):
        lines = [raw[len("[HH:MM:SS] "):] for raw in read_text(REFERENCE).splitlines()]
        self.assertEqual([colors.colorize(line) for line in lines], golden("colorize"))
        self.assertEqual(colors.paint("x", "red+bold"), golden("paint_red_bold"))
        self.assertEqual(colors.paint("x", "nope"), "x")

    # Marked names are painted like the old labels (console color bold, the self-test gray).
    def test_names_same_as_recorded_labels(self):
        label = compose("%s (%s)", switch2(), "48:f1:eb:5a:22:c2")
        self.assertEqual(colors.render(label, label.spans, color_of), golden("label_s2_with_mac"))
        selftest = name("Mac (self-test)", SELFTEST)
        self.assertEqual(colors.render(selftest, selftest.spans, color_of), golden("label_selftest"))

    # Only the marked stretch is painted: the same name inside a model, a test label or a value stays plain.
    def test_only_marked_names_are_painted(self):
        line = compose("DNS   %s asked A     ctest.p01.ctest.srv.nintendo.net -> 192.168.2.1  (Switch 2 connection test)",
                       compose("%s (127.0.0.1)", switch2()))
        painted = colors.render(line, line.spans, color_of, tag=True)
        self.assertEqual(painted.count("\x1b[35;1mSwitch 2\x1b[0m"), 1)
        self.assertEqual(colors.strip(painted), line)
        settings = compose("console %s  48:f1:eb:5a:22:c2  Nintendo Switch 2", switch2())
        self.assertEqual(colors.render(settings, settings.spans, color_of).count("\x1b[35;1m"), 1)
        plain = colors.render("Switch 2 asked A x -> BLOCKED", (), color_of)
        self.assertEqual(plain, "Switch 2 asked A x -> BLOCKED", "nothing is searched for: no mark, no color")
        marked = compose("%s asked A x -> %s", switch2(), outcome("BLOCKED", "blocked"))
        painted = colors.render(marked, marked.spans, color_of)
        self.assertEqual(painted, "\x1b[35;1mSwitch 2\x1b[0m asked A x -> \x1b[31mBLOCKED\x1b[0m")

# The Terminal, both styles.
class Terminal(unittest.TestCase):
    # Technical: gray time + colored line, exactly as recorded.
    def test_technical_line_same_as_recorded(self):
        sink, out = make_terminal("technical")
        ts = time.mktime((2026, 11, 13, 22, 26, 12, 0, 0, -1))
        sink(Event("log", "WIFI", compose("%s joined the Wi-Fi", compose("%s (%s)", switch2(), "48:f1:eb:5a:22:c2")), mac=S2, ts=ts))
        self.assertEqual(out.getvalue(), golden("technical_wifi_join") + "\n")

    # Technical: casual-only lines and facts are skipped; details follow under the line; no colors when off.
    def test_technical_skips_and_details(self):
        sink, out = make_terminal("technical", use_colors=False)
        ts = time.mktime((2026, 11, 13, 9, 5, 1, 0, 0, -1))
        sink(Event("say", casual="🎮 Switch 2 connected", mac=S2, ts=ts))
        sink(Event("console.online", mac=S2, ts=ts))
        sink(Event("log", "PROBLEM", "pf failed", level="problem", details=["line one"], ts=ts))
        self.assertEqual(out.getvalue(), "[09:05:01] PROBLEM pf failed\n[09:05:01]       line one\n")

    # Casual: friendly lines with the console's color; plain log lines are hidden.
    def test_casual(self):
        sink, out = make_terminal("casual")
        sink(Event("log", "DNS", compose("%s (127.0.0.1) asked A     x.com -> BLOCKED", switch2()), mac=S2))
        sink(Event("say", casual=compose("🎮 %s connected", switch2()), mac=S2))
        sink(Event("log", "FTP", compose("%s: FTP server is UP -> ftp://x", switch2()),
                   casual=compose("📁 %s's file server is ready: ftp://x", switch2()), mac=S2))
        self.assertEqual(out.getvalue().splitlines(), ["🎮 %s connected" % golden("casual_s2_name"),
                                                       "📁 %s's file server is ready: ftp://x" % golden("casual_s2_name")])

    # Casual: problems are always shown, laid out exactly as recorded.
    def test_casual_problem_same_as_recorded(self):
        sink, out = make_terminal("casual")
        sink(Event("log", "PROBLEM", "the firewall rules didn't load", level="problem", details=["pfctl: syntax error"]))
        self.assertEqual(out.getvalue(), golden("casual_problem"))

    # A closed window: writing fails once, then everything goes to /dev/null without errors.
    def test_survives_closed_window(self):
        sink, broken = make_terminal("technical")
        broken.close()
        sink(Event("log", "DNS", "x"))
        sink(Event("log", "DNS", "y"))
        self.assertEqual(sink.stream.name, os.devnull)
        sink.stream.close()

    # Only the two styles exist.
    def test_modes(self):
        with self.assertRaises(ValueError):
            terminal.TerminalSink("fancy")

# The run log file.
class LogFile(unittest.TestCase):
    # A temporary project folder.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")

    # Remove it.
    def tearDown(self):
        shutil.rmtree(self.dir)

    # New file + last_run.log shortcut + old single log moved + oldest runs pruned.
    def test_create_run_log(self):
        logs = os.path.join(self.dir, "logs")
        os.makedirs(logs)
        for name in ("2026-01-01_10-00-00.log", "2026-01-01_10-00-00.jsonl", "2026-01-02_10-00-00.log", "2026-01-03_10-00-00-dryrun.log",
                     "notes.log", "2025-12-31_09-00-00-dashboard.log", "2025-12-31_08-00-00-setup.log"):
            open(os.path.join(logs, name), "w").close()
        with open(os.path.join(self.dir, "last_run.log"), "w") as f:
            f.write("old single log\n")
        given = []
        now = time.mktime((2026, 11, 13, 22, 26, 10, 0, 0, -1))
        path = logfile.create_run_log(self.dir, "logs", keep_last=2, now=now, give=given.append, give_link=given.append)
        self.assertEqual(path, os.path.join(logs, "2026-11-13_22-26-10.log"))
        self.assertEqual(os.readlink(os.path.join(self.dir, "last_run.log")), os.path.join("logs", "2026-11-13_22-26-10.log"))
        self.assertEqual(sorted(os.listdir(logs)), ["2025-12-31_08-00-00-setup.log", "2025-12-31_09-00-00-dashboard.log",          # never pruned
                                                    "2026-01-03_10-00-00-dryrun.log", "2026-11-13_22-26-10.jsonl", "2026-11-13_22-26-10.log",
                                                    "before-logs-folder.log", "notes.log"])            # an old run's twin goes with it
        self.assertEqual(read_text(os.path.join(logs, "before-logs-folder.log")), "old single log\n")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(given, [logs, path, logfile.records_path(path), os.path.join(self.dir, "last_run.log")])

    # A dry run gets its suffix; keep_last 0 keeps everything; an absolute folder works.
    def test_dry_run_and_absolute_folder(self):
        folder = os.path.join(self.dir, "elsewhere")
        now = time.mktime((2026, 11, 13, 8, 0, 0, 0, 0, -1))
        path = logfile.create_run_log(self.dir, folder, dry_run=True, now=now)
        self.assertEqual(path, os.path.join(folder, "2026-11-13_08-00-00-dryrun.log"))

    # Setup's log: labelled, and last_run.log keeps pointing at the latest real run.
    def test_label_without_last_run(self):
        now = time.mktime((2026, 11, 13, 8, 0, 0, 0, 0, -1))
        path = logfile.create_run_log(self.dir, label="setup", now=now)
        self.assertTrue(path.endswith("2026-11-13_08-00-00-setup.log"))
        self.assertFalse(os.path.lexists(os.path.join(self.dir, "last_run.log")))

    # The sink writes '[HH:MM:SS] line' + details, never casual-only lines, facts or colors.
    def test_sink(self):
        path = os.path.join(self.dir, "run.log")
        sink = logfile.LogFileSink(path)
        ts = time.mktime((2026, 11, 13, 22, 26, 51, 0, 0, -1))
        sink(Event("log", "SUMMARY", "run 22:26:10 -> 22:26:51 (0:00:40)", ts=ts))
        sink(Event("say", casual="📋 What happened:", ts=ts))
        sink(Event("console.left", mac=S2, ts=ts))
        sink(Event("log", "PROBLEM", "\x1b[31mred\x1b[0m", level="problem", details=["more"], ts=ts))
        self.assertEqual(read_text(path), "[22:26:51] SUMMARY run 22:26:10 -> 22:26:51 (0:00:40)\n"
                                          "[22:26:51] PROBLEM red\n[22:26:51]       more\n")
        logfile.LogFileSink(None)(Event("log", "DNS", "nowhere"))

# Write a small text file (folders made as needed).
def put(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

# NXSPOOF's data folder: moving the old files over once, the shortcut, the program copy ▶ Start runs.
class DataFolder(unittest.TestCase):
    # A temporary home with an nx-spoof folder in it; the data folder isn't made yet.
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="nxspoof-home-")
        self.project = os.path.join(self.home, "Documents", "nx-spoof")
        self.data = os.path.join(self.home, "Library", "Application Support", "NXSPOOF")
        os.makedirs(self.project)

    # Remove the temporary home.
    def tearDown(self):
        shutil.rmtree(self.home)

    # The first time: settings.json, every log and last_run.log move over, the `data` shortcut appears; then nothing to do.
    def test_prepare_moves_old_files(self):
        put(os.path.join(self.project, "settings.json"), "{}")
        for name in ("2026-11-16_10-00-00.log", "2026-11-16_10-00-00.jsonl", ".page_start.txt"):
            put(os.path.join(self.project, "logs", name))
        os.symlink(os.path.join("logs", "2026-11-16_10-00-00.log"), os.path.join(self.project, "last_run.log"))
        done = datafolder.prepare(Places.together(self.project, self.data))
        self.assertEqual([level for level, _ in done], ["info"] * 5)
        self.assertIn("moved 3 log file(s)", " ".join(text for _, text in done))
        self.assertEqual(sorted(os.listdir(self.project)), ["data"])
        self.assertEqual(os.path.realpath(os.path.join(self.project, "data")), os.path.realpath(self.data))
        self.assertTrue(os.path.isfile(os.path.join(self.data, "settings.json")))
        self.assertEqual(os.path.realpath(os.path.join(self.data, "last_run.log")),
                         os.path.realpath(os.path.join(self.data, "logs", "2026-11-16_10-00-00.log")))
        self.assertEqual(datafolder.prepare(Places.together(self.project, self.data)), [])

    # The data folder already has settings.json: it's the one used; the old one is renamed, and that's a warning.
    def test_prepare_settings_in_both(self):
        put(os.path.join(self.data, "settings.json"), "{\"new\": 1}")
        put(os.path.join(self.project, "settings.json"), "{}")
        done = datafolder.prepare(Places.together(self.project, self.data))
        self.assertEqual(done[0][0], "warn")
        self.assertTrue(os.path.isfile(os.path.join(self.project, "settings.json.old")))
        with open(os.path.join(self.data, "settings.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{\"new\": 1}")

    # A log already in the data folder isn't overwritten: it stays in the old logs/, which then stays too.
    def test_prepare_log_in_both(self):
        put(os.path.join(self.data, "logs", "a.log"), "new")
        put(os.path.join(self.project, "logs", "a.log"), "old")
        put(os.path.join(self.project, "logs", "b.log"))
        done = datafolder.prepare(Places.together(self.project, self.data))
        self.assertEqual(done[0], ("warn", "moved 1 log file(s) to the data folder; 1 already there were left in the nx-spoof folder's logs/ (a.log)"))
        self.assertEqual(os.listdir(os.path.join(self.project, "logs")), ["a.log"])

    # A program running from the data folder itself (the program copy) moves nothing and adds no shortcut.
    def test_prepare_from_the_program_copy(self):
        inner = os.path.join(self.data, "program")
        os.makedirs(inner)
        put(os.path.join(inner, "settings.json"), "{}")
        self.assertEqual(datafolder.prepare(Places.together(inner, self.data)), [])
        self.assertEqual(os.listdir(inner), ["settings.json"])

    # The first record of how a part was wins (Undo's "before"); nothing recorded reads as {}.
    def test_remember_before(self):
        self.assertEqual(datafolder.before(self.data), {})
        os.makedirs(self.data)
        self.assertTrue(datafolder.remember_before(self.data, "bridge", {"mode": "dhcp", "ip": None, "mask": None}))
        self.assertFalse(datafolder.remember_before(self.data, "bridge", {"mode": "manual", "ip": "10.0.0.2", "mask": "255.255.255.0"}))
        self.assertFalse(datafolder.remember_before(self.data, "cable", None))
        self.assertEqual(datafolder.before(self.data), {"bridge": {"mode": "dhcp", "ip": None, "mask": None}})

    # The program copy: nx_spoof.py and nxspoof/ only (no __pycache__), then only what changed, and what's gone is removed.
    def test_copy_program(self):
        put(os.path.join(self.project, "nx_spoof.py"))
        put(os.path.join(self.project, "nxspoof", "a.py"))
        put(os.path.join(self.project, "nxspoof", "sub", "b.py"))
        put(os.path.join(self.project, "nxspoof", "__pycache__", "a.cpython-39.pyc"))
        put(os.path.join(self.project, "settings.json"))
        dest, copied, removed = datafolder.copy_program(self.project, self.data)
        self.assertEqual((dest, copied, removed), (os.path.join(self.data, "program"), 3, 0))
        self.assertEqual(sorted(os.listdir(dest)), ["nx_spoof.py", "nxspoof"])
        self.assertEqual(datafolder.copy_program(self.project, self.data)[1:], (0, 0))
        put(os.path.join(self.project, "nxspoof", "sub", "b.py"), "changed")
        os.remove(os.path.join(self.project, "nxspoof", "a.py"))
        self.assertEqual(datafolder.copy_program(self.project, self.data)[1:], (1, 1))
        self.assertFalse(os.path.exists(os.path.join(dest, "nxspoof", "a.py")))

if __name__ == "__main__":
    unittest.main()
