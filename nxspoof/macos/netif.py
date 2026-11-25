"""
nxspoof.macos.netif - network interfaces: the hotspot's interface, the Wi-Fi device, the bridge,
and (for allowlist mode) wired uplinks; and whether this Mac has a TCP connection open to a console's
port (`netstat`: FileZilla using the console's FTP server, which may take only one connection at a time).

Everything is found from the output of `ifconfig` and `networksetup -listallhardwareports`, by
device and port type, so nothing about one particular Mac is hard-coded. The parse_* functions
are pure (tested with real samples); the *_text functions run the commands. Never logs.
"""

import collections
import re
import subprocess

from nxspoof.macos import shell

DEFAULT_WIFI = "en0"
DEFAULT_BRIDGE = "bridge0"

# ##############################################################################################################################################################
# ##  PARSING (PURE)                                                                                                                                          ##
# ##############################################################################################################################################################

# ifconfig text -> {interface: {"flags": ..., "inets": [...], "status": ..., "members": [...]}} in order.
def parse_ifconfig(text):
    out = collections.OrderedDict()
    current = None
    for line in (text or "").splitlines():
        m = re.match(r"^(\S+?): flags=\S*<([^>]*)>", line)
        if m:
            current = out.setdefault(m.group(1), {"flags": m.group(2).split(","), "inets": [], "status": None, "members": []})
            continue
        if current is None:
            continue
        m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m:
            current["inets"].append(m.group(1))
        m = re.match(r"^\s+status: (\S+)", line)
        if m:
            current["status"] = m.group(1)
        m = re.match(r"^\s+member: (\S+)", line)
        if m:
            current["members"].append(m.group(1))
    return out

# The interfaces that matter to a hotspot (Wi-Fi and Ethernet en*, bridges, the virtual cable's feth*), one line each, for a
# log line's details when the hotspot doesn't come up: "bridge100: UP, active, 192.168.2.1, members en0 feth1".
def summarize_interfaces(ifconfig_text):
    lines = []
    for name, info in parse_ifconfig(ifconfig_text).items():
        if not re.match(r"^(en|bridge|feth)\d+$", name):
            continue
        parts = ["UP" if "UP" in info["flags"] else "down"] + ([info["status"]] if info["status"] else []) + info["inets"]
        if info["members"]:
            parts.append("members " + " ".join(info["members"]))
        lines.append("%s: %s" % (name, ", ".join(parts)))
    return lines

# The Internet Sharing network: the first bridge1xx interface with an address -> (interface, ip), or None.
def find_sharing_interface(ifconfig_text):
    for name, info in parse_ifconfig(ifconfig_text).items():
        if re.match(r"^bridge1\d\d$", name) and info["inets"]:
            return name, info["inets"][0]
    return None

# networksetup -listallhardwareports text -> [{"port": "Wi-Fi", "device": "en0", "address": "..."}].
def parse_hardware_ports(text):
    ports, current = [], None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            current = {"port": line.split(":", 1)[1].strip(), "device": None, "address": None}
            ports.append(current)
        elif current is not None and line.startswith("Device:"):
            current["device"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("Ethernet Address:"):
            current["address"] = line.split(":", 1)[1].strip()
    return ports

# The Wi-Fi device (e.g. en0) from the hardware ports, or the usual default.
def wifi_device(ports):
    for p in ports:
        if p["port"] == "Wi-Fi" and p["device"]:
            return p["device"]
    return DEFAULT_WIFI

# The Thunderbolt Bridge device (e.g. bridge0) from the hardware ports, or the usual default.
# By name first, then by device (the only bridgeN hardware port), since port names follow the macOS language.
def bridge_device(ports):
    for p in ports:
        if p["port"] == "Thunderbolt Bridge" and p["device"]:
            return p["device"]
    for p in ports:
        if re.match(r"^bridge\d+$", p["device"] or ""):
            return p["device"]
    return DEFAULT_BRIDGE

# Wired uplinks that are connected and have an address (Ethernet/USB LAN ports, not Wi-Fi, bridges or iPhone).
def active_uplinks(ports, ifconfig_text):
    interfaces = parse_ifconfig(ifconfig_text)
    out = []
    for p in ports:
        name = p["port"].lower()
        wired = ("ethernet" in name or "lan" in name) and "thunderbolt" not in name
        info = interfaces.get(p["device"] or "")
        if wired and info and info["status"] == "active" and info["inets"]:
            out.append({"port": p["port"], "device": p["device"], "ip": info["inets"][0]})
    return out

# `netstat -an -p tcp` text -> True if a connection to ip:port is ESTABLISHED (macOS writes it "ip.port").
def has_connection_to(netstat_text, ip, port):
    target = "%s.%d" % (ip, int(port))
    for line in (netstat_text or "").splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0].startswith("tcp") and parts[4] == target and parts[5] == "ESTABLISHED":
            return True
    return False

# ##############################################################################################################################################################
# ##  RUNNING THE COMMANDS                                                                                                                                    ##
# ##############################################################################################################################################################

# The current `ifconfig` output ("" if it can't run).
def ifconfig_text():
    try:
        return subprocess.run(["/sbin/ifconfig"], capture_output=True, text=True).stdout
    except OSError:
        return ""

# The current `networksetup -listallhardwareports` output ("" if it can't run).
def hardware_ports_text():
    try:
        return subprocess.run(["/usr/sbin/networksetup", "-listallhardwareports"], capture_output=True, text=True).stdout
    except OSError:
        return ""

# The Internet Sharing network right now -> (interface, ip), or None.
def current_sharing_interface():
    return find_sharing_interface(ifconfig_text())

# The hotspot-related interfaces right now, one line each (summarize_interfaces).
def current_interfaces():
    return summarize_interfaces(ifconfig_text())

# The hardware ports right now.
def current_ports():
    return parse_hardware_ports(hardware_ports_text())

# True if a program on this Mac (e.g. FileZilla) has a TCP connection open to ip:port right now.
def connected_to(ip, port):
    code, out = shell.run(["/usr/sbin/netstat", "-an", "-p", "tcp"])
    return code == 0 and has_connection_to(out, ip, port)
