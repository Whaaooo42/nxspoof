"""
test_net.py - unit tests for nxspoof.net: policy, DNS packets and server, web test server, self-test.

DNS replies are compared byte for byte with the ones recorded at the cutoff (tests/fixtures/golden.json); the servers run for real on
127.0.0.1 (high ports, no root needed).
"""

import os
import socket
import time
import unittest
import urllib.error
import urllib.request

from nxspoof.core.paths import PROJECT_DIR
from nxspoof.net import dns, policy, selftest, web
from tests import golden

MAC_IP = "192.168.2.1"
DNS_PORT, HTTP_PORT = 5398, 8097

# The answer function the DNS server uses (test names -> the Mac, everything else -> blocked).
def answer_for(name, qtype):
    return MAC_IP if policy.Policy().decide(name).action == "test" else policy.BLOCK_IP

# What may be reached.
class PolicyRules(unittest.TestCase):
    # Test names are answered (any case, trailing dot); everything else is blocked.
    def test_decide(self):
        p = policy.Policy()
        self.assertEqual(p.decide("CTEST.cdn.nintendo.net.").action, "test")
        self.assertEqual(p.decide("ctest.p01.ctest.srv.nintendo.net").label, "Switch 2 connection test")
        self.assertEqual(p.decide("dauth.p01.lp1.ndas.srv.nintendo.net"), policy.Decision("block", "default", None))

    # --probe: a probed name goes to the Mac (action "probe"); a test name stays a test, empty names are ignored, and
    # without --probe nothing is probed.
    def test_probe(self):
        p = policy.Policy(probe_hosts=["API.p01.lp1.ctest.srv.nintendo.net.", "", "ctest.cdn.nintendo.net"])
        self.assertEqual(p.probe_hosts, ["api.p01.lp1.ctest.srv.nintendo.net"])
        self.assertEqual(p.decide("api.p01.lp1.ctest.srv.nintendo.net").action, "probe")
        self.assertEqual(p.decide("ctest.cdn.nintendo.net").action, "test")
        self.assertEqual([p.answers_with_mac(n) for n in ("api.p01.lp1.ctest.srv.nintendo.net", "ctest.cdn.nintendo.net", "api.github.com")],
                         [True, True, False])
        self.assertEqual((policy.Policy().probe_hosts, policy.Policy().answers_with_mac("api.p01.lp1.ctest.srv.nintendo.net")), ([], False))

    # The same test names as recorded.
    def test_same_test_hosts_as_recorded(self):
        self.assertEqual(dict(policy.TEST_HOSTS), golden("test_hosts"))

# DNS packets.
class DnsPackets(unittest.TestCase):
    # Replies are byte for byte the recorded ones, for test names, blocked names, IPv6 and ANY queries.
    def test_replies_same_as_recorded(self):
        for want in golden("dns_replies"):
            reply, name, qtype = dns.build_reply(dns.encode_query(want["name"], want["qtype"], tid=0x2a2a), answer_for)
            self.assertEqual((reply.hex() if reply else None, name, qtype), (want["reply"], want["qname"], want["qtype_name"]), want)

    # Junk and responses are ignored.
    def test_junk(self):
        self.assertEqual(dns.build_reply(b"\x00\x01", answer_for), (None, None, None))
        reply, _, _ = dns.build_reply(dns.encode_query("x.com"), answer_for)
        self.assertEqual(dns.build_reply(reply, answer_for), (None, None, None))

    # parse_answer reads the address back, or None for an empty answer.
    def test_parse_answer(self):
        self.assertEqual(dns.parse_answer(dns.build_reply(dns.encode_query("ctest.cdn.nintendo.net"), answer_for)[0]), MAC_IP)
        self.assertEqual(dns.parse_answer(dns.build_reply(dns.encode_query("x.com"), answer_for)[0]), "0.0.0.0")
        self.assertIsNone(dns.parse_answer(dns.build_reply(dns.encode_query("x.com", 28), answer_for)[0]))

