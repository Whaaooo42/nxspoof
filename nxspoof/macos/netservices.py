"""
nxspoof.macos.netservices - macOS network services through `networksetup`: find the service of a
device (Thunderbolt Bridge -> bridge0), read its address, create it, give it a fixed address, or put
it back on automatic.

Services are matched by DEVICE (bridge0), never by their display name, which depends on the macOS
language. Pure: parse_service_order, parse_info, service_for_device. Thin: the rest (reading works
without admin rights; changes need root). Never logs.
"""

import re

from nxspoof.macos import shell

NETWORKSETUP = "/usr/sbin/networksetup"
BRIDGE_SERVICE_NAME = "Thunderbolt Bridge"
DEFAULT_ADDRESS = ("172.16.0.1", "255.255.255.0")       # the bridge's fixed address unless "bridge.address" says otherwise
BRIDGE_ADDRESSES = ("172.16.0.1", "10.254.0.1", "192.168.254.1")     # the private ranges offered for it (settings: bridge.address)
BRIDGE_MASK = "255.255.255.0"

# ##############################################################################################################################################################
# ##  PARSING (PURE)                                                                                                                                          ##
# ##############################################################################################################################################################

# `networksetup -listnetworkserviceorder` -> [{"name", "port", "device", "enabled"}], in order.
def parse_service_order(text):
    out, current = [], None
    for line in (text or "").splitlines():
        m = re.match(r"^\((\d+|\*)\)\s*(.+)$", line.strip())
        if m:
            current = {"name": m.group(2).strip(), "port": None, "device": None, "enabled": m.group(1) != "*"}
            out.append(current)
            continue
        m = re.match(r"^\(Hardware Port:\s*(.*?),\s*Device:\s*(.*?)\)$", line.strip())
        if m and current is not None:
            current["port"], current["device"] = m.group(1), m.group(2) or None
    return out

# `networksetup -getinfo <service>` -> {"mode": "manual"/"dhcp"/"bootp"/"off"/"other", "ip", "mask"}.
def parse_info(text):
    first = (text or "").strip().splitlines()[0].lower() if (text or "").strip() else ""
    if first.startswith("manual"):
        mode = "manual"
    elif first.startswith("dhcp"):
        mode = "dhcp"
    elif first.startswith("bootp"):
        mode = "bootp"
    elif "off" in first:
        mode = "off"
    else:
        mode = "other"
    ip = re.search(r"^IP address:\s*(\S+)", text or "", re.M)
    mask = re.search(r"^Subnet mask:\s*(\S+)", text or "", re.M)
    return {"mode": mode, "ip": ip.group(1) if ip else None, "mask": mask.group(1) if mask else None}

# The service using a device (e.g. "bridge0"), as its entry from parse_service_order, or None.
def service_for_device(services, device):
    for s in services:
        if s["device"] == device:
            return s
    return None

# ##############################################################################################################################################################
# ##  RUNNING networksetup                                                                                                                                    ##
# ##############################################################################################################################################################

# Run networksetup with arguments -> (exit code, output). run(cmd) can replace subprocess (tests).
def networksetup(*args, run=None):
    return (run or shell.run)([NETWORKSETUP] + list(args))

# The network services, in order.
def list_services(run=None):
    return parse_service_order(networksetup("-listnetworkserviceorder", run=run)[1])

# A service's address settings (see parse_info), or None if macOS doesn't know the service.
def get_info(name, run=None):
    code, out = networksetup("-getinfo", name, run=run)
    return parse_info(out) if code == 0 else None

# Create a service for a device (needs root); raises RuntimeError with networksetup's message.
def create_service(name, device, run=None):
    _check(networksetup("-createnetworkservice", name, device, run=run))

# Give a service a fixed address (needs root).
def set_manual(name, ip, mask, run=None):
    _check(networksetup("-setmanual", name, ip, mask, run=run))

# Turn a service on or off (needs root).
def set_enabled(name, on, run=None):
    _check(networksetup("-setnetworkserviceenabled", name, "on" if on else "off", run=run))

# Put a service back on automatic (DHCP) (needs root).
def set_dhcp(name, run=None):
    _check(networksetup("-setdhcp", name, run=run))

# Raise RuntimeError if a networksetup call failed (its own words, or the exit code).
def _check(result):
    code, out = result
    if code != 0 or "Error" in (out or ""):
        raise RuntimeError((out or "").strip() or "networksetup failed (code %d)" % code)
