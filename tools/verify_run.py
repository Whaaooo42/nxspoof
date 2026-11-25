#!/usr/bin/env python3
"""
verify_run.py - the real-run checklist (docs/real-runs.html), checked from a run's log and the Mac's state.

  python3 -B tools/verify_run.py              # the latest run (last_run.log) + the Mac right now
  python3 -B tools/verify_run.py LOGFILE      # another run's log
  python3 -B tools/verify_run.py --all        # which stop paths the new code's runs have covered so far
  sudo python3 -B tools/verify_run.py         # also checks that no firewall rules are left (needs root)

Each item is ✅ passed, ❌ failed (with the lines that show why) or ⏭️ not tried in this run (e.g. no
FTP server was started). Only reads: changes nothing. The checks live in nxspoof/features/runlog.py, shared
with the dashboard's run picker (the log pane).
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from nxspoof.features.runlog import STOP_PATHS, Item, check_log, check_run, came_back, new_code_runs, plain_lines, stop_path  # noqa: E402,F401

# ##############################################################################################################################################################
# ##  THE MAC RIGHT NOW AND ALL RUNS                                                                                                                          ##
# ##############################################################################################################################################################

# The Mac after the run: NXSPOOF not running, hotspot off, no firewall rules left (root only) -> [Item].
def check_mac(is_root=None):
    from nxspoof.macos import firewall, netif, user
    from nxspoof.output import runfile
    items = []
    running = runfile.held(user.data_folder())                                          # the lock (output/runfile.py): the dashboard alone never holds it
    items.append(Item("skip" if running else "pass", "NXSPOOF isn't running any more", ["it's still running: check again after stopping it"] if running else []))
    hotspot = netif.current_sharing_interface()
    items.append(Item("fail" if hotspot and not running else "pass", "the hotspot is off (no bridge1xx interface)",
                      ["%s is still up with %s" % hotspot] if hotspot else []))
    if (os.geteuid() == 0) if is_root is None else is_root:
        left = firewall.leftovers()
        items.append(Item("fail" if left else "pass", "no NXSPOOF firewall rules left", ["rules still loaded: sudo python3 nx_spoof.py cleanup"] if left else []))
    else:
        items.append(Item("skip", "no NXSPOOF firewall rules left", ["needs admin rights to check: sudo python3 -B tools/verify_run.py (or menu 6 → 1)"]))
    return items

ICONS = {"pass": "✅", "fail": "❌", "skip": "⏭️ "}

# Print items; returns the number of failures.
def report(title, items, out):
    out.write("\n%s\n" % title)
    for it in items:
        out.write("  %s %s\n" % (ICONS[it.status], it.title))
        for e in it.evidence:
            out.write("       %s\n" % e)
    return sum(1 for it in items if it.status == "fail")

# Command line: check the latest (or a given) run and the Mac; or list the stop paths covered.
def main(argv=None):
    from nxspoof.core.paths import display_path
    ap = argparse.ArgumentParser(description="Check a real NXSPOOF run against the checklist (docs/real-runs.html).")
    from nxspoof.app.system import System
    places = System().places(ROOT)
    ap.add_argument("log", nargs="?", default=os.path.join(places.logs, "last_run.log"),
                    help="the run's log (default: last_run.log in NXSPOOF's data folder)")
    ap.add_argument("--all", action="store_true", help="which stop paths the new code's runs have covered so far")
    args = ap.parse_args(argv)
    out = sys.stdout
    if args.all:
        runs = new_code_runs(places.logs_folder())
        out.write("New-code runs in logs/: %d\n" % len(runs))
        for name, how in runs:
            out.write("  %s  %s\n" % (name, STOP_PATHS.get(how, how) if how else "did not stop cleanly"))
        covered = {how for _, how in runs}
        needed = ["ctrl-c", "SIGHUP", "dashboard"]
        for how in needed:
            out.write("  %s %s\n" % (ICONS["pass" if how in covered else "skip"], STOP_PATHS[how]))
        return 0 if all(how in covered for how in needed) else 1
    if not os.path.isfile(args.log):
        out.write("can't read %s: no such run log\n" % args.log)
        return 1
    failures = report("Run: %s" % display_path(os.path.realpath(args.log)), check_run(args.log), out)      # its .jsonl twin when there is one
    failures += report("This Mac now:", check_mac(), out)
    out.write("\n%s\n" % ("All good." if not failures else "%d item(s) failed - the lines above show why." % failures))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
