"""
nxspoof.macos.hotspot - the hotspot's name, channel and security, where macOS 15 really keeps them (found by a probe on
2026-11-17; docs/how-its-built.html, the platform contract). Read only: the hotspot is macOS's (D25, revised 2026-11-17).

  name, channel, security   /Library/Preferences/SystemConfiguration/com.apple.airport.preferences.plist, section
                            InternetSharing: SSID (bytes), Channel, SecurityType. Readable by anyone.
  password                  the System keychain: the generic password "Internet-sharing password" (account AirPort).
                            NXSPOOF only checks that it's there.
Why NXSPOOF never writes them (Run H): the password's key is tied to the name, and System Settings saves both together;
writing only the name left a network no Switch could join. Name, channel and password are changed in System Settings >
General > Sharing > (i) next to Internet Sharing > Wi-Fi Options.
  (com.apple.nat.plist's NAT/AirPort section is a stale leftover System Settings no longer updates: never used here.)

Never logs.

  read()                        {"name", "channel", "security"} now (None if the section is missing or can't be read)
  password_set(run)             True if the hotspot has a password (the keychain entry exists; it isn't read)
"""

from nxspoof.macos import sharing, shell

PREFS_ID = "com.apple.airport.preferences.plist"       # in /Library/Preferences/SystemConfiguration
SECTION = "InternetSharing"
KEYCHAIN = "/Library/Keychains/System.keychain"
PASSWORD_ITEM = ["-a", "AirPort", "-D", "Internet-sharing password"]       # the keychain entry System Settings makes
OPEN = ("None", "Open")                      # SecurityType values of a network with no password at all

# The hotspot's settings from a parsed preferences dict (pure): {"name", "channel", "security"}, or None without the section.
def from_prefs(prefs):
    section = (prefs or {}).get(SECTION)
    if not isinstance(section, dict):
        return None
    ssid = section.get("SSID")
    name = ssid.decode("utf-8", "replace") if isinstance(ssid, (bytes, bytearray)) else (ssid if isinstance(ssid, str) else None)
    return {"name": name, "channel": section.get("Channel"), "security": section.get("SecurityType")}

# The hotspot's settings now (None if they can't be read). Reading needs no root.
def read(read_value=None):
    try:
        return from_prefs({SECTION: (read_value or sharing.read_value)(SECTION, PREFS_ID)})
    except (OSError, sharing.SharingError):
        return None

# True if the hotspot has a password: the keychain entry exists (its secret is never read).
def password_set(run=shell.run):
    return run(["/usr/bin/security", "find-generic-password"] + PASSWORD_ITEM + [KEYCHAIN])[0] == 0
