#!/bin/bash
# scripts/need_python.sh - "is Apple's Python 3.9+ here?", sourced by every double-click file
# (menu.command, scripts/dashboard.command; NXSPOOF.app's open_dashboard.sh checks on its own, with an alert). Without Python nothing else can even open, so this check
# comes first: if it's missing, it offers Apple's command-line tools (which bring it) and waits for a key.

# Stop here (after explaining) when /usr/bin/python3 is missing or older than 3.9.
need_python() {
  if /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    return 0
  fi
  echo "🐍 Python 3.9 or newer is needed. It comes with Apple's command-line tools."
  echo "   A macOS window will offer to install them: click Install, wait, then double-click this file again."
  xcode-select --install 2>/dev/null
  echo
  read -n 1 -s -r -p "Press any key to close this window."
  echo
  exit 1
}
