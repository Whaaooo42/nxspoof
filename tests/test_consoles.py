"""
test_consoles.py - unit tests for nxspoof.consoles: registry, activity, tracker.

The tracker replays the reference scenario and must produce the recorded DHCP / WIFI / NEW /
DNS / HTTP log lines word for word (tests/fixtures/reference/session.log).
"""

import os
import re
import unittest

from nxspoof.consoles import activity as activity_module
from nxspoof.consoles import registry as registry_module
from nxspoof.consoles import tracker as tracker_module
from nxspoof.core.events import EventBus
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.net.policy import Decision
from nxspoof.settings import store

FIXTURE = os.path.join(PROJECT_DIR, "tests", "fixtures", "settings_current.json")
REFERENCE = os.path.join(PROJECT_DIR, "tests", "fixtures", "reference", "session.log")
S1, S2, UNKNOWN = "dc:68:eb:5a:11:c1", "48:f1:eb:5a:22:c2", "0a:01:02:03:04:05"
S2_IP, MAC_IP = "127.0.0.1", "192.168.2.1"
UA = "libcurl (nnNifm; bf4812d9-d89b-4804-b53a-d08e0bed634f; SDK 23.3.0.0; Add-on 23.3.0.0)"
TEST2 = Decision("test", "connection test", "Switch 2 connection test")
BLOCK = Decision("block", "default", None)

# Read a text file and close it.
def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

# A registry on the real settings (copy), with the Mac's address set.
def make_registry():
    s = store.SettingsStore(FIXTURE)
    s.load()
    return registry_module.ConsoleRegistry(s, mac_ip=MAC_IP)

# Who is who.
class Registry(unittest.TestCase):
    # Names, colors, known/unknown, from settings.json.
    def test_lookup(self):
        r = make_registry()
        self.assertEqual((r.name(S2), r.color(S2), r.known(S2)), ("Switch 2", "magenta", True))
        self.assertEqual((r.name(UNKNOWN), r.color(UNKNOWN), r.known(UNKNOWN)), ("Unknown console", "yellow", False))

    # Labels exactly as recorded (without colors).
    def test_labels(self):
        r = make_registry()
        self.assertEqual(r.label(mac=S2, with_mac=True), "Switch 2 (48:f1:eb:5a:22:c2)")
        self.assertEqual(r.label(mac=S2, ip="192.168.2.3"), "Switch 2 (192.168.2.3)")
        self.assertEqual(r.label(ip=MAC_IP), "Mac (self-test)")
        self.assertEqual(r.label(ip="192.168.2.9"), "192.168.2.9")
        self.assertEqual(r.label(mac=UNKNOWN, with_mac=True), "Unknown console (0a:01:02:03:04:05)")

    # FTP settings: global section merged with the console's own.
    def test_ftp_settings(self):
        self.assertEqual(make_registry().ftp_settings(S2)["port"], 5000)

# What each console did.
class Activity(unittest.TestCase):
    # Connected and online happen once per visit; leaving resets them.
    def test_visits(self):
        a = activity_module.ConsoleActivity()
        self.assertTrue(a.mark_connected(S2))
        self.assertFalse(a.mark_connected(S2))
        self.assertTrue(a.web_request(S2, "ctest.p01.ctest.srv.nintendo.net", UA, "Switch 2 connection test"))
        self.assertFalse(a.web_request(S2, "ctest.p01.ctest.srv.nintendo.net", UA, "Switch 2 connection test"))
        a.joined(S2, 100.0)
        a.left(S2, 134.0)
        self.assertTrue(a.mark_connected(S2))
        self.assertEqual(a.record(S2).visits, 2)

    # One time-on-Wi-Fi calculation: closed visits plus the open one up to now.
    def test_time_on_wifi(self):
        a = activity_module.ConsoleActivity(clock=lambda: 200.0)
        a.joined(S2, 100.0)
        a.left(S2, 134.0)
        a.joined(S2, 190.0)
        self.assertEqual(a.time_on_wifi(S2), 44.0)
        self.assertEqual(a.time_on_wifi(S2, now=195.0), 39.0)

    # Blocked lookups and blocked web requests are counted together.
    def test_blocked(self):
        a = activity_module.ConsoleActivity()
        a.count_dns(S2, "Dauth.p01.lp1.ndas.srv.nintendo.net", False)
        a.count_dns(S2, "ctest.cdn.nintendo.net", True)
        a.web_request(S2, "api.github.com", "UA", None)
        self.assertEqual(a.blocked_total(S2), 2)
        self.assertEqual(dict(a.record(S2).blocked), {"dauth.p01.lp1.ndas.srv.nintendo.net": 1, "http://api.github.com": 1})

    # Service events: only changes count (no "down" before any "up").
    def test_services(self):
        a = activity_module.ConsoleActivity()
        self.assertFalse(a.service_event(S2, "FTP", False))
        self.assertTrue(a.service_event(S2, "FTP", True, "ftp://x:5000", ts=1.0))
        self.assertFalse(a.service_event(S2, "FTP", True))
        self.assertTrue(a.service_event(S2, "FTP", False, ts=2.0))
        self.assertEqual(a.record(S2).services["FTP"], [(1.0, "UP"), (2.0, "DOWN")])

    # IPs are resolved from what was seen, else from the lease lookup.
    def test_resolve(self):
        a = activity_module.ConsoleActivity(leases_lookup=lambda ip: "A:1:2:3:4:5" if ip == "192.168.2.9" else None)
        self.assertIsNone(a.resolve("192.168.2.8"))
        self.assertEqual(a.resolve("192.168.2.9"), UNKNOWN)
        self.assertTrue(a.saw_ip("192.168.2.3", S2))
        self.assertFalse(a.saw_ip("192.168.2.3", S2))
        self.assertEqual(a.resolve("192.168.2.3"), S2)

