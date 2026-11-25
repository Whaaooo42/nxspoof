"""
nxspoof.macos.machine - what kind of Mac this is: macOS version and build, model, chip, Python, language, managed or not.

For the run's "this Mac" line (worded by core/fmt.py mac_line) and the problem report (features/report.py): when
NXSPOOF meets a Mac it was never tried on, these are the first things to know. Nothing personal: no Mac name, no user
name, no serial number. Each fact that can't be read is "?". Never logs.
"""

import os
import platform
import plistlib
import sys

from nxspoof.macos import shell, user

FACTS = ("macos", "build", "model", "chip", "arch", "rosetta", "python", "python_path", "language", "managed")

# The first line of a command's output, or "?" when it fails.
def _first_line(cmd, run=shell.run):
    code, out = run(cmd)
    lines = (out or "").strip().splitlines()
    return lines[0].strip() if code == 0 and lines else "?"

# The real user's language and region, from their own preferences (the run may be root, whose preferences say nothing).
def _language(home=None):
    try:
        with open(os.path.join(home or user.home(), "Library", "Preferences", ".GlobalPreferences.plist"), "rb") as f:
            prefs = plistlib.load(f)
    except Exception:
        return "?"
    languages = prefs.get("AppleLanguages") or []
    first = languages[0] if languages else "?"
    locale = prefs.get("AppleLocale")
    return "%s (region %s)" % (first, locale) if locale else first

# "yes" when an organization manages this Mac (it can lock Internet Sharing), "no", or "?" (macOS's profiles tool).
def _managed(run=shell.run):
    code, out = run(["/usr/bin/profiles", "status", "-type", "enrollment"])
    if code != 0:
        return "?"
    for line in out.splitlines():
        if line.strip().lower().startswith("mdm enrollment:"):
            return "no" if line.split(":", 1)[1].strip().lower().startswith("no") else "yes"
    return "?"

# Every fact, as a dict with the keys in FACTS (strings). run: the command runner (tests pass a fake one).
def facts(run=shell.run):
    translated = _first_line(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], run)
    return {
        "macos": _first_line(["/usr/bin/sw_vers", "-productVersion"], run),
        "build": _first_line(["/usr/bin/sw_vers", "-buildVersion"], run),
        "model": _first_line(["/usr/sbin/sysctl", "-n", "hw.model"], run),
        "chip": _first_line(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], run),
        "arch": platform.machine() or "?",
        "rosetta": {"1": "yes", "0": "no"}.get(translated, "no"),
        "python": platform.python_version(),
        "python_path": sys.executable or "?",
        "language": _language(),
        "managed": _managed(run),
    }

# What's personal on this Mac, for the problem report to replace: {word: stand-in} with the Mac's names (as macOS's
# Sharing settings show them), your account and full name, and your home folder. Words that can't be read are left out.
def personal_words(run=shell.run):
    words = {}
    for kind in ("ComputerName", "LocalHostName"):
        name = _first_line(["/usr/sbin/scutil", "--get", kind], run)
        if name != "?":
            words[name] = "<this Mac>"
    account, full = user.account_names()
    words[user.home()] = "~"
    for name in (account, full):
        if name:
            words[name] = "<you>"
    return words
