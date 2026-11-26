"""
nxspoof.app.cli - the command-line options, as one table.

Each row says which flag it is, which setting it overrides for this run (if any), and its help.
argparse, the merged RunConfig (app/config.py) and the "from options" log line all read this
table, so a new option is one row. Options override settings.json for one run only.
"""

import argparse
import json

# ##############################################################################################################################################################
# ##  THE OPTIONS TABLE                                                                                                                                       ##
# ##############################################################################################################################################################

# One command-line option: flag, the setting it overrides (or None), what it sets, and its help.
class Option(object):
    __slots__ = ("flag", "setting", "const", "kind", "default", "choices", "help")

    # const: the value a plain flag sets (e.g. --no-firewall sets False); kind: int/text for valued options.
    def __init__(self, flag, help, setting=None, const=None, kind=None, default=None, choices=None):
        self.flag, self.setting, self.const, self.kind = flag, setting, const, kind
        self.default, self.choices, self.help = default, choices, help

    # The argparse destination name ("--no-firewall" -> "no_firewall").
    @property
    def dest(self):
        return self.flag.lstrip("-").replace("-", "_")

OPTIONS = [
    Option("--settings", "settings file (default: settings.json in NXSPOOF's data folder, ~/Library/Application Support/NXSPOOF)", kind="text"),
    Option("--console", "follow's style (technical by default); the log file is always technical", setting="display.console", kind="text",
           choices=("technical", "casual")),
    Option("--no-color", "plain text in Terminal (follow; colors are off in a file or a pipe anyway)", setting="display.colors", const=False),
    Option("--no-firewall", "don't load the firewall (for comparison tests)", setting="spoof.firewall", const=False),
    Option("--manual-sharing", "you turn Internet Sharing on and off yourself", setting="spoof.auto_sharing", const=False),
    Option("--keep-sharing", "leave Internet Sharing ON when NXSPOOF stops", setting="spoof.keep_sharing", const=True),
    Option("--no-ftp-watch", "don't watch consoles for FTP servers", setting="ftp.watch", const=False),
    Option("--open-filezilla", "open FileZilla when a console's FTP server comes up", setting="ftp.open_filezilla", const=True),
    Option("--ip", "testing: address to listen on (default: find the Internet Sharing network)", kind="text"),
    Option("--answer-ip", "testing: address given for the connection-test names (default: --ip)", kind="text"),
    Option("--dns-port", "testing: DNS port", kind="int", default=53),
    Option("--http-port", "testing: web port for the fake connection test", kind="int", default=80),
    Option("--probe", "tinkering: these names (comma-separated) get the Mac's address instead of BLOCKED, to see what a "
           "console then asks; their web requests are still refused, and logged in full", kind="text"),
    Option("--anyway", "start: even when setup's checks say this Mac isn't ready (the dashboard's Start anyway)", const=True),
    Option("--fix", "start: setup's fixes first when this Mac isn't ready, then the start (the dashboard's Fix and start)", const=True),
    Option("--allow-sleep", "start: let this Mac sleep during the run (by default a run keeps it awake: the hotspot sleeps with it)", const=True),
    Option("--no-keeper", "keep the whole run root in one process (no privilege separation; for comparison and debugging)", const=True),
    Option("--yes", "setup, uninstall: no questions, the answer is yes (the dashboard's This Mac buttons use it)", const=True),
    Option("--only", "setup: fix only these steps, e.g. --only bridge,cable (the others are checked, not fixed)", kind="text"),
    Option("--skip", "setup: never fix these steps, e.g. --skip cable (they're checked and reported)", kind="text"),
    Option("--to", "uninstall: what Undo puts back: before (this Mac as it was before NXSPOOF changed it; the default "
           "when NXSPOOF has a record) or defaults (macOS's own)", kind="text", choices=("before", "defaults")),
    Option("--task", "set by the dashboard: a ticket the command writes into its records, so the dashboard follows it", kind="text"),
    Option("--full", "report: add the whole technical log of the newest run (folded)", const=True),
    Option("--no-open", "report: don't open GitHub's new issue page, only print its address", const=True),
]

