"""
nxspoof.consoles.registry - who each console is: name, model, color, FTP settings, from settings.json.

It reads the settings store live, so a console added or edited (dashboard, settings.json) is known
at once. Names and labels come marked as their console (core/text.py): outputs color exactly the
marked stretch, using color_of(who).
"""

from nxspoof.core import text
from nxspoof.core.fmt import norm_mac

UNKNOWN_NAME = "Unknown console"
UNKNOWN_COLOR = "yellow"

# Who is who, from the settings store.
class ConsoleRegistry(object):
    # store: a SettingsStore; mac_ip: the Mac's own hotspot address (labelled "Mac (self-test)");
    # overrides: {"ftp.open_filezilla": True, ...} from command-line options, for this run only.
    def __init__(self, store, mac_ip=None, overrides=None):
        self.store = store
        self.mac_ip = mac_ip
        self.overrides = dict(overrides or {})

    # The settings entry for a MAC (effective, checked values), or None if unknown.
    def entry(self, mac):
        return self.store.console(mac) if mac else None

    # True if the console is in settings.json.
    def known(self, mac):
        return self.entry(mac) is not None

    # Console name from settings.json, or "Unknown console", marked as that console (core/text.py).
    def name(self, mac):
        e = self.entry(mac)
        return text.name((e.get("name") or norm_mac(mac)) if e else UNKNOWN_NAME, norm_mac(mac) if mac else None)

    # Name color for outputs (yellow for unknown consoles).
    def color(self, mac):
        e = self.entry(mac)
        return e.get("color", "white") if e else UNKNOWN_COLOR

    # The color a marked reference is shown in: the console's color, gray for the Mac's self-test.
    def color_of(self, who):
        return "gray" if who == text.SELFTEST else self.color(who)

    # Label with the name marked: "Switch 2 (192.168.2.3)", "Switch 2 (48:f1:..)", "Mac (self-test)", or the bare IP.
    def label(self, mac=None, ip=None, with_mac=False):
        if ip and ip == self.mac_ip:
            return text.name("Mac (self-test)", text.SELFTEST)
        if not mac:
            return ip or "?"
        extra = [x for x in (ip, mac if with_mac else None) if x]
        return text.compose("%s (%s)", self.name(mac), ", ".join(extra)) if extra else self.name(mac)

    # The global ftp settings merged with this console's own overrides.
    def ftp_settings(self, mac):
        merged = dict(self.store.effective()["ftp"])
        merged.update((k.split(".", 1)[1], v) for k, v in self.overrides.items() if k.startswith("ftp."))
        merged.update((self.entry(mac) or {}).get("ftp") or {})
        return merged

