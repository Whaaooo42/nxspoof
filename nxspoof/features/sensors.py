"""
nxspoof.features.sensors - the end-of-run check of NXSPOOF's sensors: did each one see what the others say happened?

NXSPOOF learns who is on the hotspot from three sources: macOS's Wi-Fi log lines (joins and leaves, macos/wifi.py), its
DHCP lease file (addresses, macos/leases.py) and the DNS lookups themselves. On a macOS NXSPOOF was never tried on, a
reworded log line or a moved lease file doesn't break anything loudly: that source just goes quiet. So at the end of a
real run (the hotspot, not a dry run), one line each when a source stayed silent while the others saw consoles:

  Wi-Fi watcher   consoles got addresses, but not one join or leave was seen: join and leave times are missing
  lease file      devices asked for names, but no lease named them: they show as addresses, not consoles

Both are warnings with "please report it": the run itself worked.
"""

from nxspoof.core.fmt import plural

# The sensors' end-of-run lines on the bus. sensed: the tracker's counts; wifi_watched: the Wi-Fi watcher was running;
# macos: macOS's version, for the line; leases_file: where the lease file was looked for.
def check(bus, sensed, wifi_watched, macos="?", leases_file="/var/db/dhcpd_leases"):
    if wifi_watched and sensed["dhcp"] and not sensed["wifi"]:
        bus.log("WIFI", "the Wi-Fi watcher saw no console join or leave, though %s handed out: macOS %s may word its Wi-Fi log lines "
                "differently - join and leave times are missing this run (please report it)" % (plural(sensed["dhcp"], "address", "addresses"), macos),
                casual="⚠️ I couldn't see consoles joining the Wi-Fi this time (everything else worked). Please report it: 🐞 Report a problem.",
                level="warn", fact="sensors.quiet", sensor="wifi", dhcp=sensed["dhcp"])
    unmatched = sensed["unmatched"]
    if unmatched and not sensed["dhcp"]:
        bus.log("DHCP", "%s came from addresses no DHCP lease names (%s): macOS %s may keep its lease file elsewhere than %s - "
                "consoles show as addresses this run (please report it)" % (
                    plural(sum(unmatched.values()), "lookup"), ", ".join(sorted(unmatched)), macos, leases_file),
                casual="⚠️ I couldn't tell which console was which this time (they show as addresses). Please report it: 🐞 Report a problem.",
                level="warn", fact="sensors.quiet", sensor="leases", addresses=sorted(unmatched))