BY_FLAG = {o.flag: o for o in OPTIONS}
STAGES = ("worker", "run")                   # the start's stages, each a brand-new process (app/start.py, D33)

# The commands (the first word; none = start). Each is (name, what it does).
COMMANDS = [
    ("start", "start NXSPOOF in the background, then say whether it runs (the default; needs sudo); only a Stop (or the Mac sleeping) stops it"),
    ("dashboard", "the dashboard on its own, no admin rights: settings, consoles, past runs, this Mac, Start"),
    ("check", "quick self-test of the DNS and web answers, no admin rights needed"),
    ("cleanup", "after a crash: remove leftover firewall rules and turn Internet Sharing off, with its own log (needs sudo)"),
    ("sharing", "Internet Sharing: sharing status | sudo ... sharing on | off"),
    ("link", "the virtual cable: link status | sudo ... link up | down"),
    ("setup", "guided setup of this Mac: checks, fixes what's missing (asks first), explains the rest"),
    ("doctor", "the setup checks only, nothing changed: ready or not, and why"),
    ("uninstall", "undo what setup did (your files stay)"),
    ("settings", "settings export <file.json> | settings import <file.json>: your settings and consoles to a file, or from one (checked first, the old ones kept)"),
    ("running", "is a run active right now? says yes / no; exit 0 = yes (the dashboard and other commands don't count)"),
    ("stop", "stop the running NXSPOOF, like the dashboard's Stop (no password: the run is you)"),
    ("follow", "the newest run, live, in the Terminal style you pick (--console, --no-color); Ctrl+C only stops watching"),
    ("report", "🐞 a problem report to paste into a GitHub issue: this Mac, setup, the last runs' problems, names replaced; "
               "printed, saved in logs/reports/, copied, and GitHub's new issue page opened (--full, --no-open)"),
]

# ##############################################################################################################################################################
# ##  PARSING                                                                                                                                                 ##
# ##############################################################################################################################################################

# argparse built from the table (unset options stay None, so settings.json keeps its say).
def build_parser():
    ap = argparse.ArgumentParser(prog="nx_spoof.py", description="NXSPOOF: fake internet for Switch consoles, from your Mac.",
                                 epilog="commands: " + "; ".join("%s = %s" % c for c in COMMANDS))
    ap.add_argument("command", nargs="?", default="start", choices=[name for name, _ in COMMANDS], help="what to do (default: start)")
    ap.add_argument("words", nargs="*", help="for sharing and link: on/off/up/down/status; for settings: export|import <file.json>")
    for o in OPTIONS:
        if o.kind is None:
            ap.add_argument(o.flag, dest=o.dest, action="store_const", const=o.const, default=None, help=o.help)
        else:
            ap.add_argument(o.flag, dest=o.dest, type=int if o.kind == "int" else str, choices=o.choices,
                            default=o.default, metavar=o.dest.upper() if not o.choices else None, help=o.help)
    return ap

# A started stage's words (app/start.py: "--stage worker|run <JSON spec>" before the command's own words, never typed by
# anyone and never in --help) -> (stage or None, spec or None, the command's own words).
def split_stage(argv):
    argv = list(argv)
    if argv[:1] == ["--stage"] and len(argv) >= 3 and argv[1] in STAGES:
        return argv[1], json.loads(argv[2]), argv[3:]
    return None, None, argv

# Parse a list of arguments (sys.argv[1:] style) into an argparse namespace.
def parse_args(argv):
    args = build_parser().parse_args(argv)
    args.argv = list(argv)                   # the words as given (start runs them again from the program copy)
    return args

# The options given on the command line, as typed ("--no-firewall", "--dashboard-port 8801"), leaving out `keep`:
# e.g. what a command that takes no run options ignores.
def given_options(args, keep=()):
    out = []
    for o in OPTIONS:
        value = getattr(args, o.dest, None)
        if o.flag not in keep and value is not None and value != o.default:
            out.append(o.flag if o.kind is None else "%s %s" % (o.flag, value))
    return out
