"""
nxspoof.macos.sharing - turn macOS Internet Sharing on/off without System Settings.

It writes the same setting System Settings writes (com.apple.nat through the SystemConfiguration
"SCPreferences" API, so macOS is notified): from a source device (the Thunderbolt Bridge with its
virtual cable) to the Wi-Fi device. The hotspot's name, password and channel live elsewhere in
macOS and are reused as they are.

Pure: describe, new_nat, parse_service_keys, parse_service_interface. Thin: read_nat, write_nat,
service_for_device, set_sharing, status (the frameworks are loaded on first use). Never logs.

By hand:  python3 -m nxspoof.macos.sharing status      sudo python3 -m nxspoof.macos.sharing on|off
"""

import copy
import ctypes
import os
import plistlib
import re
import subprocess
import sys
from ctypes import c_bool, c_char_p, c_int, c_long, c_uint32, c_void_p

from nxspoof.macos import netif

PREFS_ID = "com.apple.nat.plist"
_UTF8 = 0x08000100
_XML_FORMAT = 100
_LIBS = {}

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
# ##  COREFOUNDATION + SYSTEMCONFIGURATION (LOADED ON FIRST USE)                                                                                              ##
# ##############################################################################################################################################################

# The two frameworks with their function signatures, loaded once.
def _libs():
    if _LIBS:
        return _LIBS["cf"], _LIBS["sc"]
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sc = ctypes.CDLL("/System/Library/Frameworks/SystemConfiguration.framework/SystemConfiguration")
    cf.CFStringCreateWithCString.restype, cf.CFStringCreateWithCString.argtypes = c_void_p, [c_void_p, c_char_p, c_uint32]
    cf.CFDataCreate.restype, cf.CFDataCreate.argtypes = c_void_p, [c_void_p, c_char_p, c_long]
    cf.CFDataGetLength.restype, cf.CFDataGetLength.argtypes = c_long, [c_void_p]
    cf.CFDataGetBytePtr.restype, cf.CFDataGetBytePtr.argtypes = c_void_p, [c_void_p]
    cf.CFPropertyListCreateData.restype = c_void_p
    cf.CFPropertyListCreateData.argtypes = [c_void_p, c_void_p, c_long, c_long, c_void_p]
    cf.CFPropertyListCreateWithData.restype = c_void_p
    cf.CFPropertyListCreateWithData.argtypes = [c_void_p, c_void_p, c_long, c_void_p, c_void_p]
    cf.CFRelease.argtypes = [c_void_p]
    sc.SCPreferencesCreate.restype, sc.SCPreferencesCreate.argtypes = c_void_p, [c_void_p, c_void_p, c_void_p]
    sc.SCPreferencesGetValue.restype, sc.SCPreferencesGetValue.argtypes = c_void_p, [c_void_p, c_void_p]
    sc.SCPreferencesSetValue.restype, sc.SCPreferencesSetValue.argtypes = c_bool, [c_void_p, c_void_p, c_void_p]
    for fn in ("SCPreferencesCommitChanges", "SCPreferencesApplyChanges", "SCPreferencesUnlock"):
        getattr(sc, fn).restype, getattr(sc, fn).argtypes = c_bool, [c_void_p]
    sc.SCPreferencesLock.restype, sc.SCPreferencesLock.argtypes = c_bool, [c_void_p, c_bool]
    sc.SCError.restype = c_int
    sc.SCErrorString.restype, sc.SCErrorString.argtypes = c_char_p, [c_int]
    _LIBS.update(cf=cf, sc=sc)
    return cf, sc

# Python str -> CoreFoundation string (the caller releases it).
def _cfstr(s):
    cf, _ = _libs()
    return cf.CFStringCreateWithCString(None, s.encode(), _UTF8)

# Last SystemConfiguration error as readable text.
def _sc_error():
    _, sc = _libs()
    code = sc.SCError()
    return "%s (%d)" % ((sc.SCErrorString(code) or b"?").decode(), code)

# CoreFoundation property list -> Python object (via XML).
def _cf_to_py(ref):
    cf, _ = _libs()
    data = cf.CFPropertyListCreateData(None, ref, _XML_FORMAT, 0, None)
    if not data:
        return None
    try:
        raw = ctypes.string_at(cf.CFDataGetBytePtr(data), cf.CFDataGetLength(data))
    finally:
        cf.CFRelease(data)
    return plistlib.loads(raw)

# Python object -> CoreFoundation property list (the caller releases it).
def _py_to_cf(obj):
    cf, _ = _libs()
    raw = plistlib.dumps(obj, fmt=plistlib.FMT_XML)
    data = cf.CFDataCreate(None, raw, len(raw))
    try:
        return cf.CFPropertyListCreateWithData(None, data, 0, None, None)
    finally:
        cf.CFRelease(data)

# Open a SystemConfiguration settings store (com.apple.nat.plist by default; the caller releases it).
def _open_prefs(prefs_id=PREFS_ID):
    cf, sc = _libs()
    name, pid = _cfstr("nx-spoof"), _cfstr(prefs_id)
    prefs = sc.SCPreferencesCreate(None, name, pid)
    cf.CFRelease(name)
    cf.CFRelease(pid)
    if not prefs:
        raise SharingError("cannot open %s: %s" % (prefs_id, _sc_error()))
    return prefs

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
def read_value(key_name, prefs_id=PREFS_ID):
    cf, sc = _libs()
    prefs = _open_prefs(prefs_id)
    key = _cfstr(key_name)
    try:
        ref = sc.SCPreferencesGetValue(prefs, key)      # borrowed reference
        return _cf_to_py(ref) if ref else {}
    finally:
        cf.CFRelease(key)
        cf.CFRelease(prefs)

# Set one top-level value of a SystemConfiguration settings store, commit and apply (macOS is told). Needs root.
def write_value(key_name, obj, prefs_id=PREFS_ID):
    cf, sc = _libs()
    prefs = _open_prefs(prefs_id)
    key = _cfstr(key_name)
    value = _py_to_cf(obj)
    try:
        if not sc.SCPreferencesLock(prefs, True):
            raise SharingError("lock failed: %s (run with sudo)" % _sc_error())
        try:
            if not sc.SCPreferencesSetValue(prefs, key, value):
                raise SharingError("set failed: %s" % _sc_error())
            if not sc.SCPreferencesCommitChanges(prefs):
                raise SharingError("commit failed: %s (run with sudo)" % _sc_error())
            if not sc.SCPreferencesApplyChanges(prefs):
                raise SharingError("apply failed: %s" % _sc_error())
        finally:
            sc.SCPreferencesUnlock(prefs)
    finally:
        cf.CFRelease(value)
        cf.CFRelease(key)
        cf.CFRelease(prefs)

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
