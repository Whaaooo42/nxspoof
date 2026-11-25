"""
nxspoof.net.policy - what a console may reach. DNS, the web test, the dashboard and the logs all ask here.

Today (sealed mode): the two connection-test names are answered by the Mac, everything else is
blocked. The rule order planned in docs/how-its-built.html (What's next: blocklist, allow once, per-console
and global allow/deny) plugs into decide() later, without touching the DNS or web servers.
Tinkering: --probe NAME answers that name with the Mac's address too (action "probe"), so its requests reach the
Mac and can be seen (the web server still refuses them; anything but port 80 is dropped by the firewall).
"""

import collections

# The ONLY names answered by the Mac (the fake connection tests). Everything else is blocked.
TEST_HOSTS = collections.OrderedDict([
    ("ctest.cdn.nintendo.net", "Switch 1 connection test"),
    ("ctest.p01.ctest.srv.nintendo.net", "Switch 2 connection test"),
])
BLOCK_IP = "0.0.0.0"
MAC_ANSWERS = ("test", "probe")              # the actions whose names get the Mac's address in DNS

# The answer for one name: action is "test" (answered by the Mac), "probe" (sent to the Mac to see what comes, --probe)
# or "block"; rule says why; label for logs.
Decision = collections.namedtuple("Decision", "action rule label")

# Normalize a name as consoles send it ("Ctest.CDN.nintendo.net." -> "ctest.cdn.nintendo.net").
def clean(name):
    return (name or "").lower().rstrip(".")

# Decides names and web hosts for consoles.
class Policy(object):
    # test_hosts can be replaced (tests, future settings); probe_hosts: the names given with --probe.
    def __init__(self, test_hosts=None, probe_hosts=()):
        self.test_hosts = collections.OrderedDict(test_hosts or TEST_HOSTS)
        self.probe_hosts = [clean(n) for n in probe_hosts if clean(n) and clean(n) not in self.test_hosts]

    # The label of a test name ("Switch 2 connection test"), or None.
    def test_label(self, name):
        return self.test_hosts.get(clean(name))

    # Decide a name for a console (mac may be None when the console isn't known yet).
    def decide(self, name, mac=None):
        label = self.test_label(name)
        if label:
            return Decision("test", "connection test", label)
        if clean(name) in self.probe_hosts:
            return Decision("probe", "--probe", "probe: sent to the Mac to see what comes")
        return Decision("block", "default", None)

    # True if the name gets the Mac's address in DNS (a test name, or a probed one).
    def answers_with_mac(self, name):
        return self.decide(name).action in MAC_ANSWERS
