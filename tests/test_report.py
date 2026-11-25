"""
tests/test_report.py - 🐞 the problem report (features/report.py, app/reporting.py): what it picks from the logs' records,
what it replaces (nothing personal may reach a GitHub issue), and its two doors (the Terminal, the dashboard's reader).
Made-up consoles, names and addresses only.
"""

import argparse
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from nxspoof.app import reporting
from nxspoof.core.places import Places
from nxspoof.features import report

S2, FRIEND = "48:f1:eb:12:34:56", "48:f1:eb:ab:cd:ef"
ABOUT = {"mac": "macOS 15.0 (24A000), model Mac00,0", "version": "2.0.0-dev", "program": "/Users/alex/Documents/nx-spoof",
         "data": "/Users/alex/Library/Application Support/NXSPOOF (settings.json, logs/)"}
WORDS = {"/Users/alex": "~", "alex": "<you>", "Alex Doe": "<you>", "Alex's MacBook Air": "<this Mac>"}

# One record, as output/logfile.py RecordSink writes it.
def rec(tag, tech, level="info", kind="log", mac=None, marks=None, data=None, details=None, t="10:00:00"):
    r = {"kind": kind, "level": level, "tag": tag, "tech": tech, "t": t, "ts": 0}
    for key, value in (("mac", mac), ("marks", marks), ("data", data), ("details", details)):
        if value:
            r[key] = value
    return r

# A run's records: its framing, a console with its name marked, maybe a problem with a traceback.
def a_run(problem=False, hotspot="Alex's hotspot"):
    line = "console Alex's Switch  %s  Switch 2" % S2
    out = [rec("LOG", "this run: logs/x.log", kind="run.log"),
           rec("LOG", "NXSPOOF 2.0.0-dev (the nxspoof package)", kind="run.version", data={"version": "2.0.0-dev"}),
           rec("LOG", "this Mac: macOS 15.0 (24A000), model Mac00,0", kind="run.mac"),
           rec("SETTINGS", "firewall=True  auto_sharing=True"),
           rec("SETTINGS", line, mac=S2, marks=[[17, 30, S2]]),
           rec("SHARING", 'hotspot: "%s", channel 11' % hotspot, kind="run.hotspot", data={"name": hotspot}),
           rec("DNS", "listening on 192.168.2.1:53"),
           rec("READY", "Everything works on the Mac side.", kind="run.ready"),
           rec("DHCP", "gave address 192.168.2.3 to Pat's Switch (%s)" % FRIEND, mac=FRIEND, kind="console.address", marks=[[25, 37, FRIEND]]),
           rec("DNS", "one", t="10:01:00"), rec("DNS", "two", t="10:01:01"), rec("DNS", "three", t="10:01:02"), rec("DNS", "four", t="10:01:03")]
    if problem:
        out.append(rec("SHARING", "couldn't read the hotspot's settings (KeyError: 'SSID') - mail alex@example.com", level="warn",
                       details=["Traceback (most recent call last):", "KeyError: 'SSID'"], t="10:01:04"))
    out += [rec("", "Stopping...", kind="run.stopping"), rec("", "Stopped.", kind="run.stopped")]
    return out

