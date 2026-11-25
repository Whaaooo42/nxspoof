"""
test_core.py - unit tests for nxspoof.core: events, controls, formatting, paths.

The expected texts come from the reference session recording
(tests/fixtures/reference/session.log), so the new foundation produces the same look.
"""

import io
import os
import sys
import unittest

from nxspoof.core import controls, events, fmt, paths

REFERENCE = os.path.join(paths.PROJECT_DIR, "tests", "fixtures", "reference", "session.log")

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# How events turn into text (same look as today's log).
class EventsLook(unittest.TestCase):
    # Technical lines line up exactly like today's log: '%-5s %s'.
    def test_technical_line_matches_todays_alignment(self):
        cases = [("DNS", "listening on x", "DNS   listening on x"), ("HTTP", "GET y", "HTTP  GET y"),
                 ("WIFI", "z", "WIFI  z"), ("pf", "0 packets", "pf    0 packets"), ("CHECK", "DNS  PASS", "CHECK DNS  PASS"),
                 ("SETTINGS", "loaded", "SETTINGS loaded"), ("SUMMARY", "run", "SUMMARY run"), ("", "Stopped.", "Stopped.")]
        for tag, tech, expected in cases:
            self.assertEqual(events.Event("log", tag, tech).technical_line(), expected)

    # Every tagged line of the reference log can be rebuilt from (tag, text).
    def test_reference_log_lines_round_trip(self):
        for raw in read_text(REFERENCE).splitlines():
            line = raw.rstrip("\n")[len("[HH:MM:SS] "):]
            tag, _, rest = line.partition(" ")
            if tag.isupper() and rest:
                text = rest.lstrip(" ") if len(tag) < events.TAG_WIDTH else rest
                pad = rest[:len(rest) - len(text)]
                if len(tag) < events.TAG_WIDTH and len(pad) == events.TAG_WIDTH - len(tag):
                    self.assertEqual(events.Event("log", tag, text).technical_line(), line)

    # Continuation lines are indented by six spaces, like the old FATAL details.
    def test_detail_lines(self):
        e = events.Event("log", "FATAL", "port busy", details=["Something else uses port 80."])
        self.assertEqual(e.detail_lines(), ["      Something else uses port 80."])

    # Unknown levels are refused; warn and worse count as problems.
    def test_levels(self):
        with self.assertRaises(ValueError):
            events.Event("log", "X", "y", level="loud")
        self.assertFalse(events.Event("log", "X", "y").is_problem())
        self.assertTrue(events.Event("log", "X", "y", level="warn").is_problem())
        self.assertTrue(events.Event("log", "X", "y", level="fatal").is_problem())

