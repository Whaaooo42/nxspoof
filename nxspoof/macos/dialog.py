"""
nxspoof.macos.dialog - a small macOS dialog with buttons (osascript's "display dialog"), for the rare times NXSPOOF has
to tell you something while no page is open (D27: the dashboard closed itself while a run goes on). Never logs.

  ask(text, buttons, default, title, wait)   show it as you; returns the button pressed, or None (closed, or no answer
                                             within `wait` seconds)
The text, the buttons and the title reach AppleScript as arguments, never pasted into the script.
"""

from nxspoof.macos import shell

OSASCRIPT = "/usr/bin/osascript"
SCRIPT = ("on run argv\n"                     # argv: text, title, the buttons..., the default button
          "  set theButtons to items 3 thru -2 of argv\n"
          "  set answer to display dialog (item 1 of argv) with title (item 2 of argv) buttons theButtons "
          "default button (item -1 of argv) giving up after %d\n"
          "  if gave up of answer then return \"\"\n"
          "  return button returned of answer\n"
          "end run")

# The osascript command for a dialog (pure): the script, then its arguments.
def command(text, buttons, default, title="NXSPOOF", wait=120):
    return [OSASCRIPT, "-e", SCRIPT % int(wait), str(text), str(title)] + [str(b) for b in buttons] + [str(default)]

# Show the dialog and wait for it; returns the button pressed, or None.
def ask(text, buttons, default, title="NXSPOOF", wait=120, run=shell.run):
    code, out = run(command(text, buttons, default, title, wait))
    answer = out.strip() if code == 0 else ""
    return answer if answer in buttons else None
