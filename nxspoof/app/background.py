"""
nxspoof.app.background - a run in the background (D28): only a Stop stops NXSPOOF (or the Mac sleeping, D32); windows only watch it.

  stop_command      nx_spoof.py stop: the run gets a stop request (a Stop's note, stop.json, and a signal to the process
                    named in run.json), exactly like the dashboard's ■ Stop: firewall off, summary, sharing off, cable out;
                    then it waits (up to STOP_WAIT seconds) until the run has let go of its lock. No password.
  follow_command    nx_spoof.py follow [--console casual|technical] [--no-color]: the newest run, live, read from its
                    records (logs/<run>.jsonl, never its text), in the Terminal style you pick; it ends when the run has
                    stopped. Ctrl+C, or closing the window, only stops watching: NXSPOOF keeps running.
  lines_for         one record as Terminal lines, the way output/terminal.py shows a live event (pure).
"""

import json
import os
import time

from nxspoof.consoles.registry import ConsoleRegistry
from nxspoof.core.events import PROBLEM_LEVELS
from nxspoof.features import runlog
from nxspoof.output import colors as c
from nxspoof.output import runfile
from nxspoof.output.logfile import records_path
from nxspoof.output.terminal import PROBLEM_HEADER
from nxspoof.settings.store import SettingsError, SettingsStore

POLL = 0.5                                   # seconds between two looks at the run's records while following
STOP_WAIT = 30                               # seconds nx_spoof.py stop waits for the run to have stopped

# nx_spoof.py stop: ask the run to stop, then wait until it has; returns the exit code. sleep: replaceable for tests.
def stop_command(system, places, out, sleep=time.sleep):
    if not runfile.held(places.runtime):
        out.write("NXSPOOF isn't running.\n")
        return 0
    runfile.ask_stop(places.runtime, "stop command")
    ask_to_stop(system, runfile.read(places.runtime).get("pid"))
    out.write("Stop requested: firewall off, summary, Internet Sharing off, virtual cable out…\n")
    for _ in range(int(STOP_WAIT / POLL)):
        if not runfile.held(places.runtime):
            out.write("NXSPOOF has stopped.\n")
            return 0
        sleep(POLL)
    out.write("NXSPOOF hasn't stopped after %d s: look at its log (nx_spoof.py follow).\n" % STOP_WAIT)
    return 1

# Signal the run too (its process id, from run.json): the note alone is enough, the signal is a backup (a run still starting,
# or one that isn't yours, --no-keeper, only gets the note).
def ask_to_stop(system, pid):
    if pid:
        try:
            system.stop_run(pid)
        except PermissionError:
            pass

# One record as Terminal lines in a style ("technical" / "casual"), with color codes; [] when it has nothing to show.
def lines_for(record, style, color_of=None):
    tech = runlog.line_of(record) if record.get("tech") is not None else None
    if style == "technical":
        if tech is None:
            return []
        stamp = c.paint("[%s]" % record.get("t", ""), "gray")
        return (["%s %s" % (stamp, c.render(tech, record.get("marks", []), color_of, tag=True))] +
                ["%s %s" % (stamp, c.colorize("      " + d)) for d in record.get("details", [])])
    problem = record.get("level") in PROBLEM_LEVELS
    out = []
    if record.get("casual"):
        out.append(c.render(record["casual"], record.get("casual_marks", []), color_of))
    elif problem and tech is not None:
        out += [PROBLEM_HEADER, "   " + c.paint(c.strip(tech).strip(), "gray")]
    if problem:                              # its details, except those the friendly text already says (setup's hints are in both)
        out += ["   " + c.paint(d, "gray") for d in record.get("details", []) if d.strip() not in (record.get("casual") or "")]
    return out

# The records file of the newest run in the logs folder, or None (no run yet, or one from before the records).
def newest_records(logs):
    latest = runlog.latest_run(logs)
    if not latest:
        return None
    path = records_path(os.path.join(logs, latest["name"]))
    return path if os.path.isfile(path) else None

# nx_spoof.py follow, from its options: the style (--console, technical by default) and colors (off with --no-color, and
# in a file or a pipe); returns the exit code.
def follow_args(args, places, out):
    store = SettingsStore(places.settings_file())
    try:
        store.load()
    except (SettingsError, OSError):
        pass                                 # the defaults then: following needs nothing from the file
    style = args.console or "technical"
    colors = args.no_color is None
    colors = colors and bool(getattr(out, "isatty", lambda: False)())     # no color codes in a file or a pipe
    return follow_command(places, store, style, colors, out)

# nx_spoof.py follow: show the newest run live until it has stopped (or Ctrl+C); returns the exit code.
# sleep and once (one look, then return) can be replaced for tests.
def follow_command(places, store, style, colors, out, sleep=time.sleep, once=False):
    path = newest_records(places.logs_folder())
    if path is None:
        out.write("No run to follow yet (or only runs from before the records, Nov 15).\n")
        return 0
    color_of = ConsoleRegistry(store).color_of
    out.write("Following %s (%s). Ctrl+C or closing this window only stops watching: NXSPOOF keeps running.\n" % (
        os.path.splitext(os.path.basename(path))[0], style))
    pos, rest = 0, ""
    try:
        while True:
            with open(path, encoding="utf-8") as f:
                f.seek(pos)
                rest += f.read()
                pos = f.tell()
            *complete, rest = rest.split("\n")
            for line in (l for l in complete if l.strip()):
                record = json.loads(line)
                record.setdefault("t", time.strftime("%H:%M:%S", time.localtime(record.get("ts", 0))))
                for shown in lines_for(record, style, color_of):
                    out.write((shown if colors else c.strip(shown)) + "\n")
                if record.get("kind") == "run.stopped":
                    out.write("(this run has stopped)\n")
                    return 0
            out.flush()
            if once:
                return 0
            sleep(POLL)
    except KeyboardInterrupt:
        out.write("\n(stopped watching; NXSPOOF keeps running)\n")
        return 0
