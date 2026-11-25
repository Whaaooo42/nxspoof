"""
nxspoof.macos.wifi - who is on the Wi-Fi right now, from macOS's kernel log.

While a console is connected, the kernel logs 'SoftAP Client [mac]' every 5 s. When it leaves, it
logs 'Deleting <MAC> from cache' / 'Peer Delete cache indication for <MAC>' and the 5-second lines
stop. WifiTracker turns those lines into joined/left callbacks (pure: fed lines and times).
WifiMonitor runs `log stream` and feeds the tracker. Never logs.
"""

import re
import subprocess
import threading
import time

from nxspoof.core.fmt import norm_mac

LOG_STREAM_CMD = ["/usr/bin/log", "stream", "--style", "compact", "--predicate",
                  'process == "kernel" AND (eventMessage CONTAINS "SoftAP Client [" OR '
                  'eventMessage CONTAINS "deletePeerFromFWCache" OR eventMessage CONTAINS "Peer Delete cache indication")']

# Presence on the Wi-Fi from kernel lines: on_join(mac, now) and on_leave(mac, reason, joined_at, now).
class WifiTracker(object):
    CONFIRM = 6.5                                  # a delete counts once no 5-second line came for this long
    LOST = 16.0                                    # no delete at all: this much silence also counts as left
    SOFTAP_RX = re.compile(r"SoftAP Client \[([0-9a-fA-F:]{17})\]")
    DELETE_RX = re.compile(r"(?:Deleting|Delete cache indication for) ([0-9A-Fa-f:]{17})")

    # Start with nobody present.
    def __init__(self, on_join, on_leave):
        self.on_join, self.on_leave = on_join, on_leave
        self.present = {}                          # mac -> joined at
        self.last_seen = {}                        # mac -> last 5-second line
        self.pending = {}                          # mac -> delete seen at
        self.lock = threading.Lock()

    # Process one kernel log line (a 5-second status line or a peer delete).
    def feed(self, line, now):
        m = self.SOFTAP_RX.search(line)
        if m:
            mac = norm_mac(m.group(1))
            with self.lock:
                self.last_seen[mac] = now
                self.pending.pop(mac, None)
                new = mac not in self.present
                if new:
                    self.present[mac] = now
            if new:
                self.on_join(mac, now)
            return
        m = self.DELETE_RX.search(line)
        if m:
            mac = norm_mac(m.group(1))
            with self.lock:
                if mac in self.present:
                    self.pending.setdefault(mac, now)

    # Called every second: consoles that went quiet are declared as having left.
    def tick(self, now):
        gone = []
        with self.lock:
            for mac in list(self.present):
                quiet = now - self.last_seen.get(mac, now)
                if mac in self.pending and quiet >= self.CONFIRM:
                    reason = "disconnected"
                elif quiet >= self.LOST:
                    reason = "no longer seen for %ds" % int(quiet)
                else:
                    continue
                gone.append((mac, reason, self.present.pop(mac)))
                self.pending.pop(mac, None)
                self.last_seen.pop(mac, None)
        for mac, reason, joined in gone:
            self.on_leave(mac, reason, joined, now)

    # MACs present right now.
    def present_macs(self):
        with self.lock:
            return sorted(self.present)

# Runs the kernel log stream (or any command printing such lines) and feeds a WifiTracker.
class WifiMonitor(object):
    # cmd and clock can be replaced (the simulator feeds lines from a file instead of `log stream`).
    def __init__(self, tracker, cmd=None, clock=time.time):
        self.tracker = tracker
        self.cmd = list(cmd or LOG_STREAM_CMD)
        self.clock = clock
        self.process = None
        self.running = False

    # Start the stream and the 1-second ticker; raises OSError if the command can't start.
    def start(self):
        self.process = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.running = True
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._tick, daemon=True).start()

    # Feed every line of the stream to the tracker.
    def _read(self):
        for line in self.process.stdout:
            if not self.running:
                return
            self.tracker.feed(line, self.clock())

    # Tick the tracker once a second while running.
    def _tick(self):
        while self.running:
            time.sleep(1)
            self.tracker.tick(self.clock())

    # Stop ticking, end the stream process and close its pipe.
    def stop(self):
        self.running = False
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.process.stdout:
            try:
                self.process.stdout.close()
            except (OSError, ValueError):
                pass
