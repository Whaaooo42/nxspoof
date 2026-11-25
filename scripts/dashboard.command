#!/bin/bash
# Double-click to open the dashboard with a Terminal window beside it (the fallback: NXSPOOF.app is the usual way in,
# with no Terminal window; menu 5 uses this too when there's no NXSPOOF.app yet). No password needed. It closes with its
# page, Ctrl+C, or this window; a run it shows goes on either way.
# Prefer the Terminal for everything? Double-click menu.command instead.
cd "$(dirname "$0")/.." || exit 1
printf '\033]0;NXSPOOF dashboard\007'
. scripts/need_python.sh
need_python
exec /usr/bin/python3 -B ./nx_spoof.py dashboard "$@"
