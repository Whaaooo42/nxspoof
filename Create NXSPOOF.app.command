#!/bin/bash
# Double-click to build NXSPOOF.app (the dashboard's double-click door) in this folder from scripts/NXSPOOF.applescript,
# with its icon (scripts/NXSPOOF.icns, drawn by tools/icon.py), then open it. It's built on your Mac, never shipped: osacompile
# signs it for this Mac, and it's signed again (for this Mac only) once the icon is in. Double-click again to rebuild it.
printf '\033]0;Create NXSPOOF.app\007'
cd "$(dirname "$0")" || exit 1
if [ -d NXSPOOF.app ]; then
  echo "Rebuilding NXSPOOF.app from scripts/NXSPOOF.applescript…"
else
  echo "Building NXSPOOF.app (the dashboard's double-click door) from scripts/NXSPOOF.applescript…"
fi
if /usr/bin/osacompile -o NXSPOOF.app scripts/NXSPOOF.applescript; then
  if cp scripts/NXSPOOF.icns NXSPOOF.app/Contents/Resources/applet.icns && /usr/bin/codesign --force --sign - NXSPOOF.app 2>/dev/null; then
    touch NXSPOOF.app                      # Finder and the Dock show the new icon
  else
    echo "⚠️  The icon couldn't be put in: NXSPOOF.app keeps macOS's script icon (it works all the same)."
    /usr/bin/osacompile -o NXSPOOF.app scripts/NXSPOOF.applescript
  fi
  echo "✅ NXSPOOF.app is ready in the nx-spoof folder. Opening it: the dashboard opens in your browser."
  echo "   The first time (and after a rebuild), macOS asks whether NXSPOOF may use this folder: click Allow."
  open NXSPOOF.app
  echo
  echo "Done: you can close this window. From now on, double-click NXSPOOF.app."
else
  echo "❌ Couldn't build NXSPOOF.app (osacompile's message is above). Everything still works from menu.command."
  exit 1
fi