# Observations -> log lines + facts.
class Tracker(unittest.TestCase):
    # Replay the reference scenario's observations; returns (technical lines, facts).
    def replay(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        t = tracker_module.ConsoleTracker(bus, make_registry(), activity_module.ConsoleActivity(), answer_ip=MAC_IP)
        t.on_dhcp(S2_IP, S2)
        t.on_wifi_join(S2, 1000.0)
        t.on_dns(S2_IP, "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, MAC_IP)
        t.on_http(S2_IP, "GET", "ctest.p01.ctest.srv.nintendo.net", "/", UA, TEST2)
        for name in ("dauth.p01.lp1.ndas.srv.nintendo.net", "sun.p01.lp1.d4c.srv.nintendo.net",
                     "dauth.p01.lp1.ndas.srv.nintendo.net", "api.p01.lp1.ctest.srv.nintendo.net"):
            t.on_dns(S2_IP, name, "A", BLOCK, "0.0.0.0")
        t.on_http(S2_IP, "GET", "api.github.com", "/repos/x", "Mozilla/5.0 (Nintendo Switch)", BLOCK)
        t.on_wifi_join(S1, 1016.0)
        t.on_wifi_leave(S1, "disconnected", 1016.0, 1024.0)
        t.on_wifi_join(UNKNOWN, 1026.0)
        t.on_wifi_leave(S2, "disconnected", 1000.0, 1034.0)
        t.on_wifi_join(S2, 1036.0)
        t.on_dns(S2_IP, "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, MAC_IP)
        t.on_http(S2_IP, "GET", "ctest.p01.ctest.srv.nintendo.net", "/", UA, TEST2)
        lines = [e.technical_line() for e in events if e.tech is not None]
        facts = [(e.kind, e.mac) + tuple(sorted(e.data.items())) for e in events if e.kind not in ("log", "say")]   # lines can be facts too
        return lines, facts

    # The same DHCP / WIFI / NEW / DNS / HTTP lines as the reference session, word for word.
    def test_lines_same_as_reference(self):
        reference = [re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", l) for l in read_text(REFERENCE).splitlines()]
        expected = [l for l in reference if l.split(" ", 1)[0] in ("DHCP", "WIFI", "NEW", "DNS", "HTTP")]
        lines, _ = self.replay()
        self.assertEqual(lines, expected)

    # Facts come once per visit: connected (visit 1, then 2 = "is back"), online, left, address, unknown, blocked.
    def test_facts(self):
        _, facts = self.replay()
        kinds = [(f[0], f[1]) for f in facts]
        self.assertEqual(kinds.count(("console.connected", S2)), 2)
        self.assertEqual(kinds.count(("console.online", S2)), 2)
        connected = [dict(f[2:]) for f in facts if f[0] == "console.connected" and f[1] == S2]
        self.assertEqual([c["visit"] for c in connected], [1, 2])
        self.assertIn(("console.unknown", UNKNOWN), kinds)
        self.assertIn(("console.left", S1), kinds)
        blocked = [dict(f[2:]) for f in facts if f[0] == "console.blocked"]
        self.assertEqual([b["total"] for b in blocked], [1, 2, 3, 4, 5])
        self.assertEqual(blocked[-1]["name"], "http://api.github.com")

    # An unknown console getting an address before macOS's Wi-Fi line: the address opens the visit ("joined", then NEW),
    # and macOS's late Wi-Fi line adds nothing.
    def test_unknown_dhcp_first(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        t = tracker_module.ConsoleTracker(bus, make_registry(), activity_module.ConsoleActivity(), answer_ip=MAC_IP)
        t.on_dhcp("192.168.2.7", UNKNOWN)
        t.on_wifi_join(UNKNOWN, 2.0)
        self.assertEqual([e.tag for e in events if e.tech], ["WIFI", "NEW", "DHCP"])
        self.assertIn("console.unknown", [e.kind for e in events])

    # DHCP giving the same address again in a visit says "again" (a renewal); a new visit or a new address doesn't.
    def test_dhcp_again(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        act = activity_module.ConsoleActivity(clock=lambda: 100.0)
        t = tracker_module.ConsoleTracker(bus, make_registry(), act, answer_ip=MAC_IP)
        t.on_dhcp("192.168.2.3", S2)
        t.on_dhcp("192.168.2.3", S2)
        t.on_dhcp("192.168.2.4", S2)
        t.on_wifi_leave(S2, "disconnected", 100.0, 160.0)
        t.on_dhcp("192.168.2.4", S2)
        dhcp = [e for e in events if e.tag == "DHCP"]
        self.assertEqual([e.tech.endswith(" again") for e in dhcp], [False, True, False, False])
        self.assertEqual(dhcp[1].tech, "gave address 192.168.2.3 to Switch 2 (48:f1:eb:5a:22:c2) again")
        self.assertEqual([(e.kind, e.data["again"]) for e in dhcp[:2]], [("console.address", False), ("console.address", True)])
        self.assertEqual(dhcp[1].tech_names, [(28, 36, S2)])

    # A known console's DNS before macOS's Wi-Fi line opens the visit; the time on the Wi-Fi counts from there.
    def test_dns_first(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        act = activity_module.ConsoleActivity(clock=lambda: 100.0)
        t = tracker_module.ConsoleTracker(bus, make_registry(), act, answer_ip=MAC_IP)
        act.saw_ip("192.168.2.3", S2)
        t.on_dns("192.168.2.3", "ctest.p01.ctest.srv.nintendo.net", "A", TEST2, MAC_IP)
        t.on_wifi_join(S2, 102.0)
        t.on_wifi_leave(S2, "disconnected", 102.0, 160.0)
        self.assertEqual([e.tag for e in events if e.tech], ["WIFI", "DNS", "WIFI"])
        self.assertIn("was on for 1m 00s", events[-2].tech if not events[-1].tech else events[-1].tech)
        self.assertEqual((act.record(S2).joined, act.on_wifi(S2)), ([100.0], False))

    # A probed name (--probe): its DNS line shows the Mac's address and isn't counted as blocked; its web line says
    # BLOCKED (403) with the request in full as detail lines.
    def test_probe_lines(self):
        bus = EventBus()
        events = []
        bus.add_sink(events.append)
        act = activity_module.ConsoleActivity()
        t = tracker_module.ConsoleTracker(bus, make_registry(), act, answer_ip=MAC_IP)
        act.saw_ip(S2_IP, S2)
        probe = Decision("probe", "--probe", "probe: sent to the Mac to see what comes")
        t.on_dns(S2_IP, "api.p01.lp1.ctest.srv.nintendo.net", "A", probe, MAC_IP)
        t.on_http(S2_IP, "POST", "api.p01.lp1.ctest.srv.nintendo.net", "/v1", UA, probe, ["Host: api.p01.lp1.ctest.srv.nintendo.net", "body (2 bytes): b'{}'"])
        dns_line, http_line = [e for e in events if e.tag in ("DNS", "HTTP")]
        self.assertIn("-> 192.168.2.1  (probe: sent to the Mac to see what comes)", dns_line.tech)
        self.assertEqual(dns_line.kind, "log", "not a console.blocked fact")
        self.assertIn("BLOCKED (403)  (probe: the request in full below)", http_line.tech)
        self.assertEqual(http_line.details, ["Host: api.p01.lp1.ctest.srv.nintendo.net", "body (2 bytes): b'{}'"])
        self.assertEqual(act.blocked_total(S2), 1, "only the refused web request counts as blocked")

    # Command-line overrides reach the console's FTP settings (the console's own entry still wins).
    def test_overrides(self):
        s = store.SettingsStore(FIXTURE)
        s.load()
        r = registry_module.ConsoleRegistry(s, overrides={"ftp.open_filezilla": True, "ftp.port": 2121})
        self.assertTrue(r.ftp_settings(S2)["open_filezilla"])
        self.assertEqual(r.ftp_settings(S2)["port"], 5000)

if __name__ == "__main__":
    unittest.main()