# The servers, running for real on this Mac.
class Servers(unittest.TestCase):
    # Start a DNS and a web server on high ports, recording what they report.
    def setUp(self):
        self.queries, self.requests, self.errors = [], [], []
        self.dns = dns.DnsServer("127.0.0.1", DNS_PORT, MAC_IP, policy.Policy(),
                                 lambda *a: self.queries.append(a), self.errors.append)
        self.web = web.TestWebServer("127.0.0.1", HTTP_PORT, policy.Policy(), lambda *a: self.requests.append(a))
        self.dns.start()
        self.web.start()

    # Stop both servers.
    def tearDown(self):
        self.dns.stop()
        self.web.stop()

    # DNS answers and reports each query (type by name, decision, answer).
    def test_dns_server(self):
        self.assertEqual(selftest.ask_dns("127.0.0.1", DNS_PORT, "ctest.cdn.nintendo.net"), MAC_IP)
        self.assertEqual(selftest.ask_dns("127.0.0.1", DNS_PORT, "api.github.com"), "0.0.0.0")
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.settimeout(2)
        c.sendto(dns.encode_query("ctest.cdn.nintendo.net", 28), ("127.0.0.1", DNS_PORT))
        self.assertIsNone(dns.parse_answer(c.recvfrom(512)[0]))
        c.close()
        time.sleep(0.1)
        self.assertEqual([(q[1], q[2], q[3].action, q[4]) for q in self.queries], [
            ("ctest.cdn.nintendo.net", "A", "test", MAC_IP), ("api.github.com", "A", "block", "0.0.0.0"),
            ("ctest.cdn.nintendo.net", "AAAA", "test", None)])
        self.assertEqual(self.errors, [])

    # The web test: 200 ok with Nintendo's header for test names, 403 otherwise; self-test requests not reported.
    def test_web_server(self):
        req = urllib.request.Request("http://127.0.0.1:%d/" % HTTP_PORT, headers={"Host": "ctest.cdn.nintendo.net", "User-Agent": "NX NIFM/00"})
        with urllib.request.urlopen(req, timeout=2) as r:
            self.assertEqual((r.status, r.headers.get("X-Organization"), r.read()), (200, "Nintendo", b"ok"))
        req = urllib.request.Request("http://127.0.0.1:%d/x" % HTTP_PORT, headers={"Host": "api.github.com", "User-Agent": "UA"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(e.exception.code, 403)
        e.exception.close()
        selftest.ask_web("127.0.0.1", HTTP_PORT, "ctest.cdn.nintendo.net")
        time.sleep(0.1)
        self.assertEqual([(r[1], r[2], r[3], r[4], r[5].action) for r in self.requests],
                         [("GET", "ctest.cdn.nintendo.net", "/", "NX NIFM/00", "test"), ("GET", "api.github.com", "/x", "UA", "block")])

    # --probe on the real servers: DNS gives the Mac's address, the web server still refuses (403) and reports the
    # request in full (headers, then the start of the body); other names report no details.
    def test_probe_servers(self):
        probing = policy.Policy(probe_hosts=["api.p01.lp1.ctest.srv.nintendo.net"])
        queries, requests = [], []
        d = dns.DnsServer("127.0.0.1", DNS_PORT + 1, MAC_IP, probing, lambda *a: queries.append(a), self.errors.append)
        w = web.TestWebServer("127.0.0.1", HTTP_PORT + 1, probing, lambda *a: requests.append(a))
        d.start()
        w.start()
        self.addCleanup(d.stop)
        self.addCleanup(w.stop)
        self.assertEqual(selftest.ask_dns("127.0.0.1", DNS_PORT + 1, "api.p01.lp1.ctest.srv.nintendo.net"), MAC_IP)
        req = urllib.request.Request("http://127.0.0.1:%d/v1/check" % (HTTP_PORT + 1), data=b'{"hello": 1}', method="POST",
                                     headers={"Host": "api.p01.lp1.ctest.srv.nintendo.net", "User-Agent": "libcurl (nnNifm)"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(e.exception.code, 403)
        e.exception.close()
        time.sleep(0.1)
        self.assertEqual((queries[0][3].action, queries[0][4]), ("probe", MAC_IP))
        method, host, path, details = requests[0][1], requests[0][2], requests[0][3], requests[0][6]
        self.assertEqual((method, host, path, requests[0][5].action), ("POST", "api.p01.lp1.ctest.srv.nintendo.net", "/v1/check", "probe"))
        self.assertIn("User-Agent: libcurl (nnNifm)", details)
        self.assertEqual(details[-1], "body (12 bytes): b'{\"hello\": 1}'")
        self.assertIsNone(self.requests[0][6] if self.requests else None)

    # The self-test: all six checks pass, in order.
    def test_selftest(self):
        checks = selftest.run_selftest("127.0.0.1", DNS_PORT, HTTP_PORT, MAC_IP)
        self.assertEqual([(c.kind, c.name, c.passed) for c in checks], [
            ("DNS", "ctest.cdn.nintendo.net", True), ("DNS", "ctest.p01.ctest.srv.nintendo.net", True), ("DNS", "example.com", True),
            ("HTTP", "ctest.cdn.nintendo.net", True), ("HTTP", "ctest.p01.ctest.srv.nintendo.net", True), ("HTTP", "example.com", True)])
        self.assertEqual(self.requests, [])

    # Each check is reported as soon as it's done (the run logs them as they happen).
    def test_selftest_reports_each_check(self):
        seen = []
        checks = selftest.run_selftest("127.0.0.1", DNS_PORT, HTTP_PORT, MAC_IP, on_check=seen.append)
        self.assertEqual(seen, checks)

    # The self-test notices a missing web server.
    def test_selftest_fails_without_web(self):
        self.web.stop()
        checks = selftest.run_selftest("127.0.0.1", DNS_PORT, HTTP_PORT, MAC_IP)
        self.assertFalse(all(c.passed for c in checks if c.kind == "HTTP"))
        self.assertTrue(all(c.passed for c in checks if c.kind == "DNS"))

if __name__ == "__main__":
    unittest.main()
