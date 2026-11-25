"""
test_dashboard.py - unit tests for nxspoof.dashboard: the server's safety rules, its endpoints and
live stream, the snapshot, and the "add console" control.

The server runs for real on 127.0.0.1 (a high port, no root needed). The page itself is checked in a
real browser during development; here only what the server sends.
"""

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from nxspoof.consoles.activity import ConsoleActivity
from nxspoof.consoles.registry import ConsoleRegistry
from nxspoof.core.controls import ControlError, Controls
from nxspoof.core.events import Event, EventBus
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.dashboard import controls as dashboard_controls
from nxspoof.dashboard import server
from nxspoof.dashboard.server import DashboardServer, DashboardSink
from nxspoof.dashboard.state import build_state
from nxspoof.features import services
from nxspoof.settings import schema
from nxspoof.settings.store import SettingsStore
from tests.marks import unmarked_references

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")
REFERENCE_STATE = os.path.join(PROJECT_DIR, "tests", "fixtures", "reference", "run.state.json")
PORT = 8894
S1, S2, UNKNOWN = "dc:68:eb:5a:11:c1", "48:f1:eb:5a:22:c2", "0a:01:02:03:04:05"

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# One request to the dashboard -> (status, body text, headers). host: the Host header sent.
def request(path, data=None, token=None, host="localhost:%d" % PORT):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data, method="POST" if data is not None else "GET",
                                 headers={"Host": host, "X-NXSPOOF-Token": token or "", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read().decode("utf-8"), r.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8"), e.headers
        finally:
            e.close()

# The server running on a high port with a tiny state and two controls.
class ServerTest(unittest.TestCase):
    # Start it.
    def setUp(self):
        self.controls = Controls()
        self.controls.add("echo", lambda payload: {"ok": True, "message": "got %s" % payload.get("x")})

        # A control that refuses (bad input).
        def refuse(payload):
            raise ControlError("not like that")
        self.controls.add("refuse", refuse)
        self.controls.add("crash", lambda payload: 1 / 0)
        self.dash = DashboardServer(PORT, lambda: {"run": {"phase": "running"}, "consoles": [], "actions": self.controls.available()}, self.controls)
        self.dash.start()

    # Stop it.
    def tearDown(self):
        self.dash.stop(linger=0)

    # The token from the page, as the page's script reads it.
    def token(self):
        page = request("/")[1]
        return page.split('name="nxspoof-token" content="', 1)[1].split('"', 1)[0]

# Safety and endpoints.
class Server(ServerTest):
    # The page has this run's token and the no-inline-scripts rule; its files are served with the right types.
    def test_page_and_files(self):
        status, page, headers = request("/")
        self.assertEqual(status, 200)
        self.assertEqual(self.token(), self.dash.token)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        for path, ctype in (("/css/dashboard.css", "text/css"), ("/js/app.js", "text/javascript"), ("/js/core.js", "text/javascript")):
            status, _, headers = request(path)
            self.assertEqual((status, headers["Content-Type"].split(";")[0]), (200, ctype), path)
        # The browser tab's icon (tools/icon.py): a real PNG, and the page asks for it.
        req = urllib.request.Request("http://127.0.0.1:%d/favicon.png" % PORT, headers={"Host": "localhost:%d" % PORT})
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual((r.headers["Content-Type"], r.read()[:8]), ("image/png", b"\x89PNG\r\n\x1a\n"))
        self.assertIn('href="favicon.png"', request("/")[1])
        # And favicon.ico (Safari looks for it): an icon file whose first image is a PNG.
        req = urllib.request.Request("http://127.0.0.1:%d/favicon.ico" % PORT, headers={"Host": "localhost:%d" % PORT})
        with urllib.request.urlopen(req, timeout=3) as r:
            ctype, data = r.headers["Content-Type"], r.read()
        self.assertEqual((ctype, data[:4], data[6 + 16 * data[4]:][:8]), ("image/x-icon", b"\x00\x00\x01\x00", b"\x89PNG\r\n\x1a\n"))
        self.assertIn('href="favicon.ico"', request("/")[1])
        # The header's logo, the same "ok" (tools/icon.py logo.png).
        req = urllib.request.Request("http://127.0.0.1:%d/logo.png" % PORT, headers={"Host": "localhost:%d" % PORT})
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual((r.headers["Content-Type"], r.read()[:8]), ("image/png", b"\x89PNG\r\n\x1a\n"))
        self.assertIn('src="logo.png"', request("/")[1])

    # Nothing outside web/ is reachable, nor hidden files or unknown types.
    def test_nothing_else_on_disk(self):
        for path in ("/../server.py", "/js/../../server.py", "/%2e%2e/server.py", "/.hidden", "/js/", "/nope.js", "/../../../settings.json"):
            self.assertEqual(request(path)[0], 404, path)
        self.assertIsNone(self.dash.static_file("/js/../../state.py"))

    # The docs pages (📖 Help) come from the docs folder it was given, under /docs/, read only: no token in them, the same
    # no-inline-scripts rule, nothing outside that folder; without a docs folder, /docs/ is a 404.
    def test_docs_pages(self):
        self.assertEqual(request("/docs/")[0], 404)
        self.dash.docs_dir = os.path.join(PROJECT_DIR, "docs")
        for path in ("/docs", "/docs/", "/docs/index.html"):
            status, page, headers = request(path)
            self.assertEqual(status, 200, path)
            self.assertIn("<!-- generated: nav -->", page)
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(request("/docs/docs.js")[2]["Content-Type"].split(";")[0], "text/javascript")
        req = urllib.request.Request("http://127.0.0.1:%d/docs/images/icon.png" % PORT, headers={"Host": "localhost:%d" % PORT})
        with urllib.request.urlopen(req, timeout=3) as r:                     # a picture in a docs subfolder (the home page's icon)
            self.assertEqual((r.status, r.headers["Content-Type"]), (200, "image/png"))
        self.assertNotIn(self.dash.token, request("/docs/reference.html")[1])
        for path in ("/docs/../nxspoof/app/run.py", "/docs/%2e%2e/README.md", "/docs/.hidden", "/docs/nope.html"):
            self.assertEqual(request(path)[0], 404, path)
        self.assertIsNone(self.dash.static_file("/docs/../tools/docs.py"))

    # Only Host: localhost / 127.0.0.1 is answered (DNS-rebinding protection).
    def test_host_check(self):
        self.assertEqual(request("/api/state", host="evil.example:%d" % PORT)[0], 403)
        self.assertEqual(request("/api/state", host="127.0.0.1:%d" % PORT)[0], 200)

    # It listens on 127.0.0.1 only.
    def test_loopback_only(self):
        self.assertEqual(self.dash.httpd.server_address[0], "127.0.0.1")

    # Controls: token needed (403); unknown 404; bad input 400 with the reason; a bug 500; fine 200.
    def test_controls(self):
        token = self.token()
        self.assertEqual(request("/api/action/echo", b'{"x": 1}')[0], 403)
        self.assertEqual(request("/api/action/echo", b'{"x": 1}', token="wrong")[0], 403)
        self.assertEqual(json.loads(request("/api/action/echo", b'{"x": 1}', token)[1])["message"], "got 1")
        self.assertEqual(request("/api/action/nope", b"{}", token)[0], 404)
        status, body, _ = request("/api/action/refuse", b"{}", token)
        self.assertEqual((status, json.loads(body)["error"]), (400, "not like that"))
        self.assertEqual(request("/api/action/crash", b"{}", token)[0], 500)
        self.assertEqual(request("/api/action/echo", b"not json", token)[0], 400)
        self.assertEqual(request("/api/action/echo", b"[1, 2]", token)[0], 400)
        for length, status in (("abc", 400), (str(server.MAX_BODY + 1), 413)):        # a body's size: unreadable, too big
            with socket.create_connection(("127.0.0.1", PORT), timeout=3) as s:
                s.sendall(("POST /api/action/echo HTTP/1.1\r\nHost: localhost:%d\r\nX-NXSPOOF-Token: %s\r\nContent-Length: %s\r\n\r\n{}"
                           % (PORT, token, length)).encode())
                self.assertEqual(s.recv(64).split(b" ")[1], str(status).encode(), length)

    # History and the live stream: events as they happen, "bye" at the end.
    def test_history_and_stream(self):
        self.dash.publish({"tech": "DNS   early", "casual": None})
        self.assertEqual(json.loads(request("/api/history")[1])[0]["tech"], "DNS   early")
        received = []
        s = socket.create_connection(("127.0.0.1", PORT), timeout=3)
        s.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost:%d\r\n\r\n" % PORT)

        # Read the stream until "bye".
        def reader():
            buf = b""
            while b"event: bye" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            received.append(buf.decode("utf-8"))
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        for _ in range(50):
            if self.dash.subscribers:
                break
            time.sleep(0.02)
        self.dash.publish({"tech": "WIFI  live", "casual": "🎮 live"})
        self.dash.stop(linger=0.2)
        t.join(3)
        s.close()
        self.assertIn('"tech": "WIFI  live"', received[0])
        self.assertIn("event: bye", received[0])

    # A page that hangs up (its tab, window or browser closed) is gone at once, not at the next ping.
    def test_closed_page_noticed_at_once(self):
        s = socket.create_connection(("127.0.0.1", PORT), timeout=3)
        s.sendall(b"GET /api/events HTTP/1.1\r\nHost: localhost:%d\r\n\r\n" % PORT)
        for _ in range(50):
            if self.dash.viewers():
                break
            time.sleep(0.02)
        self.assertEqual(self.dash.viewers(), 1)
        s.close()
        start = time.time()
        while self.dash.viewers() and time.time() - start < 3:
            time.sleep(0.02)
        self.assertEqual(self.dash.viewers(), 0)
        self.assertLess(time.time() - start, 1.0, "noticed within LOOK seconds, well before the next ping")

# Bus events -> page events.
class Sink(unittest.TestCase):
    # One page event per line (details as their own lines); casual-only lines too; facts never.
    def test_items(self):
        ts = time.mktime((2026, 11, 14, 9, 0, 0, 0, 0, -1))
        items = DashboardSink.items(Event("log", "PROBLEM", "x failed", level="problem", details=["why"], ts=ts))
        self.assertEqual([(i["t"], i["tech"], i["problem"]) for i in items], [("09:00:00", "PROBLEM x failed", True), ("09:00:00", "      why", True)])
        self.assertEqual(DashboardSink.items(Event("say", casual="🎮 hi", mac=S2, ts=ts))[0]["casual"], "🎮 hi")
        self.assertEqual(DashboardSink.items(Event("console.online", mac=S2)), [])

# The snapshot and the add-console control.
class StateAndControls(unittest.TestCase):
    # A copy of the real settings in a temporary folder.
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nxspoof-test-")
        self.path = os.path.join(self.dir, "settings.json")
        shutil.copy(FIXTURE, self.path)
        self.store = SettingsStore(self.path)
        self.store.load()
        self.registry = ConsoleRegistry(self.store)
        self.activity = ConsoleActivity()
        self.bus = EventBus()
        self.lines, self.events = [], []
        self.bus.add_sink(lambda e: self.lines.append(e.technical_line()))
        self.bus.add_sink(self.events.append)
        self.controls = Controls()
        dashboard_controls.register(self.controls, self.bus, self.store, self.registry, self.activity,
                                    clock=lambda: time.mktime((2026, 11, 14, 9, 30, 0, 0, 0, -1)))

    # Remove the folder.
    def tearDown(self):
        shutil.rmtree(self.dir)

    # Known consoles first (settings order), then unknown ones seen; same fields as the current dashboard's.
    def test_state_shape(self):
        self.activity.saw_ip("192.168.2.3", S2)
        self.activity.mark_connected(S2)
        self.activity.joined(UNKNOWN, 100.0)
        st = build_state({"phase": "running"}, self.registry, self.activity, self.store, self.controls, now=200.0)
        self.assertEqual([c["mac"] for c in st["consoles"]], [S1, S2, UNKNOWN])
        with open(REFERENCE_STATE, encoding="utf-8") as f:
            reference = json.load(f)["before_stop"]["consoles"][0]
        self.assertEqual(sorted(st["consoles"][0]), sorted(reference))
        self.assertEqual(st["consoles"][0]["settings"]["name"], "Switch 1")
        unknown = st["consoles"][2]
        self.assertEqual((unknown["known"], unknown["color"], unknown["seen"]), (False, "yellow", True))
        self.assertIn('"mac": "0a:01:02:03:04:05"', unknown["snippet"])
        self.assertIn("DNS", st["tags"])
        self.assertEqual(st["actions"], ["add_console", "edit_console", "remove_console", "set_settings"])
        rows = {r["name"]: r for r in st["settings"]}
        self.assertEqual(len(rows), 7)                                        # D30: run-only values are --flags, not settings
        self.assertEqual(rows["bridge.address"]["choices"], ["172.16.0.1", "10.254.0.1", "192.168.254.1"])
        self.assertEqual((rows["ftp.port"]["apply"], rows["ftp.port"]["min"], rows["ftp.port"]["saved"]), ("live", 1, 5000))

    # Add a console seen this run: saved (tidy rewrite), recognized at once, announced.
    def test_add_console(self):
        self.activity.joined(UNKNOWN, 100.0)
        answer = self.controls.call("add_console", {"mac": UNKNOWN.upper(), "name": "  Max's   Switch ", "model": "Switch OLED (Mariko)", "color": "cyan"})
        self.assertEqual(answer["message"], "Saved 'Max's Switch' to settings.json")
        self.assertEqual(self.registry.name(UNKNOWN), "Max's Switch")
        saved = json.loads(read_text(self.path))["consoles"][-1]
        self.assertEqual((saved["mac"], saved["color"], saved["model"], saved["notes"]),
                         (UNKNOWN, "cyan", "Switch OLED (Mariko)", ["Added from the dashboard on 2026-11-14 09:30."]))
        self.assertIn("SETTINGS added console Max's Switch (0a:01:02:03:04:05) to settings.json from the dashboard", self.lines)

    # A console that never connected can be added by its MAC (the "Add a console" form).
    def test_add_console_by_mac(self):
        answer = self.controls.call("add_console", {"mac": "AA-BB-CC-DD-EE-FF", "name": "Friend's Switch", "color": "green"})
        self.assertEqual(answer["message"], "Saved 'Friend's Switch' to settings.json")
        self.assertEqual(self.registry.name("aa:bb:cc:dd:ee:ff"), "Friend's Switch")

    # Refusals, each with its reason.
    def test_add_console_refusals(self):
        self.activity.joined(UNKNOWN, 100.0)
        self.activity.joined(S2, 100.0)
        cases = [({"mac": "not a mac", "name": "X"}, "isn't a MAC address"), ({"mac": S2, "name": "X"}, "already in settings.json"),
                 ({"mac": UNKNOWN, "name": " "}, "give it a name"), ({"mac": UNKNOWN, "name": "X", "color": "pink"}, "color must be"),
                 ({"mac": UNKNOWN, "name": "switch 2"}, "already"), ({"mac": UNKNOWN, "name": "X", "model": "OLED"}, "model must be one of")]
        for payload, reason in cases:
            with self.assertRaises(ControlError) as e:
                self.controls.call("add_console", payload)
            self.assertIn(reason, str(e.exception), payload)
        self.assertEqual(len(json.loads(read_text(self.path))["consoles"]), 2)

    # Edit: only real changes are saved; the log line lists them; names follow at once; an untouched form changes nothing.
    def test_edit_console(self):
        st = build_state({}, self.registry, self.activity, self.store, self.controls)["consoles"][1]["settings"]
        self.assertEqual(self.controls.call("edit_console", dict(st, mac=S2, notes="\n".join(st["notes"])))["message"], "Nothing changed.")
        answer = self.controls.call("edit_console", {"mac": S2, "name": "Big  Switch", "color": "cyan", "ftp_port": "5001",
                                                     "notes": "first line\n\n second line ", "model": st["model"]})
        self.assertEqual(answer["message"], "Saved the changes to 'Big Switch'")
        self.assertEqual(self.registry.name(S2), "Big Switch")
        saved = [c for c in json.loads(read_text(self.path))["consoles"] if c["mac"] == S2][0]
        self.assertEqual((saved["color"], saved["ftp"], saved["notes"]), ("cyan", {"port": 5001}, ["first line", "second line"]))
        self.assertIn("SETTINGS changed console Switch 2 (48:f1:eb:5a:22:c2) from the dashboard: color magenta -> cyan, "
                      "FTP port 5000 -> 5001, name Switch 2 -> Big Switch, notes (3 -> 2 lines)", self.lines)
        changed = [e for e in self.events if e.tech and e.tech.startswith("changed console")][0]
        line = changed.technical_line()
        self.assertEqual([(line[s:e], who) for s, e, who in changed.technical_names()], [("Switch 2", S2)])   # the reference, not the values
        self.assertEqual([(changed.casual[s:e], who) for s, e, who in changed.casual_names], [("Big Switch", S2)])

    # The model: picked from the list (sent with the state); a hand-written current text is kept, never re-typed into another.
    def test_models(self):
        st = build_state({}, self.registry, self.activity, self.store, self.controls)
        self.assertEqual([m["name"] for m in st["models"]], schema.MODEL_NAMES)
        self.assertIn("Switch V2 (Mariko)", schema.MODEL_NAMES)
        current = st["consoles"][1]["settings"]["model"]
        self.assertNotIn(current, schema.MODEL_NAMES, "the fixture keeps a custom model text")
        self.assertEqual(self.controls.call("edit_console", {"mac": S2, "model": current})["message"], "Nothing changed.")
        self.controls.call("edit_console", {"mac": S2, "model": "Switch 2"})
        self.assertIn("model %s -> Switch 2" % current, self.lines[-1])
        self.controls.call("edit_console", {"mac": S2, "model": ""})
        self.assertEqual([c for c in json.loads(read_text(self.path))["consoles"] if c["mac"] == S2][0]["model"], "")   # "not set"

    # Edit refusals, each with its reason; nothing written.
    def test_edit_refusals(self):
        before = read_text(self.path)
        cases = [({"mac": UNKNOWN, "name": "X"}, "isn't in settings.json"), ({"mac": S2, "name": "  "}, "give it a name"),
                 ({"mac": S2, "color": "pink"}, "color must be"), ({"mac": S2, "ftp_port": "ftp"}, "must be a number"),
                 ({"mac": S2, "ftp_port": "70000"}, "between 1 and 65535"), ({"mac": S2, "name": "switch 1"}, "already called"),
                 ({"mac": S2, "model": "Switch 3"}, "model must be one of")]
        for payload, reason in cases:
            with self.assertRaises(ControlError) as e:
                self.controls.call("edit_console", payload)
            self.assertIn(reason, str(e.exception), payload)
        self.assertEqual(read_text(self.path), before)

    # Remove: out of settings.json; a console seen this run becomes unknown and can be added back.
    def test_remove_then_add_back(self):
        self.activity.joined(S1, 100.0)
        answer = self.controls.call("remove_console", {"mac": S1})
        self.assertIn("Removed 'Switch 1' from settings.json. It connected during this run", answer["message"])
        self.assertFalse(self.registry.known(S1))
        self.assertEqual([c["mac"] for c in json.loads(read_text(self.path))["consoles"]], [S2])
        self.assertIn("SETTINGS removed console Switch 1 (dc:68:eb:5a:11:c1) from settings.json from the dashboard", self.lines)
        unknown = build_state({}, self.registry, self.activity, self.store, self.controls)["consoles"][-1]
        self.assertEqual((unknown["mac"], unknown["known"], "snippet" in unknown), (S1, False, True))
        self.controls.call("add_console", {"mac": S1, "name": "Switch 1", "color": "green"})
        self.assertEqual(self.registry.name(S1), "Switch 1")
        self.assertEqual(unmarked_references(self.events, self.registry), [])
        with self.assertRaises(ControlError):
            self.controls.call("remove_console", {"mac": UNKNOWN})

    # Configure's Save: typed from the page, checked, only real changes saved; one line per change saying when it takes effect.
    def test_set_settings(self):
        facts = []
        self.bus.add_listener(facts.append, kinds=["settings.changed"])
        self.assertEqual(self.controls.call("set_settings", {"values": {"ftp.port": "5000"}})["message"], "Nothing changed.")
        answer = self.controls.call("set_settings", {"values": {"ftp.check_every_seconds": "2.5", "ftp.port": "5000"}})
        self.assertEqual((answer["message"], answer["saved"]), ("Saved 1 change.", ["ftp.check_every_seconds"]))
        self.assertIn("SETTINGS changed ftp.check_every_seconds 3 -> 2.5 from the dashboard (applies now)", self.lines)
        self.controls.call("set_settings", {"values": {"ftp.watch": False}})
        self.assertEqual(json.loads(read_text(self.path))["ftp"]["watch"], False)
        self.assertIn("SETTINGS changed ftp.watch true -> false from the dashboard (saved; takes effect at the next start)", self.lines)
        self.assertEqual([(f.data["name"], f.data["value"]) for f in facts], [("ftp.check_every_seconds", 2.5), ("ftp.watch", False)])
        self.assertEqual(facts[-1].technical_line(), "SETTINGS changed ftp.watch true -> false from the dashboard "
                         "(saved; takes effect at the next start)", "the line IS the announcement: one event")

    # An option given at start wins for the run: the change is saved for the next start, and the log says so.
    def test_set_setting_overridden_by_an_option(self):
        controls = Controls()
        dashboard_controls.register(controls, self.bus, self.store, self.registry, self.activity,
                                    overridden_by=lambda name: "--no-ftp-watch" if name == "ftp.watch" else None)
        controls.call("set_settings", {"values": {"ftp.watch": "false"}})
        self.assertIn("this run keeps --no-ftp-watch, the change applies from the next start", self.lines[-1])

    # Refusals, each "name: reason"; all or nothing: one wrong value and none of them is written.
    def test_set_settings_refusals(self):
        before = read_text(self.path)
        cases = [({"nope": 1}, "nope: no such setting"), ({"ftp.watch": "maybe"}, "ftp.watch: must be on or off"),
                 ({"ftp.port": "ftp"}, "ftp.port: must be a number"), ({"ftp.port": "50.5"}, "ftp.port: must be a whole number"),
                 ({"ftp.port": 0}, "ftp.port: must be at least 1, got 0"),
                 ({"bridge.address": "fancy"}, "bridge.address: must be one of"),
                 ({"logs.keep_last": 5, "ftp.port": 0, "ftp.watch": "maybe"}, "ftp.port: must be at least 1, got 0"), ({}, "nothing to save")]
        for values, reason in cases:
            with self.assertRaises(ControlError) as e:
                self.controls.call("set_settings", {"values": values})
            self.assertIn(reason, str(e.exception), values)
            self.assertNotIn("(using", str(e.exception))
        self.assertEqual(read_text(self.path), before)

    # The FTP watcher follows: a new port starts it over; turning watching off stops it.
    def test_watcher_follows_console_changes(self):
        svc = services.Services(self.bus, self.registry, self.activity, background=False)
        svc.attach()
        self.bus.fact("console.address", mac=S2, ip="192.168.2.3")
        first = svc.watchers[S2]
        self.controls.call("edit_console", {"mac": S2, "model": "Switch 2"})
        self.assertIs(svc.watchers[S2], first)
        self.controls.call("edit_console", {"mac": S2, "ftp_port": "5001"})
        self.assertTrue(first._stop.is_set())
        self.assertEqual(svc.watchers[S2].url, "ftp://192.168.2.3:5001")
        text = [l for l in self.lines if l]                                    # facts have no text
        watching = text.index("FTP   watching Switch 2 for an FTP server on port 5001 (start it on the console)")
        self.assertTrue(text[watching - 1].startswith("SETTINGS changed console Switch 2"), "cause first, consequence after")
        self.controls.call("set_settings", {"values": {"ftp.check_every_seconds": 7}})      # global, not overridden by the console
        self.assertEqual(svc.watchers[S2].every, 7.0)
        self.store.update_console(S2, {"ftp": {"port": 5001, "watch": False}})        # the store is quiet: the caller announces
        self.assertIn(S2, svc.watchers)
        self.bus.log("SETTINGS", "changed console Switch 2 (test)", mac=S2, fact="console.changed", changed=["ftp"])
        self.assertNotIn(S2, svc.watchers)

if __name__ == "__main__":
    unittest.main()
