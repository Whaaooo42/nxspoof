"""
nxspoof.macos.scprefs - macOS's SystemConfiguration settings (SCPreferences) read and written through ctypes, in a fresh
process of its own: NXSPOOF never calls these frameworks in a process that was forked (the background run, the keeper).
(They're loaded in every Python process at launch anyway; calling them after a fork without exec is what macOS forbids.)

macOS doesn't allow CoreFoundation and SystemConfiguration in a child process that was forked without exec: on a newer
macOS (27.3, Nov 25) the run crashed in SCPreferencesCreate the moment it read the hotspot's name ("crashed on child
side of fork pre-exec"). So macos/sharing.py runs this file as its own program, a brand-new Python, every time:

  python3 scprefs.py read  <settings id> <key>     the value, as an XML property list {"value": ...} on the output
  python3 scprefs.py write <settings id> <key>     the value from the input (the same XML), set, committed, applied (root)

It imports nothing from NXSPOOF (it runs as a plain file). A problem: its message on the error output, exit code 1.
"""

import ctypes
import plistlib
import sys
from ctypes import c_bool, c_char_p, c_int, c_long, c_uint32, c_void_p

_UTF8 = 0x08000100
_XML_FORMAT = 100
_LIBS = {}

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
def _open_prefs(prefs_id):
    cf, sc = _libs()
    name, pid = _cfstr("nx-spoof"), _cfstr(prefs_id)
    prefs = sc.SCPreferencesCreate(None, name, pid)
    cf.CFRelease(name)
    cf.CFRelease(pid)
    if not prefs:
        raise RuntimeError("cannot open %s: %s" % (prefs_id, _sc_error()))
    return prefs

# ##############################################################################################################################################################
# ##  READING AND WRITING ONE VALUE                                                                                                                           ##
# ##############################################################################################################################################################

# One top-level value of a SystemConfiguration settings store, as Python ({} when it isn't there). Reading needs no root.
def read_value(key_name, prefs_id):
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
def write_value(key_name, obj, prefs_id):
    cf, sc = _libs()
    prefs = _open_prefs(prefs_id)
    key = _cfstr(key_name)
    value = _py_to_cf(obj)
    try:
        if not sc.SCPreferencesLock(prefs, True):
            raise RuntimeError("lock failed: %s (run with sudo)" % _sc_error())
        try:
            if not sc.SCPreferencesSetValue(prefs, key, value):
                raise RuntimeError("set failed: %s" % _sc_error())
            if not sc.SCPreferencesCommitChanges(prefs):
                raise RuntimeError("commit failed: %s (run with sudo)" % _sc_error())
            if not sc.SCPreferencesApplyChanges(prefs):
                raise RuntimeError("apply failed: %s" % _sc_error())
        finally:
            sc.SCPreferencesUnlock(prefs)
    finally:
        cf.CFRelease(value)
        cf.CFRelease(key)
        cf.CFRelease(prefs)

# ##############################################################################################################################################################
# ##  COMMAND LINE (HOW NXSPOOF RUNS IT)                                                                                                                      ##
# ##############################################################################################################################################################

# python3 scprefs.py read|write <settings id> <key>: the value out (read) or in (write), as an XML property list.
def main(argv):
    if len(argv) != 3 or argv[0] not in ("read", "write"):
        sys.stderr.write("usage: python3 scprefs.py read|write <settings id> <key>\n")
        return 2
    action, prefs_id, key_name = argv
    try:
        if action == "read":
            sys.stdout.write(plistlib.dumps({"value": read_value(key_name, prefs_id)}, fmt=plistlib.FMT_XML).decode())
        else:
            write_value(key_name, plistlib.loads(sys.stdin.buffer.read())["value"], prefs_id)
    except Exception as e:
        sys.stderr.write("%s\n" % e)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
