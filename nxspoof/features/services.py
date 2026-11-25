"""
nxspoof.features.services - watches each console for its services (today: its FTP server).

When a console gets an address (fact console.address), a watcher starts knocking on its FTP port:
every "check_every_seconds" while the server is down, every "recheck_when_up_seconds" while it's up
(0 = stop knocking once it's up). Changes are logged with today's wording, recorded in the console's
activity (for the summary and the dashboard), and announced as facts:

  service.up    (service, url)    e.g. the FTP server answered
  service.down  (service, url)    it stopped answering

A server that was up counts as down only after MISSES_BEFORE_DOWN failed knocks in a row, and never while a
program on this Mac is connected to it (in_use): a console's FTP app may take one connection at a time, so
while FileZilla uses it, the knock is refused although the server is fine ("busy", logged once).

FTP settings are the "ftp" section merged with the console's own "ftp" entry. When a console is added,
changed or removed in settings.json, or a global FTP setting changes (dashboard), a watcher whose port or
timing changed starts over.
More services (other ports apps open) plug in here later, each with its own name, port and URL scheme.
"""

import socket
import threading

from nxspoof.core.fmt import norm_mac
from nxspoof.core.text import compose, outcome

FTP = "FTP"
CONSOLE_CHANGES = ("console.added", "console.changed", "console.removed")
RESTART_KEYS = ("port", "check_every_seconds", "recheck_when_up_seconds")     # a change here means knocking differently
MISSES_BEFORE_DOWN = 3                     # failed knocks in a row (check_every_seconds apart) before an up server is down

