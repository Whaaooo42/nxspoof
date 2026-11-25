"""
nxspoof.macos.sharing - turn macOS Internet Sharing on/off without System Settings.

It writes the same setting System Settings writes (com.apple.nat through the SystemConfiguration
"SCPreferences" API, so macOS is notified): from a source device (the Thunderbolt Bridge with its
virtual cable) to the Wi-Fi device. The hotspot's name, password and channel live elsewhere in
macOS and are reused as they are.

Pure: describe, new_nat, parse_service_keys, parse_service_interface. Thin: read_nat, write_nat, read_value, write_value,
service_for_device, set_sharing, status. Never logs. The SCPreferences calls themselves run in a fresh process,
macos/scprefs.py: macOS doesn't allow these frameworks in a forked child (the background run and the keeper are forked;
on macOS 27.3 that crashed, Nov 25).

By hand:  python3 -m nxspoof.macos.sharing status      sudo python3 -m nxspoof.macos.sharing on|off
"""

import copy
import os
import plistlib
import re
import subprocess
import sys

from nxspoof.macos import netif, shell

PREFS_ID = "com.apple.nat.plist"
SCPREFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scprefs.py")     # run as its own process, never imported

# Internet Sharing couldn't be read or changed (the message says why).
class SharingError(RuntimeError):
    pass

# ##############################################################################################################################################################
# ##  PURE PARTS                                                                                                                                              ##
# ##############################################################################################################################################################

# One-line summary of the NAT settings: 'enabled=True  from=Thunderbolt Bridge (bridge0)  to=en0'.
def describe(nat):
    pi = nat.get("PrimaryInterface", {})
    return ("enabled=%s  from=%s (%s)  to=%s" % (
        bool(nat.get("Enabled")), pi.get("PrimaryUserReadable", "?"), pi.get("Device", "?"),
        ", ".join(nat.get("SharingDevices", [])) or "(nothing)"))

# The NAT settings to write: on/off, from the source service, to the Wi-Fi device (a new dict).
def new_nat(nat, enabled, service_id, service_name, source_device, wifi_device):
    nat = copy.deepcopy(nat or {})
    if service_id:
        nat["PrimaryService"] = service_id
        pi = nat.get("PrimaryInterface", {})
        pi.update({"Device": source_device, "PrimaryUserReadable": service_name,
                   "Enabled": pi.get("Enabled", 0), "HardwareKey": pi.get("HardwareKey", "")})
        nat["PrimaryInterface"] = pi
    nat["SharingDevices"] = [wifi_device]          # kept even when OFF, so System Settings shows it ticked
    nat["Enabled"] = 1 if enabled else 0
    return nat

# `scutil list .../Interface` text -> the service keys ("Setup:/Network/Service/<id>").
def parse_service_keys(text):
    return re.findall(r"= (Setup:/Network/Service/[^/\s]+)/Interface", text or "")

# `scutil show <service>/Interface` text -> the service's display name if it uses device, else None.
def parse_service_interface(text, device):
    if not re.search(r"DeviceName : %s\b" % re.escape(device), text or ""):
        return None
    name = re.search(r"UserDefinedName : (.+)", text)
    return name.group(1).strip() if name else device

# ##############################################################################################################################################################
# ##  READING AND WRITING                                                                                                                                     ##
# ##############################################################################################################################################################

# The current NAT (Internet Sharing) settings as a dict.
def read_nat():
    return read_value("NAT")

# Write the NAT settings, commit and apply (macOS reacts to the apply). Needs root.
def write_nat(nat):
    write_value("NAT", nat)

# One top-level value of a SystemConfiguration settings store, as Python ({} when it isn't there). Reading needs no root.
# Read by macos/scprefs.py in a fresh process (these frameworks aren't fork-safe, and a run is forked).
def read_value(key_name, prefs_id=PREFS_ID, run=shell.run):
    out = _fresh(["read", prefs_id, key_name], run=run)
    try:
        return plistlib.loads(out.encode())["value"]
    except Exception:
        raise SharingError("couldn't read %s %s: %s" % (prefs_id, key_name, out.strip()[:200] or "no answer"))

# Set one top-level value of a SystemConfiguration settings store, commit and apply (macOS is told). Needs root. Written by
# macos/scprefs.py in a fresh process, like read_value.
def write_value(key_name, obj, prefs_id=PREFS_ID, run=shell.run):
    _fresh(["write", prefs_id, key_name], plistlib.dumps({"value": obj}, fmt=plistlib.FMT_XML).decode(), run=run)

# macos/scprefs.py with these words, as a brand-new Python process (-I: nothing from the environment); `data` goes to its
# input -> its output (raises SharingError with its message when it fails).
def _fresh(words, data=None, run=shell.run):
    code, out = run([sys.executable, "-I", "-B", SCPREFS] + list(words), text=data)
    if code != 0:
        raise SharingError(out.strip() or "scprefs %s failed (code %d)" % (words[0], code))
    return out

# The network service using a device -> (service id, display name), or (None, None).
def service_for_device(device):
    scutil = lambda cmd: subprocess.run(["/usr/sbin/scutil"], input=cmd + "\n", capture_output=True, text=True).stdout
    for key in parse_service_keys(scutil("list Setup:/Network/Service/[^/]+/Interface")):
        name = parse_service_interface(scutil("show %s/Interface" % key), device)
        if name is not None:
            return key.rsplit("/", 1)[1], name
    return None, None

# Turn Internet Sharing on (source device -> Wi-Fi) or off; returns describe() of what was written.
def set_sharing(enabled, source_device=None, wifi_device=None):
    ports = netif.current_ports() if source_device is None or wifi_device is None else []
    source_device = source_device or netif.bridge_device(ports)
    wifi_device = wifi_device or netif.wifi_device(ports)
    service_id, service_name = service_for_device(source_device)
    if enabled and not service_id:
        raise SharingError("no network service uses %s (Thunderbolt Bridge missing? see docs/setup.html, step B)" % source_device)
    nat = new_nat(read_nat(), enabled, service_id, service_name, source_device, wifi_device)
    write_nat(nat)
    return describe(nat)

# What macOS has right now: {"settings": describe(...), "running": True if the hotspot is up}.
def status():
    return {"settings": describe(read_nat()), "running": netif.current_sharing_interface() is not None}

# ##############################################################################################################################################################
# ##  COMMAND LINE                                                                                                                                            ##
# ##############################################################################################################################################################

# python3 -m nxspoof.macos.sharing status | on | off
def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        s = status()
        print("settings: " + s["settings"])
        print("running : %s" % ("YES (the hotspot is up)" if s["running"] else "no"))
        return 0
    if cmd not in ("on", "off"):
        print("usage: python3 -m nxspoof.macos.sharing status   or   sudo python3 -m nxspoof.macos.sharing on|off")
        return 1
    if os.geteuid() != 0:
        print("Run with sudo: sudo python3 -m nxspoof.macos.sharing %s" % cmd)
        return 1
    try:
        print("now: " + set_sharing(cmd == "on"))
    except SharingError as e:
        print("couldn't: %s" % e)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
