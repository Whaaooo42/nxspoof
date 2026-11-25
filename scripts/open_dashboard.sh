#!/bin/bash
# scripts/open_dashboard.sh - what NXSPOOF.app runs: the dashboard (nx_spoof.py dashboard) in the background, with no
# Terminal window, from the nx-spoof folder this file is in; then it returns at once (the app quits, the dashboard
# stays until its page is closed). An already open dashboard is just shown again in the browser.
# Its own output goes nowhere: the dashboard writes its log in NXSPOOF's data folder (logs/).
cd "$(dirname "$0")/.." || exit 1
if ! /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  /usr/bin/osascript -e 'display alert "NXSPOOF needs Python 3.9 or newer" message "It comes with Apple’s command-line tools. Click Install in the window macOS shows next, wait until it is done, then open NXSPOOF again."' >/dev/null 2>&1
  xcode-select --install >/dev/null 2>&1
  exit 1
fi
nohup /usr/bin/python3 -B ./nx_spoof.py dashboard >/dev/null 2>&1 </dev/null &
exit 0