# True if something accepts a TCP connection on ip:port within the timeout.
def probe_port(ip, port, timeout=1.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False

# Watches one service of one console.
class ServiceWatcher(object):
    # owner: the Services (bus, registry, activity, probe, in_use, server_up); settings: the console's merged ftp settings.
    def __init__(self, owner, mac, ip, settings):
        self.bus, self.registry, self.activity = owner.bus, owner.registry, owner.activity
        self.mac, self.ip, self.settings = mac, ip, dict(settings)
        self.on_up, self.probe = owner.server_up, owner.probe
        self.in_use = owner.in_use or (lambda ip, port: False)
        self.misses = 0                                      # failed knocks in a row while up
        self.busy = False                                    # "busy" already logged for this stretch
        self.port = int(self.settings.get("port") or 5000)
        self.every = max(1.0, float(self.settings.get("check_every_seconds") or 3))
        self.recheck = float(self.settings.get("recheck_when_up_seconds") or 0)
        self.url = "ftp://%s:%d" % (ip, self.port)
        self.up = False
        self._stop = threading.Event()
        self.thread = None

    # Announce the watch and start knocking in the background.
    def start(self):
        self.announce()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # Log that the watch started and remember the address.
    def announce(self):
        self.activity.service_watching(self.mac, FTP, self.url)
        self.bus.log("FTP", compose("watching %s for an FTP server on port %d (start it on the console)",
                     self.registry.label(mac=self.mac), self.port), mac=self.mac)

    # Knock until stopped (or until the server is up, when rechecking is off).
    def _loop(self):
        while not self._stop.is_set():
            wait = self.step(self.probe(self.ip, self.port))
            if wait is None or self._stop.wait(wait):
                return

    # A failed knock while up: seconds until the next knock if it doesn't count as down (busy, or not enough misses yet).
    def _not_down_yet(self):
        if self.in_use(self.ip, self.port):
            if not self.busy:
                self.busy = True
                self.bus.log("FTP", compose("%s: FTP server busy (a program on this Mac is connected to it, e.g. FileZilla) - still UP",
                             self.registry.label(mac=self.mac)), mac=self.mac)
            self.misses = 0
            return self.recheck if self.recheck > 0 else self.every
        self.misses += 1
        return self.every if self.misses < MISSES_BEFORE_DOWN else None

    # One knock's result -> log/record changes; returns seconds until the next knock, or None to stop.
    def step(self, now_up):
        name = self.registry.label(mac=self.mac)
        if now_up:
            self.misses, self.busy = 0, False
        elif self.up:
            wait = self._not_down_yet()
            if wait is not None:
                return wait
        if now_up and not self.up:
            self.up = True
            self.activity.service_event(self.mac, FTP, True, self.url)
            self.bus.log("FTP", compose("%s: FTP server %s -> %s", name, outcome("is UP", "up"), self.url),
                         casual=compose("📁 %s's file server is ready: %s", self.registry.name(self.mac), self.url), mac=self.mac,
                         fact="service.up", service=FTP, url=self.url)
            if self.on_up:
                self.on_up(self.mac, self.url)
            if self.recheck <= 0:
                return None                                  # don't poke the server again
        elif self.up and not now_up:
            self.up = False
            self.activity.service_event(self.mac, FTP, False, self.url)
            self.bus.log("FTP", compose("%s: FTP server %s (watching again)", name, outcome("went DOWN", "down")),
                         casual=compose("📁 %s's file server stopped", self.registry.name(self.mac)), mac=self.mac,
                         fact="service.down", service=FTP, url=self.url)
        return self.recheck if self.up else self.every

    # Stop knocking (returns at once; the thread ends at its next wake-up).
    def stop(self):
        self._stop.set()

# Starts one watcher per console when it gets an address.
class Services(object):
    # enabled: False for --no-ftp-watch; open_filezilla(url): called when a server comes up and
    # "open_filezilla" is on for that console; probe: replaceable for tests and the simulator;
    # in_use(ip, port): True while a program on this Mac is connected to a console's server;
    # background=False announces watchers without knocking (the caller drives watcher.step()).
    def __init__(self, bus, registry, activity, enabled=True, open_filezilla=None, probe=probe_port, background=True, in_use=None):
        self.bus, self.registry, self.activity = bus, registry, activity
        self.enabled, self.open_filezilla, self.probe, self.in_use = enabled, open_filezilla, probe, in_use
        self.background = background
        self.watchers = {}                                   # mac -> ServiceWatcher
        self.lock = threading.Lock()

    # Listen for consoles getting an address, and for console changes in settings.json.
    def attach(self):
        self.bus.add_listener(self.on_address, kinds=["console.address"])
        self.bus.add_listener(self.on_console_changed, kinds=list(CONSOLE_CHANGES))
        self.bus.add_listener(self.on_setting_changed, kinds=["settings.changed"])

    # A console has an address: start its watcher (again only if the address changed).
    def on_address(self, event):
        mac, ip = event.mac, event.data.get("ip")
        if not (self.enabled and mac and ip):
            return
        settings = self.registry.ftp_settings(mac)
        if not settings.get("watch", True):
            return
        with self.lock:
            old = self.watchers.get(mac)
            if old is not None and old.ip == ip:
                return
        self._restart(mac, ip, settings)

    # A console was added, changed or removed in settings.json: a watcher whose FTP settings changed starts over.
    def on_console_changed(self, event):
        self.follow(event.mac)

    # A global FTP setting changed (dashboard): every watcher whose FTP settings changed starts over.
    def on_setting_changed(self, event):
        if str(event.data.get("name", "")).startswith("ftp."):
            with self.lock:
                macs = list(self.watchers)
            for mac in macs:
                self.follow(mac)

    # Bring one console's watcher in line with its FTP settings as they are now (stop, restart, or leave it).
    def follow(self, mac):
        with self.lock:
            old = self.watchers.get(mac)
        if old is None:
            return
        settings = self.registry.ftp_settings(mac)
        if not settings.get("watch", True):
            with self.lock:
                self.watchers.pop(mac, None)
            old.stop()
        elif any(settings.get(k) != old.settings.get(k) for k in RESTART_KEYS):
            self._restart(mac, old.ip, settings)

    # Stop a console's watcher (if any) and start a new one with these settings.
    def _restart(self, mac, ip, settings):
        with self.lock:
            old = self.watchers.get(mac)
            if old is not None:
                old.stop()
            watcher = ServiceWatcher(self, mac, ip, settings)
            self.watchers[mac] = watcher
        if self.background:
            watcher.start()
        else:
            watcher.announce()

    # A server came up: open FileZilla if that console asks for it (a live setting, read now).
    def server_up(self, mac, url):
        if self.open_filezilla and self.registry.ftp_settings(mac).get("open_filezilla"):
            self.open_filezilla(url)

    # The FTP address known for a console, or None (for the dashboard's "Open in FileZilla").
    def url_for(self, mac):
        with self.activity.lock:
            r = self.activity.records.get(norm_mac(mac or ""))
            return r.service_urls.get(FTP) if r else None

    # Stop every watcher.
    def stop_all(self):
        with self.lock:
            watchers, self.watchers = list(self.watchers.values()), {}
        for w in watchers:
            w.stop()
