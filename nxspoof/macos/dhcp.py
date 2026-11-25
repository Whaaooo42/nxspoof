"""
nxspoof.macos.dhcp - notices when Internet Sharing's DHCP server hands out an address.

DhcpMonitor watches the lease file's modification time and calls on_lease(ip, mac) for every new
lease (a console getting an address, or renewing it). Leases already in the file at start are
not announced. Never logs.
"""

import threading
import time

from nxspoof.macos import leases

# The leases not seen before (pure); updates seen with them. A lease is new if its (ip, mac, lease) is new.
def new_leases(seen, current):
    out = []
    for lease in current:
        key = (lease.ip, lease.mac, lease.lease)
        if key not in seen:
            seen.add(key)
            out.append(lease)
    return out

# Polls the lease file and announces new leases through on_lease(ip, mac).
class DhcpMonitor(object):
    # path and interval can be replaced (the simulator uses a fake lease file).
    def __init__(self, on_lease, path=leases.LEASES_FILE, interval=2.0):
        self.on_lease = on_lease
        self.path = path
        self.interval = interval
        self.seen = set()
        self.running = False
        self._mtime = None

    # Remember what's already in the file, then start polling.
    def start(self):
        self._mtime = leases.lease_mtime(self.path)
        new_leases(self.seen, leases.read_leases(self.path))
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # Check the file once (also usable directly in tests): announce new leases if it changed.
    def check(self):
        mtime = leases.lease_mtime(self.path)
        if mtime is None or mtime == self._mtime:
            return []
        self._mtime = mtime
        found = new_leases(self.seen, leases.read_leases(self.path))
        for lease in found:
            self.on_lease(lease.ip, lease.mac)
        return found

    # Poll until stopped.
    def _loop(self):
        while self.running:
            time.sleep(self.interval)
            if self.running:
                self.check()

    # Stop polling.
    def stop(self):
        self.running = False
