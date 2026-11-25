"""
nxspoof.macos.leases - the one reader of macOS's DHCP lease file (/var/db/dhcpd_leases).

Internet Sharing's DHCP server writes one { } block per address it handed out. This is the only
place that parses it (the old code had two copies). Never logs.
"""

import collections
import os
import re

from nxspoof.core.fmt import norm_mac

LEASES_FILE = "/var/db/dhcpd_leases"
Lease = collections.namedtuple("Lease", "ip mac lease")

# Parse the lease file's text into Lease(ip, mac, lease) entries (MAC normalized; incomplete blocks skipped).
def parse_leases(text):
    out = []
    for block in re.findall(r"\{[^}]*\}", text or ""):
        ip = re.search(r"ip_address=(\S+)", block)
        hw = re.search(r"hw_address=\d+,(\S+)", block)
        lease = re.search(r"lease=(\S+)", block)
        if ip and hw:
            out.append(Lease(ip.group(1), norm_mac(hw.group(1)), lease.group(1) if lease else None))
    return out

# Read and parse the lease file (empty list if it's missing or unreadable).
def read_leases(path=LEASES_FILE):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return parse_leases(f.read())
    except OSError:
        return []

# The lease file's modification time, or None if it's missing.
def lease_mtime(path=LEASES_FILE):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None
