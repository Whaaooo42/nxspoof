"""
nxspoof.net.selftest - the self-check run before consoles connect: both test names must get the
Mac's address and the "ok" reply, and a blocked name must get 0.0.0.0 and a 403.

Returns results; the caller writes the CHECK lines. Never logs.

The web check talks straight to the Mac's own address with http.client: no proxy lookup. urllib would first ask macOS for
proxy settings (SystemConfiguration, through _scproxy), which crashes a forked process on newer macOS (27.3, Nov 25:
the run is forked), and a proxy has no business in a check of this Mac's own answers anyway.
"""

import collections
import http.client
import socket

from nxspoof.core.text import compose, outcome
from nxspoof.net import dns, policy as policy_module
from nxspoof.net.web import SELFTEST_HEADER

BLOCKED_SAMPLE = "example.com"
Check = collections.namedtuple("Check", "kind name passed got")

# One check as its CHECK line's text, PASS / FAIL marked: "DNS  PASS ctest.cdn.nintendo.net -> 192.168.2.1".
def check_line(ch):
    return compose("%-4s %s %s -> %s", ch.kind, outcome("PASS", "pass") if ch.passed else outcome("FAIL", "fail"), ch.name, ch.got)

# Ask the DNS server for a name; returns the answered address or an "error: ..." text.
def ask_dns(ip, port, name, timeout=3):
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(timeout)
    try:
        c.sendto(dns.encode_query(name), (ip, port))
        reply, _ = c.recvfrom(512)
        return dns.parse_answer(reply) or "no answer"
    except OSError as e:
        return "error: %s" % e
    finally:
        c.close()

# Ask the web server as host, directly (no proxy); returns (status or "error: ...", X-Organization header, body). A refusal
# (403) comes back as its status, with no body.
def ask_web(ip, port, host, timeout=3):
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        conn.request("GET", "/", headers={"Host": host, SELFTEST_HEADER: "1"})
        r = conn.getresponse()
        body = r.read()
        if r.status >= 400:
            return r.status, None, b""
        return r.status, r.getheader("X-Organization"), body
    except (OSError, http.client.HTTPException) as e:
        return "error: %s" % e, None, b""
    finally:
        conn.close()

# Run all six checks; returns [Check(kind "DNS"/"HTTP", name, passed, got)] in the old order.
# on_check(check) is called as each one finishes (the run logs them as they happen).
def run_selftest(ip, dns_port, http_port, answer_ip, test_hosts=None, on_check=None):
    names = list(test_hosts or policy_module.TEST_HOSTS)
    checks = []
    for name, want in [(n, answer_ip) for n in names] + [(BLOCKED_SAMPLE, policy_module.BLOCK_IP)]:
        got = ask_dns(ip, dns_port, name)
        checks.append(Check("DNS", name, got == want, got))
        if on_check:
            on_check(checks[-1])
    for host, want in [(n, 200) for n in names] + [(BLOCKED_SAMPLE, 403)]:
        status, org, body = ask_web(ip, http_port, host)
        passed = (status == 200 and org == "Nintendo" and body == b"ok") if want == 200 else status == want
        checks.append(Check("HTTP", host, passed, "200 ok" if status == 200 else status))
        if on_check:
            on_check(checks[-1])
    return checks
