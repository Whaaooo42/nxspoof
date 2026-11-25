"""
nxspoof.consoles.tracker - turns what the sensors saw into console activity, log lines and facts.

The DNS server, web server, Wi-Fi tracker and DHCP monitor only report raw observations (IPs,
names, MACs). Here they become: activity updates, the technical log line (today's exact wording),
and facts for listeners. A visit starts with the console's first sign (Wi-Fi, DHCP, DNS or web), with the
"joined the Wi-Fi" line: macOS writes its Wi-Fi line only every 5 seconds, so a console's first packets often
come before it. Facts, each emitted once per visit:

  console.connected  (visit, known)   first sign of a console in this visit (Wi-Fi, DHCP, DNS or web)
  console.online                      it passed its connection test (first time this visit)
  console.left       (seconds, name)  it left the Wi-Fi (console.joined, its line, has the name too)
  console.address    (ip, via, again) it has an IP address (via dhcp: the DHCP line; again: same address renewed this visit)
  console.unknown                     it isn't in settings.json
  console.blocked    (name)           a blocked attempt (kept in the records, where the run checklist reads it; the
                                      requests inbox will listen, later)

It also counts what each sensor saw (sensed: Wi-Fi joins and leaves, DHCP leases, lookups from addresses no lease names), for the
end-of-run check of features/sensors.py: on a macOS NXSPOOF was never tried on, a sensor that sees nothing is how a
change shows up.
"""

from nxspoof.core.fmt import fmt_duration
from nxspoof.core.text import compose, outcome

UNKNOWN_LINE = "unknown console %s - not in settings.json yet (add it on the dashboard, or paste the entry the summary prints when you stop)"

# Observations in, activity + log lines + facts out.
class ConsoleTracker(object):
    # bus: EventBus; registry: ConsoleRegistry; activity: ConsoleActivity; answer_ip: the Mac's hotspot address.
    def __init__(self, bus, registry, activity, answer_ip=None):
        self.bus, self.registry, self.activity = bus, registry, activity
        self.answer_ip = answer_ip
        self.sensed = {"wifi": 0, "dhcp": 0, "unmatched": {}}     # Wi-Fi joins and leaves, DHCP leases, {address: lookups} no lease names

    # First sign of a console in this visit: emit console.connected (and console.unknown if it's new to us).
    def _connected(self, mac):
        if self.activity.mark_connected(mac):
            r = self.activity.record(mac)
            self.bus.fact("console.connected", mac=mac, visit=r.visits, known=self.registry.known(mac))

    # Remember an IP; emit console.address the first time a console has it.
    def _address(self, ip, mac):
        if self.activity.saw_ip(ip, mac):
            self.bus.fact("console.address", mac=mac, ip=ip)

    # A sign of a console (ts: when; None = now): the first one of a visit opens it with the "joined" line.
    def _arrived(self, mac, ts=None):
        if not self.activity.arrive(mac, self.activity.clock() if ts is None else ts):
            return
        self.bus.log("WIFI", compose("%s joined the Wi-Fi", self.registry.label(mac=mac, with_mac=True)), mac=mac,
                     fact="console.joined", name=str(self.registry.name(mac)))
        self._connected(mac)
        if not self.registry.known(mac):
            self.bus.log("NEW", UNKNOWN_LINE % mac, mac=mac, fact="console.unknown")

    # The Wi-Fi tracker saw a console join (nothing new if its packets already opened the visit).
    def on_wifi_join(self, mac, ts):
        self.sensed["wifi"] += 1
        self._arrived(mac, ts)

    # The Wi-Fi tracker saw a console leave (reason: "disconnected" or "no longer seen for 16s"); the time on
    # the Wi-Fi counts from the visit's first sign (joined_at, macOS's first Wi-Fi line, can be a few seconds later).
    def on_wifi_leave(self, mac, reason, joined_at, ts):
        self.sensed["wifi"] += 1
        r = self.activity.record(mac)
        start = min(joined_at, r.joined[-1]) if self.activity.on_wifi(mac) else joined_at
        self.activity.left(mac, ts)
        self.bus.log("WIFI", compose("%s left the Wi-Fi (%s, was on for %s)",
                     self.registry.label(mac=mac, with_mac=True), reason, fmt_duration(ts - start)), mac=mac,
                     fact="console.left", seconds=ts - start, reason=reason, name=str(self.registry.name(mac)))

    # The DHCP server gave a console an address ("again" when it's the same address as before in this visit: a renewal).
    def on_dhcp(self, ip, mac):
        self.sensed["dhcp"] += 1
        self._arrived(mac)
        self.activity.saw_ip(ip, mac)
        again = self.activity.dhcp_again(mac, ip)
        self._connected(mac)
        self.bus.log("DHCP", compose("gave address %s to %s%s", ip, self.registry.label(mac=mac, with_mac=True), " again" if again else ""),
                     mac=mac, fact="console.address", ip=ip, via="dhcp", again=again)

    # What a DNS line says after "->": the address given and why, "no answer" (asked for IPv6, say), or BLOCKED.
    @staticmethod
    def _dns_result(decision, answer):
        if decision.action not in ("test", "probe"):
            return outcome("BLOCKED", "blocked")
        return "%s  (%s)" % (answer, decision.label) if answer else "(no IPv6 / no answer)"

    # The DNS server answered a query: qtype_name "A"/"AAAA"/..., decision from the policy, answer = address given or None.
    def on_dns(self, client_ip, name, qtype_name, decision, answer):
        mac = self.activity.resolve(client_ip)
        allowed, probed = decision.action == "test", decision.action == "probe"
        if mac:
            self._arrived(mac)
            self.activity.count_dns(mac, name, allowed or probed)
            self._address(client_ip, mac)
            self._connected(mac)
        elif client_ip != self.answer_ip:                                # a device no lease names (not the Mac's own self-test)
            self.sensed["unmatched"][client_ip] = self.sensed["unmatched"].get(client_ip, 0) + 1
        result = self._dns_result(decision, answer)
        blocked = {"fact": "console.blocked", "total": self.activity.blocked_total(mac)} if mac and not (allowed or probed) else {}
        self.bus.log("DNS", compose("%s asked %-5s %s -> %s",
                     self.registry.label(mac=mac, ip=client_ip), qtype_name, name, result),
            mac=mac, name=name if allowed or not mac else name.lower(), allowed=allowed, **blocked)

    # The web server answered a request (decision.action "test" = the fake connection test passed); details: a probed
    # request's headers and body (--probe), shown under its line.
    def on_http(self, client_ip, method, host, path, ua, decision, details=None):
        mac = self.activity.resolve(client_ip)
        passed = decision.action == "test"
        first_online = False
        if mac:
            self._arrived(mac)
            first_online = self.activity.web_request(mac, host, ua, decision.label if passed else None)
            self._connected(mac)
        result = compose("%s  (%s)", outcome("200 ok", "pass"), decision.label) if passed else outcome("BLOCKED (403)", "blocked")
        if decision.action == "probe":
            result = compose("%s  (probe: the request in full below)", result)
        fact = {"fact": "console.online"} if first_online else {}
        if mac and not passed:
            fact = {"fact": "console.blocked", "name": "http://" + host.lower(), "total": self.activity.blocked_total(mac)}
        self.bus.log("HTTP", compose("%s %s http://%s%s  (UA: %s) -> %s",
                     self.registry.label(mac=mac, ip=client_ip), method, host or "?", path, ua, result), mac=mac,
                     passed=passed, details=details, **fact)
