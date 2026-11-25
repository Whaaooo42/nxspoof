"""
test_config.py - unit tests for nxspoof.app.cli and nxspoof.app.config (options + settings -> RunConfig).

The SETTINGS lines must keep the old wording (they appear in every run's log).
"""

import os
import unittest

from nxspoof.app import cli, config
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.settings import store

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")

# A store loaded from the settings fixture.
def fixture_store():
    s = store.SettingsStore(FIXTURE)
    s.load()
    return s

# Options + settings -> RunConfig.
class RunConfigBuild(unittest.TestCase):
    # No options: settings.json's values, each marked as coming from it; old SETTINGS wording.
    def test_plain(self):
        c = config.build_config(fixture_store(), cli.parse_args([]))
        self.assertTrue(c.get("spoof.firewall"))
        self.assertEqual((c.source("spoof.firewall"), c.source("ftp.port")), ("default", "settings.json"))    # a run-only value (D30)
        self.assertEqual(c.describe(), [
            "console=technical (Terminal style; this file is always technical)",
            "firewall=True  auto_sharing=True  keep_sharing=False  ftp_watch=True  open_filezilla=False"])

    # Options win for this run, and are named in an extra line.
    def test_overrides(self):
        args = cli.parse_args(["--no-firewall", "--console", "casual", "--open-filezilla"])
        c = config.build_config(fixture_store(), args)
        self.assertFalse(c.get("spoof.firewall"))
        self.assertEqual(c.get("display.console"), "casual")
        self.assertEqual(c.source("spoof.firewall"), "--no-firewall")
        lines = c.describe()
        self.assertEqual(lines[1], "firewall=False  auto_sharing=True  keep_sharing=False  ftp_watch=True  open_filezilla=True")
        self.assertTrue(lines[2].startswith("from options: "))
        self.assertIn("spoof.firewall=False (--no-firewall)", lines[2])

    # Testing options keep their defaults (DNS 53, web 80) and are reachable as extras.
    def test_extras(self):
        c = config.build_config(fixture_store(), cli.parse_args(["--ip", "127.0.0.1"]))
        self.assertEqual((c.extra("ip"), c.extra("dns_port"), c.extra("http_port")), ("127.0.0.1", 53, 80))

    # A setting missing from the file comes from the defaults.
    def test_default_source(self):
        s = store.SettingsStore(os.path.join(PROJECT_DIR, "tests", "fixtures", "does-not-exist.json"))
        s.load()
        c = config.build_config(s, cli.parse_args([]))
        self.assertEqual(c.source("ftp.port"), "default")
        self.assertEqual(c.get("ftp.port"), 5000)

# The options table itself.
class OptionsTable(unittest.TestCase):
    # Every option that changes a value points at a real setting, or at a value a run has without one (D30: RUN_ONLY).
    def test_settings_exist(self):
        from nxspoof.settings import schema
        for o in cli.OPTIONS:
            if o.setting:
                self.assertTrue(o.setting in schema.BY_NAME or o.setting in config.RUN_ONLY, o.flag)

    # The same flags as before the restructure (nothing lost), plus the agreed new ones.
    def test_same_flags_as_the_old_code(self):
        old = ["--ip", "--answer-ip", "--dns-port", "--http-port", "--settings", "--no-firewall", "--manual-sharing",
               "--keep-sharing", "--no-color", "--console", "--no-ftp-watch", "--open-filezilla"]     # --no-dashboard, --dashboard-port: gone with their settings (D30)
        new = ["--anyway", "--fix",              # agreed: start even when not ready / setup's fixes first (the page's Start anyway, Fix and start; D30)
               "--yes", "--only", "--skip", "--task",   # agreed: setup's choices and the dashboard's ticket (stage 3, 2026-11-16)
               "--no-keeper",                           # agreed: one root process, no privilege separation (D24, 2026-11-16)
               "--full", "--no-open",                   # agreed: the problem report's choices ("do your thing", 2026-11-24)
               "--allow-sleep",                         # agreed: a run keeps the Mac awake ("if I put NXSPOOF on, it's cuz I wanna use it", 2026-11-24)
               "--to",                                  # agreed: what Undo puts back, before NXSPOOF or macOS's defaults (2026-11-17)
               "--probe"]                               # agreed: send a name to the Mac to see what a console asks (2026-11-23, "let's probe that thing")
        self.assertEqual(sorted(old + new), sorted(cli.BY_FLAG))

    # given_options: what was typed, as typed, without the kept ones and without values equal to the default.
    def test_given_options(self):
        args = cli.parse_args(["dashboard", "--settings", "x.json", "--keep-sharing", "--no-color", "--dns-port", "53"])
        self.assertEqual(cli.given_options(args, keep=("--settings",)), ["--no-color", "--keep-sharing"])
        self.assertEqual(cli.given_options(cli.parse_args(["dashboard"])), [])

    # What the start found becomes a START line (a start with --anyway is a warning); nothing for a dry run.
    def test_preflight(self):
        # The START lines for these extras: (technical text, level, data).
        def lines(**extras):
            return [(tech, level, data) for tech, _, level, data in config.RunConfig({}, {}, extras).preflight()]
        self.assertEqual(lines(), [("setup's checks: this Mac is ready", "info", {"doctor": "ready"})])
        self.assertEqual(lines(anyway=True)[0][1:], ("warn", {"doctor": "anyway"}))
        self.assertEqual(lines(ip="127.0.0.1"), [])

    # The START line for the virtual cable every real run plugs in itself.
    def test_cable_line(self):
        from collections import namedtuple
        st = namedtuple("St", "ok")
        self.assertEqual(config.RunConfig.cable_line("plugged", st(True)),
                         ("virtual cable: plugged in just now (taken out again when NXSPOOF stops)", None, "info", {"cable": "plugged"}))
        self.assertEqual(config.RunConfig.cable_line("already", st(True))[0], "virtual cable: already plugged in")
        warn = config.RunConfig.cable_line("plugged", st(False))
        self.assertEqual((warn[2], warn[3]), ("warn", {"cable": "plugged", "address": False}))

if __name__ == "__main__":
    unittest.main()
