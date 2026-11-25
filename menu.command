#!/bin/bash
# Double-click to open the NXSPOOF menu: everything from the Terminal (start, follow, stop, this Mac, tests, docs...).
# The usual way in is NXSPOOF.app (the dashboard); this menu is the Terminal-only way, always there.
printf '\033]0;NXSPOOF menu\007'
printf '\033[8;32;100t'                      # the window: 32 rows, 100 columns (the menu cuts what's wider, never wraps)
. "$(dirname "$0")/scripts/need_python.sh"
need_python
exec /usr/bin/python3 -B "$(dirname "$0")/tools/menu.py"
