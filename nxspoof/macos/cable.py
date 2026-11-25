"""
nxspoof.macos.cable - the virtual cable that makes Thunderbolt Bridge look "plugged in", so Internet Sharing
will share from it (it refuses a bridge with nothing connected).

The cable is a pair of macOS's built-in virtual Ethernet interfaces (feth0 <-> feth1), with feth0 added to the
bridge. Nothing is connected to the real internet, and a restart of the Mac undoes it. Until 2026-11-15 this was
scripts/fake_link.sh; it lives here now, with everything else that talks to the Mac. Never logs: returns values
or raises.

  status(run)  -> Status: plugged in? the bridge's link? does macOS show the bridge's fixed address?
  up(run)      plug it in (needs root), then the status
  down(run)    take it out (needs root)
"""

import collections
import time

from nxspoof.macos import shell
from nxspoof.macos.netif import DEFAULT_BRIDGE
from nxspoof.macos.netservices import BRIDGE_ADDRESSES

IFCONFIG = "/sbin/ifconfig"
SCUTIL = "/usr/sbin/scutil"
PAIR = ("feth0", "feth1")
SETTLE_SECONDS = 3                                       # macOS needs a moment before the bridge shows as active

# What the cable looks like now. ok: plugged in AND macOS shows the bridge's address (what Internet Sharing needs).
class Status(collections.namedtuple("Status", "plugged link address")):
    __slots__ = ()

    # True when Internet Sharing can use the bridge.
    @property
    def ok(self):
        return self.plugged and self.address

    # One line saying what was found (PASS / FAIL and why), e.g. for setup's cable line and `nx_spoof.py link`.
    def text(self):
        if self.ok:
            return "PASS - macOS sees Thunderbolt Bridge as up with its address"
        if not self.plugged:
            return "FAIL - not plugged in yet (setup and Start plug it in)"
        return "FAIL - plugged in, but Thunderbolt Bridge has no fixed address yet (setup sets it; see docs/troubleshooting.html)"

# The bridge's ifconfig text (empty if it can't be read).
def _bridge_text(run):
    code, out = run([IFCONFIG, DEFAULT_BRIDGE])
    return out if code == 0 else ""

# True if one of macOS's network services currently has one of the bridge's fixed addresses (scutil's live state).
def _address_shown(run):
    keys = [line.split()[-1] for line in run([SCUTIL], "list State:/Network/Service/[^/]+/IPv4\n")[1].splitlines()
            if "State:/Network/Service/" in line]
    shown = [run([SCUTIL], "show %s\n" % key)[1] for key in keys]
    return any(address in text for text in shown for address in BRIDGE_ADDRESSES)

# The cable's state now.
def status(run=shell.run):
    text = _bridge_text(run)
    link = next((line.split("status:", 1)[1].strip() for line in text.splitlines() if "status:" in line), "?")
    return Status("member: %s" % PAIR[0] in text, link, _address_shown(run))

# Plug the cable in (root): create the pair if needed, connect it, add feth0 to the bridge; returns the status.
def up(run=shell.run):
    for name in PAIR:
        if run([IFCONFIG, name])[0] != 0:
            run([IFCONFIG, name, "create"])
    run([IFCONFIG, PAIR[0], "peer", PAIR[1]])
    for name in PAIR:
        run([IFCONFIG, name, "up"])
    if "member: %s" % PAIR[0] not in _bridge_text(run):
        code, out = run([IFCONFIG, DEFAULT_BRIDGE, "addm", PAIR[0]])
        if code != 0:
            raise OSError("couldn't add %s to %s: %s" % (PAIR[0], DEFAULT_BRIDGE, out.strip() or "error %d" % code))
    time.sleep(SETTLE_SECONDS)
    return status(run)

# Take the cable out (root); a restart does the same.
def down(run=shell.run):
    run([IFCONFIG, DEFAULT_BRIDGE, "deletem", PAIR[0]])
    for name in PAIR:
        run([IFCONFIG, name, "destroy"])
