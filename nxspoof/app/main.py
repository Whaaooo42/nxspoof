"""
nxspoof.app.main - the entry point: read the options, pick the command, run it.

  start (default) one NXSPOOF run, in the background (app/launch.py, app/run.py)
  dashboard       the control center: the dashboard on its own, no admin rights (app/center.py)
  check           quick self-test on this Mac (127.0.0.1, free ports), no admin rights
  cleanup         leftover firewall rules + Internet Sharing off, after a crash (sudo; setup/checklist.py, own log)
  sharing         Internet Sharing status / on / off
  link            the virtual cable status / up / down
  setup, doctor, uninstall          the guided setup (setup/checklist.py)
  report          🐞 a problem report for a GitHub issue (app/reporting.py)

Where files go is decided once, here, by role (core/places.py): the program (the nx-spoof folder) and the platform's
folders for settings, logs and runtime files (on a Mac, all NXSPOOF's data folder, ~/Library/Application Support/NXSPOOF:
output/datafolder.py says why), prepared here for every command that uses them. main() takes the System and the places
as parameters, so the simulator runs exactly this code with a fake Mac in a temporary folder.
"""

import sys

from nxspoof.app import background, cli
from nxspoof.app.commands import check_command, find_places, link_command, prepare_data, running_command
from nxspoof.app.transfer import settings_command
from nxspoof.app.launch import center_command
from nxspoof.app.start import start_command
from nxspoof.app.system import System
from nxspoof.core.paths import PROJECT_DIR
from nxspoof.macos import sharing

# Run a command line (sys.argv[1:] style); returns the exit code.
#   places: where NXSPOOF's files are (core/places.py); by default the platform's (System.places), or, given only a
#   project folder (tests, the simulator), that folder for every role.
def main(argv=None, system=None, project_dir=None, stream=None, places=None):
    args = cli.parse_args(sys.argv[1:] if argv is None else argv)
    system = system or System()
    places = places or find_places(system, project_dir, PROJECT_DIR)
    prepared = prepare_data(args.command, system, places)
    if prepared is None:
        return 1
    return dispatch(args, system, places, stream, prepared)

# nx_spoof.py report: with the settings (their names are replaced), or without them when they can't be read.
def report_args(args, system, places, out):
    from nxspoof.app.commands import load_store
    from nxspoof.app.reporting import report_command
    try:
        store, _ = load_store(args, places)
    except Exception:
        store = None
    return report_command(args, system, places, store, out)

# Run the command picked on the command line, with NXSPOOF's folders ready; returns the exit code.
def dispatch(args, system, places, stream, prepared):
    out = stream or sys.stdout
    if args.command == "start":
        return start_command(args, system, places, stream, prepared)
    if args.command == "dashboard":
        return center_command(args, system, places, stream, prepared)
    if args.command == "check":
        return check_command(args, places, out)
    if args.command == "sharing":
        return sharing.main(args.words)
    if args.command == "link":
        return link_command(args.words)
    if args.command == "settings":
        return settings_command(args, places, system, out)
    if args.command == "running":
        return running_command(places, out)
    if args.command == "stop":
        return background.stop_command(system, places, out)
    if args.command == "follow":
        return background.follow_args(args, places, out)
    if args.command == "report":
        return report_args(args, system, places, out)
    from nxspoof.setup import checklist
    return checklist.main(args.command, system=system, project_dir=places.program, stream=out, style=args.console or "casual",
                          places=places, args=args)
