"""
nxspoof.core.fmt - small formatting helpers shared by every output: durations, plurals, MAC
addresses, errors in words and the ready-to-paste entry for a new console. One copy of each, used everywhere.
"""

import json
import traceback

EXPECTED_ERRORS = (OSError, RuntimeError)   # what macos/ raises, with macOS's own words: their message is the story

# Normalize a MAC address: 'A:1:EB:...' (macOS's DHCP file drops leading zeros) -> '0a:01:eb:...'.
def norm_mac(mac):
    parts = (mac or "").strip().lower().replace("-", ":").split(":")
    if len(parts) != 6:
        return (mac or "").strip().lower()
    return ":".join(p.zfill(2) for p in parts)

# Compact duration for technical lines: '45s', '3m 42s', '1h 05m 00s' (rounded, like casual_duration).
def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return "%dh %02dm %02ds" % (h, m, sec)
    if m:
        return "%dm %02ds" % (m, sec)
    return "%ds" % sec

# Duration in plain words for casual lines: '21 seconds', '1 minute', '3 min 42 s', '1 h 5 min'.
def casual_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "%d second%s" % (seconds, "" if seconds == 1 else "s")
    if seconds < 3600:
        m, sec = divmod(seconds, 60)
        return ("%d minute%s" % (m, "" if m == 1 else "s")) if sec == 0 else "%d min %d s" % (m, sec)
    h, rest = divmod(seconds, 3600)
    return "%d h %d min" % (h, rest // 60)

# '1 lookup' / '5 lookups' (an explicit plural can be given for irregular words).
def plural(n, word, many=None):
    return "%d %s" % (n, word if n == 1 else (many or word + "s"))

# The settings.json entry suggested for a console that isn't known yet.
def console_snippet(mac):
    return {"name": "Friend's Switch", "model": "", "mac": norm_mac(mac), "owner": "", "color": "yellow",
            "ftp": {"port": 5000}, "notes": []}

# The same entry as one line of JSON, ready to paste.
def console_snippet_json(mac):
    return json.dumps(console_snippet(mac))

# An error in words for a log line: its kind, then its message ("KeyError: 'name'", "PermissionError: [Errno 13] ...").
def error_text(e):
    text = str(e)
    return "%s: %s" % (type(e).__name__, text) if text else type(e).__name__

# The traceback of an unexpected error, as lines for a log line's details; None for EXPECTED_ERRORS (unless the keeper
# sent one along: remote_trace, or always: a bug whatever its kind). An unexpected error on someone else's Mac is how a
# different macOS shows up.
def error_trace(e, always=False):
    remote = getattr(e, "remote_trace", None)
    if remote:
        return list(remote)
    if (isinstance(e, EXPECTED_ERRORS) and not always) or e.__traceback__ is None:
        return None
    return "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip().splitlines()

# What kind of Mac this is, in one line (the facts of macos/machine.py): "macOS 15.7.7 (24G720), model Mac16,13, Apple M4,
# arm64, Python 3.9.6 (/usr/bin/python3), language en-CA (region en_CA), managed by an organization: no". The model is
# Apple's identifier, comma included (hence its label).
def mac_line(f):
    return "macOS %s (%s), model %s, %s, %s%s, Python %s (%s), language %s, managed by an organization: %s" % (
        f.get("macos", "?"), f.get("build", "?"), f.get("model", "?"), f.get("chip", "?"), f.get("arch", "?"),
        " under Rosetta" if f.get("rosetta") == "yes" else "", f.get("python", "?"), f.get("python_path", "?"),
        f.get("language", "?"), f.get("managed", "?"))
