#!/usr/bin/env python3
"""
nx_spoof.py - starts NXSPOOF (the nxspoof/ package). Double-click NXSPOOF.command (the dashboard) instead of running it by hand.

  sudo python3 -B nx_spoof.py                      # a run (scripts/start.sh does this, after a quick check)
  python3 -B nx_spoof.py check                     # quick self-test, no admin rights
  python3 -B nx_spoof.py doctor                    # is this Mac ready? nothing changed
  sudo python3 -B nx_spoof.py cleanup              # after a crash: firewall rules out, sharing off
  python3 -B nx_spoof.py --help                    # every option and command
"""

import os
import sys

sys.dont_write_bytecode = True                     # no root-owned __pycache__ folders in your project
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nxspoof.app.main import main  # noqa: E402  (after the path is set)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