# The problem report's engine, on made-up logs.
class Report(unittest.TestCase):
    # A temporary logs/ folder.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-report-")
        self.logs = os.path.join(self.dir, "logs")
        os.makedirs(self.logs)

    # Remove it.
    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # A log and its records in logs/.
    def write(self, name, records):
        open(os.path.join(self.logs, name), "w").close()
        with open(os.path.join(self.logs, name[:-4] + ".jsonl"), "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in records))

    # Names (marked, given or generic), owners, the Mac, you, e-mail addresses and MAC addresses: each its own stand-in.
    def test_scrub(self):
        scrub = report.Scrub(["Alex's Switch", "Switch 2", "Pat's Switch"], ["Pat"], WORDS)
        self.assertEqual(scrub("Alex's Switch and alex's switch, Pat's Switch, Pat"), "console 1 and console 1, console 2, <owner>")
        self.assertEqual(scrub("Switch 2 passed"), "Switch 2 passed")                   # a model name isn't personal
        self.assertEqual(scrub("/Users/alex/Documents by alex on Alex's MacBook Air"), "~/Documents by <you> on <this Mac>")
        self.assertEqual(scrub("%s %s %s" % (S2, FRIEND.upper(), S2)), "48:f1:eb:xx:xx:01 48:f1:eb:xx:xx:02 48:f1:eb:xx:xx:01")
        self.assertEqual(scrub("write to alex.doe+nx@mail.example.org"), "write to <e-mail>")
        self.assertEqual(scrub("/opt/homebrew/opt/python@3.15/bin/python3"), "/opt/homebrew/opt/python@3.15/bin/python3")   # not an address
        self.assertEqual(scrub("Patrick"), "Patrick")                                 # whole names only

    # The last 3 runs (framing lines, each problem with 3 lines before it and its traceback), the newest other log with a
    # problem; nothing personal anywhere.
    def test_build(self):
        for i in range(4):
            self.write("2026-11-2%d_10-00-00.log" % i, a_run(problem=i == 3))
        self.write("2026-11-23_09-00-00-setup.log", [rec("SETUP", "bridge  fix failed: OSError: busy", level="problem")])
        self.write("2026-11-23_09-30-00-dashboard.log", [rec("DASH", "dashboard open")])
        text = report.build(self.logs, ABOUT, [{"name": "bridge", "title": "Thunderbolt Bridge", "state": "ok", "tech": "bridge0 for alex"}],
                            consoles=["Alex's Switch"], people=["Pat"], words=WORDS, now=0)
        titles = [l for l in text.splitlines() if l.startswith("#### ")]
        self.assertEqual(len(titles), 4)
        self.assertTrue(titles[0].startswith("#### Run 2026-11-23 10:00:00") and titles[0].endswith("· 1 warning"), titles[0])
        setup = [t for t in titles if t.startswith("#### Setup log")]
        self.assertEqual(len(setup), 1)
        self.assertTrue(setup[0].startswith("#### Setup log 2026-11-23 ") and setup[0].endswith(" · 1 problem"), setup)
        self.assertNotIn("2026-11-20", text)                                            # the 4th run is too old
        self.assertNotIn("Dashboard log", text)                                         # no problem there
        self.assertIn("[10:00:00] SETTINGS console console 1  48:f1:eb:xx:xx:01  Switch 2", text)
        self.assertIn('hotspot: "<hotspot>", channel 11', text)
        block = text.split("#### ")[1]
        self.assertIn("[10:01:01] DNS   two\n[10:01:02] DNS   three\n[10:01:03] DNS   four\n[10:01:04] SHARING couldn't read", block)
        self.assertIn("           KeyError: 'SSID'", block)                            # its traceback, indented under it
        self.assertNotIn("DNS   one", block)                                            # 4 lines before: left out
        self.assertIn("…", block)
        self.assertIn("✅ Thunderbolt Bridge: bridge0 for <you>", text)
        for personal in ("alex", "Alex", "Pat's", "12:34:56", "ab:cd:ef", "example.com", "Alex's hotspot"):
            self.assertNotIn(personal, text, personal)

    # full: the newest run's whole log, folded; too long: the older logs go first, said.
    def test_full_and_too_long(self):
        for i in range(3):
            self.write("2026-11-2%d_10-00-00.log" % i, a_run())
        text = report.build(self.logs, ABOUT, [], words=WORDS, full=True, now=0)
        self.assertIn("<details><summary>The whole technical log of 2026-11-22_10-00-00.log", text)
        self.assertIn("[10:01:00] DNS   one", text.split("<details>")[1])
        with mock.patch.object(report, "MAX_CHARS", 1200):
            short = report.build(self.logs, ABOUT, [], words=WORDS, now=0)
        self.assertEqual(short.count("#### "), 1)
        self.assertIn("_(older logs left out: too long)_", short)

    # No logs with records yet: the report says what to do.
    def test_no_logs(self):
        self.assertIn("_No logs with records yet", report.build(self.logs, ABOUT, [], now=0))

# The two doors (app/reporting.py), with a made-up Mac.
class Doors(unittest.TestCase):
    # A temporary data folder with one run.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-report-")
        logs = os.path.join(self.dir, "logs")
        os.makedirs(logs)
        open(os.path.join(logs, "2026-11-24_10-00-00.log"), "w").close()
        with open(os.path.join(logs, "2026-11-24_10-00-00.jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in a_run(problem=True)))
        self.places = Places.together(self.dir, self.dir)
        self.copied, self.opened = [], []
        test = self

        # A Mac whose setup checks can't run here (the report says so and goes on).
        class Mac(object):
            # What kind of Mac, made up.
            def machine_facts(self):
                return {"macos": "15.0", "build": "24A000", "model": "Mac00,0"}

            # Its personal words.
            def personal_words(self):
                return dict(WORDS)

            # The hotspot.
            def hotspot_settings(self):
                return {"name": "Alex's hotspot"}

            # Copying works.
            def copy_text(self, text):
                test.copied.append(text)
                return True

            # Opening a page.
            def open_url(self, url):
                test.opened.append(url)

            # Files stay as they are.
            def give_to_user(self, path):
                return None
        self.system = Mac()

    # Remove it.
    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # GitHub's new issue page: none before the repository exists; the problem form with a title to finish after.
    def test_new_issue_url(self):
        self.assertIsNone(reporting.new_issue_url(None))
        self.assertEqual(reporting.new_issue_url("https://github.com/someone/nxspoof/"),
                         "https://github.com/someone/nxspoof/issues/new?template=problem.yml&title=Problem%3A+")

    # The Terminal's door: printed, saved in logs/reports/, copied, GitHub opened (or only shown, --no-open, or nothing to
    # open yet); a setup check that can't run is a line of the report, not a crash.
    def test_command(self):
        out = io.StringIO()
        with mock.patch.object(reporting, "new_issue_url", lambda: "https://github.com/someone/nxspoof/issues/new"):
            self.assertEqual(reporting.report_command(argparse.Namespace(full=False, no_open=False), self.system, self.places, None, out), 0)
            reporting.report_command(argparse.Namespace(full=False, no_open=True), self.system, self.places, None, out)
        with mock.patch.object(reporting, "new_issue_url", lambda: None):          # a copy with no repository address
            reporting.report_command(argparse.Namespace(full=True, no_open=False), self.system, self.places, None, out)
        said = out.getvalue()
        self.assertEqual(self.opened, ["https://github.com/someone/nxspoof/issues/new"])
        self.assertIn("🔗 New issue: https://github.com/someone/nxspoof/issues/new", said)
        self.assertIn("🔗 No repository address yet", said)
        saved = os.listdir(os.path.join(self.dir, "logs", "reports"))
        self.assertTrue(saved and all(name.endswith("-report.md") for name in saved))
        self.assertEqual(len(self.copied), 3)
        self.assertIn("<details>", self.copied[2])
        text = self.copied[0]
        self.assertIn("**This Mac:** macOS 15.0 (24A000), model Mac00,0", text)
        self.assertIn("✋ FileZilla (optional): check failed: AttributeError:", text)      # a check that crashes is a row, never a lost part
        self.assertIn('hotspot: "<hotspot>"', text)
        for personal in ("alex", "Alex", "12:34:56"):
            self.assertNotIn(personal, text, personal)

    # The dashboard's door: the control center's reader "report" answers the report and GitHub's page.
    def test_reader(self):
        readers = {}
        center = argparse.Namespace(system=self.system, places=self.places, store=None,
                                    server=argparse.Namespace(add_reader=lambda name, func: readers.update({name: func})))
        reporting.attach(center)
        answer = readers["report"]({"full": "1"})
        self.assertEqual(sorted(answer), ["issue_url", "text"])
        self.assertIn("<details>", answer["text"])
        self.assertNotIn("<details>", readers["report"]({})["text"])