# How the bus delivers events to sinks and listeners.
class EventBusDelivery(unittest.TestCase):
    # Sinks get every event in order; listeners only their kinds.
    def test_sinks_and_listeners(self):
        bus, seen, heard = events.EventBus(), [], []
        bus.add_sink(lambda e: seen.append(e.kind))
        bus.add_listener(lambda e: heard.append(e.kind), kinds=["console.joined"])
        bus.log("DNS", "a")
        bus.say("hello")
        bus.fact("console.joined", mac="aa")
        self.assertEqual(seen, ["log", "say", "console.joined"])
        self.assertEqual(heard, ["console.joined"])

    # A line that is also a fact: ONE event (its kind is the fact), so listeners always react after the line.
    def test_line_with_fact(self):
        bus, seen, got = events.EventBus(), [], []
        bus.add_sink(seen.append)
        bus.add_listener(got.append, kinds=["console.left"])
        bus.log("WIFI", "Switch 2 left the Wi-Fi", mac="m", fact="console.left", seconds=3)
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0].kind, seen[0].technical_line(), seen[0].data, got), ("console.left", "WIFI  Switch 2 left the Wi-Fi", {"seconds": 3}, seen))
        self.assertEqual(bus.log("DNS", "listening").kind, "log")

    # say() has no technical text (never reaches the log file); log() keeps facts in data.
    def test_say_and_data(self):
        bus, got = events.EventBus(), []
        bus.add_sink(got.append)
        bus.say("🎮 hi")
        bus.log("DNS", "q", name="x.com", allowed=False)
        self.assertIsNone(got[0].tech)
        self.assertEqual(got[0].casual, "🎮 hi")
        self.assertEqual(got[1].data, {"name": "x.com", "allowed": False})

    # A broken sink or listener doesn't stop the others; a broken listener is also said in the log (a PROBLEM with its
    # traceback: stderr may be nowhere), once, even when it breaks again on that line.
    def test_failures_are_isolated(self):
        bus, got = events.EventBus(), []
        bus.add_sink(lambda e: 1 / 0)
        bus.add_sink(got.append)
        bus.add_listener(lambda e: 1 / 0)
        bus.add_listener(lambda e: got.append("listener"))
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            bus.log("X", "y")
        finally:
            sys.stderr = old
        self.assertEqual([g if g == "listener" else g.tag for g in got], ["X", "PROBLEM", "listener", "listener"])
        problem = got[1]
        self.assertEqual(problem.level, "problem")
        self.assertIn("a reaction to log failed (ZeroDivisionError: division by zero)", problem.tech)
        self.assertEqual(problem.details[-1], "ZeroDivisionError: division by zero")

    # A sink that emits is refused (it would break the ordering guarantee); the bus keeps working.
    def test_sinks_must_not_emit(self):
        bus, got = events.EventBus(), []
        bus.add_sink(lambda e: bus.log("BAD", "loop") if e.tag == "X" else None)
        bus.add_sink(got.append)
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            bus.log("X", "first")
            report = sys.stderr.getvalue()
        finally:
            sys.stderr = old
        self.assertIn("sinks must never emit", report)
        bus.log("Y", "second")
        self.assertEqual([e.tag for e in got], ["X", "Y"])

    # Listeners may emit (that's how reactions work).
    def test_listeners_may_emit(self):
        bus, got = events.EventBus(), []
        bus.add_sink(got.append)
        bus.add_listener(lambda e: bus.say("🎮 connected"), kinds=["console.joined"])
        bus.fact("console.joined", mac="aa")
        self.assertEqual([e.kind for e in got], ["console.joined", "say"])

# The controls registry.
class ControlsRegistry(unittest.TestCase):
    # Register, list what's available, call; unknown names and bad input are reported clearly.
    def test_add_available_call(self):
        c = controls.Controls()
        state = {"ftp": False}
        c.add("stop", lambda p: {"ok": True, "message": "Stopping…"})
        c.add("open_filezilla", lambda p: None, available=lambda: state["ftp"])
        self.assertEqual(c.available(), ["stop"])
        state["ftp"] = True
        self.assertEqual(c.available(), ["open_filezilla", "stop"])
        self.assertEqual(c.call("stop")["message"], "Stopping…")
        self.assertEqual(c.call("open_filezilla"), {"ok": True})
        with self.assertRaises(KeyError):
            c.call("self_destruct")

    # Not available right now -> ControlError (HTTP 400), not a crash.
    def test_unavailable_is_a_control_error(self):
        c = controls.Controls()
        c.add("open_filezilla", lambda p: None, available=lambda: False)
        with self.assertRaises(controls.ControlError):
            c.call("open_filezilla")

    # Duplicate or strange names are refused.
    def test_names(self):
        c = controls.Controls()
        c.add("stop", lambda p: None)
        with self.assertRaises(ValueError):
            c.add("stop", lambda p: None)
        with self.assertRaises(ValueError):
            c.add("../evil", lambda p: None)

