"""
nxspoof.app.password - one nx_spoof.py command as root through macOS's own password window, for the dashboard: ▶ Start
(app/starter.py: nx_spoof.py start) and This Mac's tasks and 🧹 Clean up (app/tasks.py). The window answers when the
command has finished (a start finishes once the run has its answer; the run itself goes on in the background).

  1. the program copy in NXSPOOF's data folder is refreshed (output/datafolder.py: macOS keeps a program started through
     its password window out of Documents, Desktop, Downloads and iCloud Drive, so it never touches the nx-spoof folder)
  2. its output file is emptied (the data folder's logs/.page_start.txt or .page_task.txt)
  3. macOS's window, with NXSPOOF's sentence (macos/admin.py through app/system.py); the answer is waited for on a thread
Every step is a DASH line in the dashboard's log. The command words come from NXSPOOF's own code, never from the page.
"""

import threading

from nxspoof.core.controls import ControlError
from nxspoof.output.datafolder import copy_program
from nxspoof.output.logfile import start_output

PYTHON = "/usr/bin/python3"                  # Apple's Python, what NXSPOOF runs on
NOT_PERMITTED_HINT = " (macOS refused the command a folder: the Terminal way works around it)"

# Refresh the program copy, empty the output file `output_name`, and show the password window for nx_spoof.py `words`
# (it answers when the command has finished). Returns (the window's process, the output file's path); ControlError if the
# window can't be shown.
def ask(center, words, output_name, prompt):
    c = center
    try:
        program, copied, removed = copy_program(c.project_dir, c.places.runtime, give=c.system.give_to_user)
        output = start_output(c.places.logs, name=output_name)
    except OSError as e:
        raise ControlError("couldn't prepare NXSPOOF's data folder (%s) - try the Terminal way" % e)
    c.note("program copy ready: %s (%d file(s) copied, %d removed; the command runs from there, wherever the nx-spoof folder is)" % (
        program, copied, removed))
    try:
        handle = c.system.start_with_password(program, [PYTHON, "-B", "./nx_spoof.py"] + list(words), output, prompt)
    except OSError as e:
        raise ControlError("couldn't show macOS's password window (%s) - try the Terminal way" % e)
    return handle, output

# Wait for the window's answer on a thread, then call then(state, message): "started" (the command has finished),
# "cancelled" or "failed" (macOS's message).
def when_answered(center, handle, then):
    threading.Thread(target=lambda: then(*center.system.password_outcome(handle)), daemon=True).start()

# Say why the window started nothing: cancelled ("<name> cancelled: ..."), or macOS's refusal ("couldn't <doing>: ...").
# name: "start", "setup"...; doing: "start NXSPOOF", "set up this Mac"...
def say_not_started(center, name, doing, state, message):
    if state == "cancelled":
        center.note("%s cancelled: %s" % (name, message), casual="✋ %s cancelled." % name.capitalize())
        return
    hint = NOT_PERMITTED_HINT if "not permitted" in message.lower() else ""
    center.note("couldn't %s: macOS said: %s%s" % (doing, message, hint), casual="😕 Couldn't %s: %s" % (doing, message), level="warn")
