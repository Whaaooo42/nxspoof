"""
nxspoof.app.config - settings.json + command-line options -> one RunConfig for this run.

Every value remembers where it came from: "settings.json", "default" (not in the file, or a bad
value there), or the option that overrode it (e.g. "--no-firewall"). What only a run started by hand
needs is a --flag, never a setting (D30): RUN_ONLY below, its defaults. The SETTINGS log lines are
built here, keeping today's exact wording, plus a line naming any option overrides.
"""

from nxspoof.app import cli
from nxspoof.core.text import compose
from nxspoof.settings import schema

RUN_ONLY = {                                 # values a run has without a setting (D30): the dashboard never shows them; a --flag changes them
    "spoof.firewall": True,                  # --no-firewall
    "spoof.auto_sharing": True,              # --manual-sharing
    "spoof.keep_sharing": False,             # --keep-sharing
    "display.console": "technical",          # --console casual (follow, and a run's own output file)
    "display.colors": True,                  # --no-color
}

# The settings in force for one run, with where each value came from.
class RunConfig(object):
    # Built by build_config(); values and sources are keyed by "section.key".
    def __init__(self, values, sources, extras):
        self.values = values
        self.sources = sources
        self.extras = extras

    # The value of one setting, e.g. get("spoof.firewall").
    def get(self, name):
        return self.values[name]

    # A testing/extra option (ip, answer_ip, dns_port, http_port, settings).
    def extra(self, name):
        return self.extras.get(name)

    # Where a value came from: "settings.json", "default" or the overriding option.
    def source(self, name):
        return self.sources[name]

    # How this run is stopped, for the READY line: (technical, casual). Every run is in the background (D30).
    def stop_hint(self):
        return ("Stop with the dashboard's Stop, menu 2 or nx_spoof.py stop.",
                "(Stop it with ■ Stop on the dashboard, or menu 2.)")

    # What the start found before the run (app/launch.py: setup's checks), as START lines: [(technical text, casual text or
    # None, level, data)]; nothing for a dry run (--ip), which needs nothing from the Mac.
    def preflight(self):
        if self.extra("ip"):
            return []
        if self.extra("anyway"):
            return [("started with --anyway: setup's checks were skipped (nx_spoof.py doctor shows them)",
                     "⚠️ Started without setup's checks (Start anyway).", "warn", {"doctor": "anyway"})]
        return [("setup's checks: this Mac is ready", None, "info", {"doctor": "ready"})]

    # The START line for the virtual cable the run plugged in itself: found "already" | "plugged", status: macos.cable.Status.
    @staticmethod
    def cable_line(found, status):
        if not status.ok:
            return ("virtual cable: plugged in, but macOS doesn't show Thunderbolt Bridge's fixed address yet - Internet Sharing may "
                    "not start (Set up this Mac fixes it)", "⚠️ The virtual cable is in, but this Mac isn't fully set up for it.",
                    "warn", {"cable": found, "address": False})
        text = "already plugged in" if found == "already" else "plugged in just now (taken out again when NXSPOOF stops)"
        return ("virtual cable: " + text, None, "info", {"cable": found})

    # The SETTINGS log lines (without the tag), in today's wording.
    def describe(self):
        g = self.get
        lines = ["console=%s (Terminal style; this file is always technical)" % g("display.console"),
                 "firewall=%s  auto_sharing=%s  keep_sharing=%s  ftp_watch=%s  open_filezilla=%s" % (
                     g("spoof.firewall"), g("spoof.auto_sharing"), g("spoof.keep_sharing"), g("ftp.watch"), g("ftp.open_filezilla"))]
        overridden = ["%s=%s (%s)" % (n, self.values[n], s) for n, s in sorted(self.sources.items()) if s.startswith("--")]
        if overridden:
            lines.append("from options: " + "  ".join(overridden))
        return lines

    # The "console <name>  <mac>  <model>" SETTINGS lines (without the tag), the names marked: [(text, mac)].
    @staticmethod
    def console_lines(store, registry):
        return [(compose("console %s  %s  %s", registry.name(c["mac"]), c["mac"], c.get("model", "")), c["mac"]) for c in store.consoles()]

# Merge the store's effective settings with the parsed options (options win, for this run only).
def build_config(store, args):
    doc = store.document()
    values, sources = {}, {}
    for f in schema.FIELDS:
        values[f.name] = store.get(f.name)
        in_file = store.loaded_from_file and isinstance(doc.get(f.section), dict) and f.key in doc[f.section]
        valid = in_file and f.check(doc[f.section][f.key])[1] is None
        sources[f.name] = "settings.json" if valid else "default"
    values.update(RUN_ONLY)
    sources.update((name, "default") for name in RUN_ONLY)
    for o in cli.OPTIONS:
        given = getattr(args, o.dest, None)
        if o.setting and given is not None:
            good, problem = (given, None) if o.setting in RUN_ONLY else schema.field(o.setting).check(given)
            if problem:
                raise ValueError("%s: %s" % (o.flag, problem))
            values[o.setting] = good
            sources[o.setting] = o.flag
    extras = {o.dest: getattr(args, o.dest, None) for o in cli.OPTIONS if not o.setting}
    return RunConfig(values, sources, extras)
