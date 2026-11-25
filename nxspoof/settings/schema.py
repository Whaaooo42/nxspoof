"""
nxspoof.settings.schema - the one description of every setting and every console field.

Everything that needs to know about settings reads this table: validation, the tidy save order,
the command-line options (app/cli.py), the SETTINGS log lines, and the dashboard's Configure
tab and the settings table in docs/reference.html (written from here by tools/docs.py). Adding a setting = adding one Field here.

  apply = "live"     takes effect immediately when changed while NXSPOOF runs
  apply = "restart"  saved, and takes effect at the next start
"""

# ##############################################################################################################################################################
# ##  FIELD DESCRIPTIONS                                                                                                                                      ##
# ##############################################################################################################################################################

COLORS = ("red", "green", "yellow", "blue", "magenta", "cyan", "white")
# The console models the dashboard offers (name stored in settings.json, details shown as a hint). A hand-written
# settings.json may say anything; the dashboard only writes these (or keeps a console's current text unchanged).
CONSOLE_MODELS = [
    ("Switch V1 (Erista, unpatched)", "HAC-001 · 2017-2018 · Tegra X1 (T210) · the RCM exploit works"),
    ("Switch V1 (Erista, patched)", "HAC-001 · 2018-2019 · Tegra X1 (T210) · RCM patched, modchip needed"),
    ("Switch V2 (Mariko)", "HAC-001(-01) · 2019+ · Tegra X1+ (T214) · longer battery · modchip needed"),
    ("Switch Lite (Mariko)", "HDH-001 · \"Hoag\" · 2019+ · Tegra X1+ (T214) · handheld only"),
    ("Switch OLED (Mariko)", "HEG-001 · \"Aula\" · 2021+ · Tegra X1+ (T214) · 7\" OLED, LAN port in the dock"),
    ("Switch 2", "BEE-001 · 2025+ · NVIDIA T239"),
]
MODEL_NAMES = [name for name, _ in CONSOLE_MODELS]
CONSOLE_STYLES = ("technical", "casual")

# One setting or console field: where it lives, its type and default, how it's checked and applied.
class Field(object):
    __slots__ = ("section", "key", "kind", "default", "help", "apply", "choices", "minimum", "maximum")

    # Describe a field; kind is one of bool, int, number, text, list, dict.
    def __init__(self, section, key, kind, default, help, apply="restart", choices=None, minimum=None, maximum=None):
        self.section, self.key, self.kind, self.default = section, key, kind, default
        self.help, self.apply, self.choices = help, apply, choices
        self.minimum, self.maximum = minimum, maximum

    # "section.key", the name used everywhere else (e.g. "ftp.port").
    @property
    def name(self):
        return "%s.%s" % (self.section, self.key) if self.section else self.key

    # Check one value; returns (value to use, problem text or None). A bad value falls back to the default.
    def check(self, value):
        if self.kind == "bool":
            ok = isinstance(value, bool)
        elif self.kind == "int":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif self.kind == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif self.kind == "text":
            ok = isinstance(value, str)
        elif self.kind == "list":
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif self.kind == "dict":
            ok = isinstance(value, dict)
        else:
            raise ValueError("unknown field kind %r" % self.kind)
        if not ok:
            return self.default, "%s should be %s, got %r (using %r)" % (self.name, KIND_WORDS[self.kind], value, self.default)
        if self.choices and value not in self.choices:
            return self.default, "%s must be one of %s, got %r (using %r)" % (
                self.name, ", ".join(self.choices), value, self.default)
        if self.minimum is not None and value < self.minimum:
            return self.default, "%s must be at least %s, got %r (using %r)" % (self.name, self.minimum, value, self.default)
        if self.maximum is not None and value > self.maximum:
            return self.default, "%s must be at most %s, got %r (using %r)" % (self.name, self.maximum, value, self.default)
        return value, None

KIND_WORDS = {"bool": "true or false", "int": "a whole number", "number": "a number", "text": "text",
              "list": "a list of texts", "dict": "a group of settings in { }"}

# ##############################################################################################################################################################
# ##  THE SETTINGS                                                                                                                                            ##
# ##############################################################################################################################################################

SECTIONS = ("bridge", "logs", "ftp")         # D30: what only a Terminal run needs is a --flag (app/config.py RUN_ONLY)
BRIDGE_ADDRESSES = ("172.16.0.1", "10.254.0.1", "192.168.254.1")     # private ranges for the bridge (macos/netservices.py)

FIELDS = [
    Field("bridge", "address", "text", "172.16.0.1", "Thunderbolt Bridge's fixed address (setup sets it; pick another if it overlaps your home network)",
          choices=BRIDGE_ADDRESSES),
    Field("logs", "keep_last", "int", 0, "Keep only the newest N run logs (0 = keep everything)", minimum=0),
    Field("ftp", "watch", "bool", True, "Watch each console for an FTP server"),
    Field("ftp", "port", "int", 5000, "FTP port to check", apply="live", minimum=1, maximum=65535),
    Field("ftp", "check_every_seconds", "number", 3, "How often to check while the FTP server is down", apply="live", minimum=1),
    Field("ftp", "recheck_when_up_seconds", "number", 30,
          "How often to check while it's up, to notice when it goes down (0 = check once and stop)", apply="live", minimum=0),
    Field("ftp", "open_filezilla", "bool", False, "Open FileZilla on the console when its FTP server comes up", apply="live"),
]

# ##############################################################################################################################################################
# ##  CONSOLE FIELDS                                                                                                                                          ##
# ##############################################################################################################################################################

CONSOLE_FIELDS = [
    Field("", "name", "text", "", "Shown in every log line and on the dashboard", apply="live"),
    Field("", "model", "text", "", "Which Switch it is (the dashboard offers CONSOLE_MODELS); shown at start", apply="live"),
    Field("", "mac", "text", "", "How the console is recognized (System Settings > Internet > System MAC Address)", apply="live"),
    Field("", "owner", "text", "", "For your reference, handy for friends' consoles", apply="live"),
    Field("", "color", "text", "white", "Name color in Terminal and on the dashboard", apply="live", choices=COLORS),
    Field("", "serial", "text", "", "For your reference", apply="live"),
    Field("", "system_version", "text", "", "For your reference", apply="live"),
    Field("", "ftp", "dict", None, "Per-console overrides of the ftp section, e.g. {\"port\": 5000}", apply="live"),
    Field("", "notes", "list", [], "Your notes, one text per line", apply="live"),
]

# ##############################################################################################################################################################
# ##  LOOKUPS                                                                                                                                                 ##
# ##############################################################################################################################################################

BY_NAME = {f.name: f for f in FIELDS}
CONSOLE_BY_KEY = {f.key: f for f in CONSOLE_FIELDS}

# The built-in defaults as a settings dict: {"bridge": {...}, "logs": {...}, "ftp": {...}, "consoles": []}.
def defaults():
    out = {section: {} for section in SECTIONS}
    for f in FIELDS:
        out[f.section][f.key] = f.default
    out["consoles"] = []
    return out

# The Field for "section.key" (KeyError if there's no such setting).
def field(name):
    return BY_NAME[name]
