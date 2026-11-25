"""
nxspoof.consoles.activity - what each console did this run. The only place presence is stored.

Per console: Wi-Fi visits (joined/left times), the current visit's "connected" and "online" flags,
its IP addresses, tests passed, blocked attempts, User-Agents, service events. Transition methods
return True the first time something happens (per visit), so the tracker emits each fact once.
Time on the Wi-Fi is computed in one place (time_on_wifi) for every output.
"""

import collections
import threading
import time

from nxspoof.core.fmt import norm_mac

# Everything one console did this run.
class Record(object):
    # A fresh record for one console.
    def __init__(self, mac):
        self.mac = mac
        self.ip = None
        self.joined = []                           # Wi-Fi visit starts (timestamps)
        self.left = []                             # Wi-Fi visit ends (timestamps)
        self.connected = False                     # this visit: seen at all (Wi-Fi, DHCP, DNS or web)
        self.online = False                        # this visit: passed its connection test
        self.dhcp_ip = None                        # this visit: the address DHCP gave it last
        self.visits = 0                            # how many visits started (1 = first)
        self.dns = 0
        self.tests = collections.OrderedDict()     # test label -> times passed
        self.blocked = collections.Counter()       # name (or http://host) -> times
        self.uas = []
        self.services = collections.OrderedDict()  # service name -> [(timestamp, "UP"/"DOWN")]
        self.service_urls = {}

    # True if this console did anything worth listing in a summary.
    def active(self):
        return bool(self.joined or self.dns or self.tests or self.ip)

# Per-run activity of every console.
class ConsoleActivity(object):
    # leases_lookup(ip) -> mac (e.g. from the DHCP lease file) is used for IPs not seen yet.
    def __init__(self, leases_lookup=None, clock=time.time):
        self.records = collections.OrderedDict()
        self.ip_mac = {}
        self.leases_lookup = leases_lookup
        self.clock = clock
        self.lock = threading.RLock()

    # The record for a console (created on first use).
    def record(self, mac):
        mac = norm_mac(mac)
        with self.lock:
            if mac not in self.records:
                self.records[mac] = Record(mac)
            return self.records[mac]

    # The console using an IP: what we've seen, else the lease lookup; None if unknown.
    def resolve(self, ip):
        with self.lock:
            if ip in self.ip_mac:
                return self.ip_mac[ip]
        mac = self.leases_lookup(ip) if self.leases_lookup else None
        if mac:
            with self.lock:
                self.ip_mac.setdefault(ip, norm_mac(mac))
            return norm_mac(mac)
        return None

    # Remember a console's IP; True if it's a new IP for that console.
    def saw_ip(self, ip, mac):
        with self.lock:
            r = self.record(mac)
            self.ip_mac[ip] = r.mac
            new = r.ip != ip
            r.ip = ip
            return new

    # DHCP gave the console ip: True if it already gave it that same address this visit (a renewal: the line says "again").
    def dhcp_again(self, mac, ip):
        with self.lock:
            r = self.record(mac)
            again = r.dhcp_ip == ip
            r.dhcp_ip = ip
            return again

    # The console was seen (any signal); True if that starts its "connected" state for this visit.
    def mark_connected(self, mac):
        with self.lock:
            r = self.record(mac)
            if r.connected:
                return False
            r.connected = True
            r.visits += 1
            return True

    # The console joined the Wi-Fi at ts.
    def joined(self, mac, ts):
        with self.lock:
            self.record(mac).joined.append(ts)

    # True while the console is on the Wi-Fi (its last visit has started and not ended).
    def on_wifi(self, mac):
        with self.lock:
            r = self.record(mac)
            return len(r.joined) > len(r.left)

    # The console showed up at ts: True if that opens a Wi-Fi visit (it wasn't on the Wi-Fi), in one step.
    def arrive(self, mac, ts):
        with self.lock:
            if self.on_wifi(mac):
                return False
            self.record(mac).joined.append(ts)
            return True

    # The console left the Wi-Fi at ts; the visit ends (connected/online/DHCP address reset).
    def left(self, mac, ts):
        with self.lock:
            r = self.record(mac)
            r.left.append(ts)
            r.connected = False
            r.online = False
            r.dhcp_ip = None

    # Count a DNS lookup (and a blocked one).
    def count_dns(self, mac, name, allowed):
        with self.lock:
            r = self.record(mac)
            r.dns += 1
            if not allowed:
                r.blocked[name.lower()] += 1

    # A web request: test passed (label) or blocked (host); returns True if it's the first online this visit.
    def web_request(self, mac, host, ua, test_label):
        with self.lock:
            r = self.record(mac)
            if ua not in r.uas:
                r.uas.append(ua)
            if not test_label:
                r.blocked["http://" + host.lower()] += 1
                return False
            r.tests[test_label] = r.tests.get(test_label, 0) + 1
            first = not r.online
            r.online = True
            return first

    # A service watcher started on a console: remember its address (the summary says "never seen on ...").
    def service_watching(self, mac, service, url):
        with self.lock:
            self.record(mac).service_urls[service] = url

    # A service went up or down (e.g. FTP); returns True if that's a change.
    def service_event(self, mac, service, up, url=None, ts=None):
        with self.lock:
            r = self.record(mac)
            events = r.services.setdefault(service, [])
            state = "UP" if up else "DOWN"
            if (events and events[-1][1] == state) or (not events and not up):
                return False
            events.append((ts if ts is not None else self.clock(), state))
            if url:
                r.service_urls[service] = url
            return True

    # Total time on the Wi-Fi so far (open visits count until now): the one calculation for every output.
    def time_on_wifi(self, mac, now=None):
        now = self.clock() if now is None else now
        with self.lock:
            r = self.records.get(norm_mac(mac))
            if not r:
                return 0.0
            return sum(((r.left[i] if i < len(r.left) else now) - j) for i, j in enumerate(r.joined))

    # Blocked attempts so far for one console.
    def blocked_total(self, mac):
        with self.lock:
            r = self.records.get(norm_mac(mac))
            return sum(r.blocked.values()) if r else 0

    # The records of consoles that did something, in first-seen order.
    def active_records(self):
        with self.lock:
            return [r for r in self.records.values() if r.active()]