# Durations, plurals, MAC addresses, the new-console snippet.
class Formatting(unittest.TestCase):
    # MAC addresses: leading zeros restored, lower case.
    def test_norm_mac(self):
        self.assertEqual(fmt.norm_mac("A:1:2:3:4:5"), "0a:01:02:03:04:05")
        self.assertEqual(fmt.norm_mac("48:F1:EB:5A:22:C2"), "48:f1:eb:5a:22:c2")
        self.assertEqual(fmt.norm_mac("48-f1-eb-5a-22-c2"), "48:f1:eb:5a:22:c2")

    # Technical durations, as in the reference log ('8s', '34s', '3m 42s').
    def test_fmt_duration(self):
        self.assertEqual(fmt.fmt_duration(8), "8s")
        self.assertEqual(fmt.fmt_duration(222), "3m 42s")
        self.assertEqual(fmt.fmt_duration(3600), "1h 00m 00s")
        self.assertEqual((fmt.fmt_duration(8.6), fmt.casual_duration(8.6)), ("9s", "9 seconds"))

    # Casual durations.
    def test_casual_duration(self):
        self.assertEqual(fmt.casual_duration(1), "1 second")
        self.assertEqual(fmt.casual_duration(21), "21 seconds")
        self.assertEqual(fmt.casual_duration(60), "1 minute")
        self.assertEqual(fmt.casual_duration(222), "3 min 42 s")
        self.assertEqual(fmt.casual_duration(3900), "1 h 5 min")

    # Plurals.
    def test_plural(self):
        self.assertEqual(fmt.plural(1, "lookup"), "1 lookup")
        self.assertEqual(fmt.plural(5, "lookup"), "5 lookups")
        self.assertEqual(fmt.plural(2, "entry", "entries"), "2 entries")

    # Errors in words: their kind, then their message; a traceback only for the unexpected ones (or always, for a bug), or
    # the one the keeper sent along.
    def test_errors_in_words(self):
        try:
            {}["name"]
        except KeyError as e:
            self.assertEqual(fmt.error_text(e), "KeyError: 'name'")
            self.assertEqual(fmt.error_trace(e)[-1], "KeyError: 'name'")
        try:
            raise OSError(48, "Address already in use")
        except OSError as e:
            self.assertEqual(fmt.error_text(e), "OSError: [Errno 48] Address already in use")
            self.assertIsNone(fmt.error_trace(e))                    # macOS's own words are the story
            self.assertEqual(fmt.error_trace(e, always=True)[-1], "OSError: [Errno 48] Address already in use")
        self.assertEqual(fmt.error_text(RuntimeError()), "RuntimeError")
        sent = RuntimeError("KeyError: 'Wi-Fi'")
        sent.remote_trace = ["Traceback (most recent call last):", "KeyError: 'Wi-Fi'"]
        self.assertEqual(fmt.error_trace(sent), sent.remote_trace)

    # What kind of Mac this is, in one line; a fact that couldn't be read says "?", Rosetta is named.
    def test_mac_line(self):
        facts = {"macos": "15.0", "build": "24A000", "model": "Mac00,0", "chip": "Apple M0", "arch": "x86_64", "rosetta": "yes",
                 "python": "3.9.6", "python_path": "/usr/bin/python3", "language": "fr-CA (region fr_CA)", "managed": "no"}
        self.assertEqual(fmt.mac_line(facts), "macOS 15.0 (24A000), model Mac00,0, Apple M0, x86_64 under Rosetta, Python 3.9.6 (/usr/bin/python3), "
                         "language fr-CA (region fr_CA), managed by an organization: no")
        self.assertTrue(fmt.mac_line({}).startswith("macOS ? (?), model ?, ?, ?, Python ?"))

    # The ready-to-paste entry is exactly the one in the reference log.
    def test_console_snippet_matches_reference(self):
        reference = read_text(REFERENCE)
        self.assertIn(fmt.console_snippet_json("0a:01:02:03:04:05"), reference)

# Project-relative paths.
class Paths(unittest.TestCase):
    # The project folder is found from the package's location.
    def test_project_dir(self):
        self.assertTrue(os.path.exists(os.path.join(paths.PROJECT_DIR, "nx_spoof.py")))
        self.assertEqual(paths.project_path("logs"), os.path.join(paths.PROJECT_DIR, "logs"))

    # Paths inside the project are shown relative; outside ones stay absolute.
    def test_display_path(self):
        self.assertEqual(paths.display_path(paths.project_path("logs", "x.log")), os.path.join("logs", "x.log"))
        self.assertEqual(paths.display_path("/tmp/elsewhere.log"), "/tmp/elsewhere.log")

if __name__ == "__main__":
    unittest.main()
